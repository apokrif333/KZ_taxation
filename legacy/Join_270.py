import datetime
import os
import copy
import pandas as pd
import numpy as np

from lxml import etree
from copy import deepcopy
from colorama import init, Fore, Style
from pprint import pprint
from collections import defaultdict

# Settings
pd.options.display.max_rows = 1_500
pd.options.display.max_columns = 500
pd.set_option('display.width', 1400)
pd.options.display.float_format = '{:.6f}'.format


def get_xmls(need_year: int, fio1: str, fio2: str, fio3: str) -> dict:
    data = {}
    for file in os.listdir('postProcessed'):
        if ((str(need_year) in file) and (f"{fio1}_{fio2}_{fio3}" in file) and ('merged' not in file) and
                ('final' not in file)):
            account = file.split('_')[2]
            data[account] = []

            tree = etree.parse(os.path.join('postProcessed', file))
            data[account].append(tree)
            data[account].append(tree.getroot())

    return data


def get_form(root: etree.Element, form_name: str) -> list:
    return [form for form in root.findall("form") if form.get("name") == form_name]


def get_field_dict(sheet: etree.Element) -> dict:
    return {f.get("name"): f.text for f in sheet.findall("field")}


def merge_form_270_00(form1: etree.Element, form2: etree.Element) -> etree.Element:
    sheet1 = form1.find(".//sheet[@name='page_270_00_01']")
    sheet2 = form2.find(".//sheet[@name='page_270_00_01']")
    merged = copy.deepcopy(form1)

    field_dict_1 = get_field_dict(sheet1)
    field_dict_2 = get_field_dict(sheet2)

    special_fields = ["fio1", 'fio2', 'fio3', "iin", "page_number"]
    for field in special_fields:
        if field_dict_1.get(field) != field_dict_2.get(field):
            raise ValueError(f"Field '{field}' differs between files: {field_dict_1.get(field)} vs "
                             f"{field_dict_2.get(field)}")

    for i in range(1, 8):
        field = f"pril_{i}"
        val1 = field_dict_1.get(field) == "true"
        val2 = field_dict_2.get(field) == "true"
        merged_value = "true" if val1 or val2 else "false"
        field_el = merged.find(f".//field[@name='{field}']")
        field_el.text = merged_value

    return merged


def merge_form_270_01(form1: etree.Element, form2: etree.Element) -> etree.Element:
    merged = copy.deepcopy(form1)

    fields1 = {f.get("name"): f for f in form1.findall(".//field")}
    fields2 = {f.get("name"): f for f in form2.findall(".//field")}
    all_field_names = set(fields1.keys()).union(fields2.keys())

    sheet = merged.find(".//sheet")
    for f in sheet.findall("field"):
        sheet.remove(f)

    special_fields = {"field_270_01_bin", "iin", "page_number", "period_year"}
    for name in sorted(all_field_names):
        val1 = fields1.get(name).text if fields1.get(name) is not None else None
        val2 = fields2.get(name).text if fields2.get(name) is not None else None

        if name in special_fields:
            if val1 and val2 and val1 != val2:
                raise ValueError(f"Special field '{name}' mismatch: {val1} vs {val2}")
            value = val1 or val2 or ""
        else:
            try:
                num1 = float(val1.replace(',', '.')) if val1 else 0.0
                num2 = float(val2.replace(',', '.')) if val2 else 0.0
                value = str(int(round(num1 + num2, 0)))
            except (ValueError, TypeError, AttributeError):
                value = val1 or val2 or ""

        new_field = etree.Element("field", name=name)
        new_field.text = value
        sheet.append(new_field)

    return merged


