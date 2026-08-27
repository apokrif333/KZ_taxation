FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QCM_JOB_ROOT=/tmp/qcm-tax-270

WORKDIR /app

RUN groupadd --gid 10001 qcm && \
    useradd --uid 10001 --gid qcm --create-home --shell /usr/sbin/nologin qcm

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m pip install --no-cache-dir ".[web]"

COPY --chown=qcm:qcm data/ ./data/
COPY --chown=qcm:qcm reference/ ./reference/

RUN mkdir -p /tmp/qcm-tax-270 data/raw data/processed data/output && \
    chown -R qcm:qcm /tmp/qcm-tax-270 data reference

USER qcm

CMD ["sh", "-c", "python -m uvicorn kztax270.webapi.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
