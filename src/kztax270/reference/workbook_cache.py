"""Safe persistence primitives for mutable Excel reference workbooks."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pandas as pd


_THREAD_LOCK = threading.RLock()
_LOCK_STATE = threading.local()


def last_good_workbook_path(path: Path) -> Path:
    """Return the sibling copy retained before the most recent replacement."""

    return path.with_name(f"{path.stem}.last_good{path.suffix}")


def is_valid_excel_workbook(path: Path) -> bool:
    """Return whether *path* is a readable OOXML workbook container."""

    try:
        return path.is_file() and path.stat().st_size > 0 and zipfile.is_zipfile(path)
    except OSError:
        return False


def read_excel_checked(path: Path, *args: Any, **kwargs: Any) -> pd.DataFrame:
    """Read an Excel workbook only after checking its ZIP container."""

    if not is_valid_excel_workbook(path):
        raise zipfile.BadZipFile(f"Excel workbook is missing or invalid: {path}")
    return pd.read_excel(path, *args, **kwargs)


def write_excel_atomic(frame: pd.DataFrame, path: Path) -> None:
    """Write a complete workbook before replacing the live reference file.

    A valid predecessor is retained as ``*.last_good.xlsx``.  It makes a
    reference cache recoverable even if a host is interrupted between runs.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}-", suffix=".xlsx", delete=False)
    temporary_path = Path(handle.name)
    handle.close()
    try:
        frame.to_excel(temporary_path, index=False)
        if not is_valid_excel_workbook(temporary_path):
            raise zipfile.BadZipFile(f"New Excel workbook is invalid: {temporary_path}")
        if is_valid_excel_workbook(path):
            _copy_file_atomic(path, last_good_workbook_path(path))
        os.replace(temporary_path, path)
        if not is_valid_excel_workbook(last_good_workbook_path(path)):
            _copy_file_atomic(path, last_good_workbook_path(path))
    except PermissionError as exc:
        raise PermissionError(f"Cannot write {path}; close the workbook in Excel and retry.") from exc
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def restore_last_good_workbook(path: Path) -> bool:
    """Restore a valid retained predecessor, returning whether recovery succeeded."""

    backup = last_good_workbook_path(path)
    if not is_valid_excel_workbook(backup):
        return False
    _copy_file_atomic(backup, path)
    return True


@contextmanager
def reference_update_lock(data_dir: Path) -> Iterator[None]:
    """Serialize updates to shared reference workbooks across jobs and processes."""

    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / ".kase_aix_reference.lock"
    with _THREAD_LOCK:
        depth = getattr(_LOCK_STATE, "depth", 0)
        if depth:
            _LOCK_STATE.depth = depth + 1
            try:
                yield
            finally:
                _LOCK_STATE.depth -= 1
            return

        with _exclusive_file_lock(lock_path):
            _LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _LOCK_STATE.depth = 0


def _copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.stem}-",
        suffix=destination.suffix or ".xlsx",
        delete=False,
    )
    temporary_path = Path(handle.name)
    handle.close()
    try:
        shutil.copy2(source, temporary_path)
        if not is_valid_excel_workbook(temporary_path):
            raise zipfile.BadZipFile(f"Excel workbook copy is invalid: {source}")
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Use an OS-managed lock that is released automatically on process exit."""

    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