def merge_form_270_04(forms: list[etree.Element]) -> list[etree.Element]:
    B_fields = []
    E_fields = []
    for form in forms:
        for field in form.findall(".//field"):
            name = field.get("name", "")
            if name.startswith("field_270_04_B_"):
                B_fields.append(copy.deepcopy(field))
            elif name.startswith("field_270_04_E_"):
                E_fields.append(copy.deepcopy(field))

    def prep_fields(empty_dict, fields):
        for field in fields:
            if field.text is None:
                continue
            empty_dict[field.get('name')[:-2]].append(field.text)

        cur_dict = pd.DataFrame(empty_dict)
        cur_dict = cur_dict[~(cur_dict.isna().sum(axis=1) >= len(cur_dict.columns)-1)]
        cur_dict[list(empty_dict.keys())[0]] = [str(i) for i in range(1, len(cur_dict) + 1)]
        cur_dict = cur_dict.to_dict(orient='records')

        return cur_dict

    # Trades
    trades_dict = {
        'field_270_04_B_A': [], 'field_270_04_B_B': [], 'field_270_04_B_C': [], 'field_270_04_B_D': [],
        'field_270_04_B_E': [], 'field_270_04_B_F': [], 'field_270_04_B_G': [], 'field_270_04_B_H': [],
        'field_270_04_B_I': [], 'field_270_04_B_J': []
    }
    trades_list = prep_fields(trades_dict, B_fields)

    df_trades = pd.DataFrame(trades_list)
    float_cols = ['field_270_04_B_D', 'field_270_04_B_J']
    df_trades[float_cols] = df_trades[float_cols].astype(float)
    group_cols = df_trades.columns.difference(['field_270_04_B_A'] + float_cols).to_list()
    df_trades = df_trades.groupby(group_cols)[float_cols].sum().reset_index()
    df_trades['field_270_04_B_A'] = df_trades.index + 1
    df_trades = df_trades[df_trades.columns].astype(str)
    trades_list = df_trades[np.sort(df_trades.columns)].to_dict(orient='records')
    num_trades = len(trades_list)

    # Assets
    asset_dict = {
        'field_270_04_E_A': [], 'field_270_04_E_B': [], 'field_270_04_E_C': [], 'field_270_04_E_D': [],
        'field_270_04_E_E': []
    }
    assets_list = prep_fields(asset_dict, E_fields)

    assets_df = pd.DataFrame(assets_list).drop_duplicates('field_270_04_E_C').reset_index(drop=True)
    assets_df['field_270_04_E_A'] = (assets_df.index + 1).astype(str)
    assets_list = assets_df.to_dict(orient='records')
    num_assets = len(assets_list)

    # Create new forms
    base_form = deepcopy(forms[0])
    for field in base_form.xpath(".//field[@name]"):
        field.text = ""

    new_forms = []
    trade_idx = 0
    asset_idx = 0
    while (trade_idx < num_trades) or (asset_idx < num_assets):
        new_form = deepcopy(base_form)
        for field in new_form.findall(".//field"):
            field.text = None

        sheet = new_form.find(".//sheet")

        for i in range(6):
            if trade_idx >= num_trades:
                break
            trade = trades_list[trade_idx]
            for key in trade.keys():
                field = sheet.find(f".//field[@name='{key}_{i+1}']")
                if field is None:
                    raise ValueError(f"Поле '{key}_{i + 1}' не найдено в шаблоне.")
                field.text = trade[key]
            trade_idx += 1

        for i in range(5):
            if asset_idx >= num_assets:
                break
            asset = assets_list[asset_idx]
            for key in asset.keys():
                field = sheet.find(f".//field[@name='{key}_{i+1}']")
                if field is None:
                    raise ValueError(f"Поле '{key}_{i + 1}' не найдено в шаблоне.")
                field.text = asset[key]

            asset_idx += 1

        new_forms.append(new_form)

    if len(new_forms) == 0:
        new_forms.append(deepcopy(base_form))

    return new_forms


def merge_form_270_05(forms: list[etree.Element]) -> list[etree.Element]:
    B_fields = []
    C_fields = []
    for form in forms:
        for field in form.findall(".//field"):
            name = field.get("name", "")
            if name.startswith("field_270_05_B_"):
                B_fields.append(copy.deepcopy(field))
            elif name.startswith("field_270_05_C_"):
                C_fields.append(copy.deepcopy(field))

    def prep_fields(empty_dict, fields):
        for field in fields:
            if field.text is None:
                continue
            name = field.get('name').rsplit("_", 1)[0]
            empty_dict[name].append(field.text)

        cur_dict = pd.DataFrame(empty_dict)
        cur_dict = cur_dict[~(cur_dict.isna().sum(axis=1) >= len(cur_dict.columns)-1)]
        cur_dict[list(empty_dict.keys())[0]] = [str(i) for i in range(1, len(cur_dict) + 1)]
        cur_dict = cur_dict.to_dict(orient='records')

        return cur_dict

    buy_dict = {
        'field_270_05_B_A': [], 'field_270_05_B_B': [], 'field_270_05_B_C': [], 'field_270_05_B_D': [],
        'field_270_05_B_E': [], 'field_270_05_B_F': [], 'field_270_05_B_G': [], 'field_270_05_B_H': [],
        'field_270_05_B_I': [], 'field_270_05_B_J': [], 'field_270_05_B_K': [], 'field_270_05_B_L': [],
        'field_270_05_B_M': []
    }
    buy_list = prep_fields(buy_dict, B_fields)
    num_buys = len(buy_list)

    sell_dict = {
        'field_270_05_C_A': [], 'field_270_05_C_B': [], 'field_270_05_C_C': [], 'field_270_05_C_D': [],
        'field_270_05_C_E': [], 'field_270_05_C_F': [], 'field_270_05_C_G': [], 'field_270_05_C_H': [],
        'field_270_05_C_I': []
    }
    sell_list = prep_fields(sell_dict, C_fields)
    num_sells = len(sell_list)

    base_form = deepcopy(forms[0])
    for field in base_form.xpath(".//field[@name]"):
        field.text = ""

    new_forms = []
    buy_idx = 0
    sell_idx = 0
    while (buy_idx < num_buys) or (sell_idx < num_sells):
        new_form = deepcopy(base_form)
        sheet = new_form.find(".//sheet")

        for i in range(10):
            if buy_idx >= num_buys:
                break
            trade = buy_list[buy_idx]
            for key in trade.keys():
                field = sheet.find(f".//field[@name='{key}_{i+1}']")
                if field is None:
                    raise ValueError(f"Поле '{key}_{i + 1}' не найдено в шаблоне.")
                field.text = trade[key]
            buy_idx += 1

        for i in range(6):
            if sell_idx >= num_sells:
                break
            asset = sell_list[sell_idx]
            for key in asset.keys():
                field = sheet.find(f".//field[@name='{key}_{i+1}']")
                if field is None:
                    raise ValueError(f"Поле '{key}_{i + 1}' не найдено в шаблоне.")
                field.text = asset[key]
            sell_idx += 1

        new_forms.append(new_form)

    if len(new_forms) == 0:
        new_forms.append(deepcopy(base_form))

    return new_forms


