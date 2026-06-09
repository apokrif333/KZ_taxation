import datetime
import pandas as pd
import numpy as np

from lxml import etree
from copy import deepcopy
from colorama import init, Fore, Style
from pprint import pprint

# Settings
pd.options.display.max_rows = 1_500
pd.options.display.max_columns = 500
pd.set_option('display.width', 1400)
pd.options.display.float_format = '{:.6f}'.format


def load_xml_template(iin: str) -> tuple[etree.ElementTree, etree.Element]:
    xml_template_path = "files/250.00 empty.xml"
    tree = etree.parse(xml_template_path)
    root = tree.getroot()

    for field in root.findall(".//field[@name='iin']"):
        field.text = iin

    return tree, root


def get_exist_form(root: etree.Element, form_name: str) -> etree.Element:
    template_form = None
    for form in root.findall("form"):
        if form.attrib.get("name") == form_name:
            template_form = form
            break

    if template_form is None:
        raise ValueError(f"Форма {form_name} не найдена в шаблоне.")

    return template_form


def prep_assets(pos_df: pd.DataFrame) -> list:
    cur_positions = pos_df[
            ~pos_df['Isin'].str.contains('.SWAP') & ~pos_df['Isin'].str.contains('.REPO')
        ]
    total_sum_kzt = (cur_positions['Price'] * cur_positions['KZT'] * cur_positions['Qty']).sum()
    print(f"Total sum: {total_sum_kzt:,.0f}₸")

    assets_list = []
    for idx, row in cur_positions.iterrows():
        cur_dict = {}
        cur_dict['field_250_04_7_A'] = str(idx + 1)
        cur_dict['field_250_04_7_B'] = str(row['Qty'])
        cur_dict['field_250_04_7_C'] = row['Country']
        cur_dict['field_250_04_7_D'] = row['Currency']
        cur_dict['field_250_04_7_E'] = str(row['Price'])
        assets_list.append(cur_dict)

    return assets_list


def fill_270_04(root: etree.Element, pos_df: pd.DataFrame, tax_data: dict, template_form) -> dict:
    assets_list = prep_assets(pos_df)
    num_assets = len(assets_list)

    new_forms = []
    asset_idx = 0
    while (asset_idx < num_assets):
        new_form = deepcopy(template_form)
        sheet = new_form.find(".//sheet")

        for i in range(6):
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

    if len(new_forms) > 0:
        root.remove(template_form)
        for form in new_forms:
            root.append(form)

    return tax_data


def xml_indents(elem, level=0):
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


def save_new_form(root: etree.ElementTree, tree: etree.ElementTree, need_year: int, fio1: str, fio2: str, fio3: str):
    output_path = f"postProcessed/250_{need_year-1}1231_{fio1}_{fio2}_{fio3}_filled.xml"
    xml_indents(root)
    tree.write(output_path, encoding="utf-8", pretty_print=True, xml_declaration=True)

    return None


def update_field_values(root: etree.Element, form_name: str, sheet_name: str, new_fields: dict):
    for form in root.findall("form"):
        if form.get("name") != form_name:
            continue
        for sheet_group in form.findall("sheetGroup"):
            for sheet in sheet_group.findall("sheet"):
                if sheet.get("name") != sheet_name:
                    continue

                existing_fields = {f.get("name"): f for f in sheet.findall("field")}
                all_field_names = set(existing_fields) | set(new_fields)
                sorted_names = sorted(all_field_names)

                for f in sheet.findall("field"):
                    sheet.remove(f)

                for name in sorted_names:
                    if name in new_fields:
                        value = new_fields[name]
                        elem = etree.Element("field", name=name)
                        if value is not None:
                            elem.text = str(value)
                        elem.tail = '\n'
                        sheet.append(elem)
                    else:
                        existing_elem = existing_fields[name]
                        existing_elem.tail = '\n'
                        sheet.append(existing_fields[name])

                return True

    raise Exception(f"{form_name} / {sheet_name} not found")


def run(need_year, fio1, fio2, fio3, iin, pos_df):
    cur_date = datetime.datetime.now().strftime('%d.%m.%Y')
    full_name = f"{fio1} {fio2} {fio3}"
    tax_data = {'form_250_00': {
        'page_250_00_01': {'app_year': f"31.12.{need_year - 1}", 'name1': fio1, 'name2': fio2, 'name3': fio3,
                           'iin': iin, 'period_year': str(need_year)},
        'page_250_00_02': {'accept_date': cur_date, 'submit_date': cur_date, 'filler_name': full_name}
    }}

    tree, root = load_xml_template(iin)

    form_250_04 = get_exist_form(root, 'form_250_04')
    tax_data = fill_270_04(root, pos_df, tax_data, form_250_04)

    for form_name, pages in tax_data.items():
        for page, data in pages.items():
            update_field_values(root, form_name, page, data)

    save_new_form(root, tree, need_year, fio1, fio2, fio3)

    return None


def read_files(files: dict, need_year: int) -> pd.DataFrame:
    need_year -= 1

    pos_df = pd.DataFrame()
    for broker, split in files.items():
        account = broker.split('_')[1]
        broker = broker.split('_')[0]

        cur_df = pd.read_excel(f'postProcessed/{broker}_{account}.xlsx', sheet_name=None)
        if 'Positions' not in cur_df.keys():
            continue
        else:
            cur_df = cur_df['Positions']

        if split:
            cur_df['Qty'] = round(cur_df['Qty'] / 2, 4)
        cur_df['Price'] = round(cur_df['Price'], 2)
        cur_df = cur_df[cur_df['Year'] == need_year]
        pos_df = pd.concat([pos_df, cur_df], ignore_index=True, sort=False)

    print(pos_df)
    pos_df = pos_df[['Isin', 'Country', 'Currency', 'Qty', 'Price', 'KZT']]
    pos_df = pos_df.groupby(['Isin', 'Country', 'Currency', 'Price', 'KZT'])[['Qty']].sum() \
        .sort_values(['Isin', 'Qty'])\
        .reset_index()
    print(pos_df)

    return pos_df


if __name__ == "__main__":
    need_year: int = 2025
    fio1: str = 'Тергемесова'
    fio2: str = 'Гаухар'
    fio3: str = 'Кажыбековна'
    iin: str = '901111401059'
    files = {
        'IB_U8850143': False
    }

    pos_df = read_files(files, need_year)
    run(need_year, fio1, fio2, fio3, iin, pos_df)