def join_270(data: dict) -> etree.Element:
    all_form_270_04 = []
    all_form_270_05 = []
    for idx, items in enumerate(data.items()):
        root = items[1][1]

        if idx == 0:
            merged_270_00 = get_form(root, 'form_270_00')[0]
            merged_270_01 = get_form(root, 'form_270_01')[0]
        else:
            merged_270_00 = merge_form_270_00(merged_270_00, get_form(root, 'form_270_00')[0])
            merged_270_01 = merge_form_270_01(merged_270_01, get_form(root, 'form_270_01')[0])

        all_form_270_04.extend(get_form(root, 'form_270_04'))
        all_form_270_05.extend(get_form(root, 'form_270_05'))
    merged_270_04_forms = merge_form_270_04(all_form_270_04)
    merged_270_05_forms = merge_form_270_05(all_form_270_05)

    # Create a new 270 form
    new_tree = copy.deepcopy(data[list(data.keys())[0]][0])
    new_root = new_tree.getroot()

    form_map = {
        f.get("name"): f for f in new_root.findall(".//form")
        if f.get("name") not in ['form_270_00', 'form_270_01', 'form_270_04', 'form_270_05']
    }

    for f in new_root.findall(".//form"):
        new_root.remove(f)

    all_forms = ["form_270_00", "form_270_01", "form_270_02", "form_270_03", "form_270_04", "form_270_05",
                 "form_270_06", "form_270_07"]
    for name in all_forms:
        if name == "form_270_00":
            new_root.append(merged_270_00)
        elif name == "form_270_01":
            new_root.append(merged_270_01)
        elif name == "form_270_04":
            for f in merged_270_04_forms:
                new_root.append(f)
        elif name == "form_270_05":
            for f in merged_270_05_forms:
                new_root.append(f)
        elif name in form_map:
            new_root.append(form_map[name])

    return new_tree


def xml_indents(elem: etree.Element, level=0):
    i = "\n" + level * "  "

    if len(elem):
        if (not elem.text) or (not elem.text.strip()):
            elem.text = i + "  "
        for child in elem:
            xml_indents(child, level + 1)
        if (not elem.tail) or (not elem.tail.strip()):
            elem.tail = i
    else:
        if (not elem.tail) or (not elem.tail.strip()):
            elem.tail = i

    return None


def correct_merged_tree(merged_tree: etree.Element):
    root = merged_tree.getroot()

    iin = root.find(".//field[@name='iin']").text
    year = root.find(".//field[@name='period_year']").text

    for field in root.findall(".//field[@name='iin']"):
        field.text = iin
    for field in root.findall(".//field[@name='period_year']"):
        field.text = year
    for field in root.findall(".//field[@name='page_number']"):
        field.text = '1'

    return None


def save_tree(need_year, fio1, fio2, fio3, merged_tree: etree.Element):
    output_path = f"postProcessed/270merged_{need_year}_{fio1}_{fio2}_{fio3}.xml"
    xml_indents(merged_tree.getroot())
    merged_tree.write(output_path, encoding="utf-8", xml_declaration=True, pretty_print=True)

    return None


if __name__ == '__main__':
    need_year: int = 2024
    fio1: str = 'Сулейменов'
    fio2: str = 'Олжас'
    fio3: str = 'Шамильевич'

    data = get_xmls(need_year, fio1, fio2, fio3)
    merged_tree = join_270(data)
    correct_merged_tree(merged_tree)
    save_tree(need_year, fio1, fio2, fio3, merged_tree)
