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


def load_brokers_data(account: str, broker: str) -> dict:
    dfs = pd.read_excel(f'postProcessed/{broker}_{account}.xlsx', sheet_name=None)
    dfs.pop('Years_Results')

    df_yearly = pd.read_excel(f'postProcessed/{broker}_{account}.xlsx', sheet_name='Years_Results', header=None)
    df_yearly = df_yearly.dropna(axis=0, how='all').reset_index(drop=True)

    current_title = None
    current_start = None
    for i, row in df_yearly.iterrows():
        non_nulls = row.dropna()
        if (len(non_nulls) == 1 and 'Yearly' in non_nulls.iloc[0]) or (i == len(df_yearly)-1):
            if current_title and current_start is not None:
                if i == len(df_yearly) - 1:
                    i += 1

                sub_df = df_yearly.iloc[current_start:i].dropna(how='all').copy()
                sub_df.columns = sub_df.iloc[0]
                sub_df = sub_df[1:].reset_index(drop=True)
                for col in ['Year', 'Exchange', 'Country', 'Currency']:
                    if col in sub_df.columns:
                        sub_df[col] = sub_df[col].ffill().infer_objects(copy=False)
                sub_df.dropna(axis=1, how='all', inplace=True)
                dfs[current_title] = sub_df

            if i < len(df_yearly)-1:
                current_title = non_nulls.iloc[0].strip()
                current_start = i + 1

    return dfs


def load_xml_template(iin: str) -> tuple[etree.ElementTree, etree.Element]:
    xml_template_path = "files/270.00 empty.xml"
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


def prep_trades(dfs: dict, need_year: int, split: bool) -> list:
    if 'Trades' not in dfs.keys():
        return []

    cur_trades = dfs['Trades'][dfs['Trades']['Date_Time'].dt.year == need_year].copy()
    cur_trades['Comment'] = cur_trades['Comment'].astype(str)
    cur_trades = cur_trades[~cur_trades['Comment'].str.contains('Transfer', na=False)].reset_index(drop=True)

    cur_trades = cur_trades[
            ~cur_trades['Security_ID'].str.contains('.SWAP', na=False) &
            ~cur_trades['Security_ID'].str.contains('.REPO', na=False) &
            ~cur_trades['Comment'].str.contains('Transfer', na=False) &
            ~cur_trades['Asset_Category'].isin(['Currency', 'FOREX', 'FX_SPOT']) &
            (cur_trades['Country'] != 'KZ')
        ].copy()
    print(f'{Fore.RED}Forex trades will not be indicated{Style.RESET_ALL}')
    if len(cur_trades) == 0:
        return []

    cur_trades.loc[cur_trades['Quantity'] > 0, 'Oper_Type'] = 'Покупка'
    cur_trades.loc[cur_trades['Quantity'] < 0, 'Oper_Type'] = 'Продажа'
    cur_trades.loc[cur_trades['Comment'] == 'Bond Full Call', 'Oper_Type'] = 'Полученное по долговым обязательствам'
    mask = cur_trades['Comment'].isin(['Spinoff', 'Enrolment of rights'])
    cur_trades.loc[mask, 'Oper_Type'] = 'Безвозмездно полученное (за исключением наследства)'

    mask = cur_trades['Asset_Category'].str.contains('ption')
    cur_trades.loc[mask, 'Asset_Type'] = 'производные финансовые инструменты'
    cur_trades['Asset_Type'] = cur_trades['Asset_Type'].fillna('ценные бумаги')

    cur_trades['Date_Time'] = cur_trades['Date_Time'].dt.date
    need_cols = ['Oper_Type', 'Asset_Type', 'Quantity', 'Security_ID', 'Date_Time', 'Country', 'Currency', 'Invest']
    cur_trades = cur_trades[need_cols]
    cur_trades = cur_trades.groupby(list(set(need_cols) - {'Quantity', 'Invest'}))[['Quantity', 'Invest']]\
        .sum().reset_index()

    trades_list = []
    for idx, row in cur_trades.iterrows():
        quant = abs(row['Quantity'] / 2) if split else abs(row['Quantity'])
        invest = abs(row['Invest'] / 2) if split else abs(row['Invest'])

        cur_dict = {}
        cur_dict['field_270_04_B_A'] = str(idx + 1)
        cur_dict['field_270_04_B_B'] = row['Oper_Type']
        cur_dict['field_270_04_B_C'] = row['Asset_Type']
        cur_dict['field_270_04_B_D'] = str(round(quant, 4))
        cur_dict['field_270_04_B_E'] = row['Security_ID']
        cur_dict['field_270_04_B_F'] = row['Date_Time'].strftime('%d.%m.%Y')
        cur_dict['field_270_04_B_G'] = '-'
        cur_dict['field_270_04_B_H'] = row['Country']
        cur_dict['field_270_04_B_I'] = row['Currency']
        cur_dict['field_270_04_B_J'] = str(round(invest, 2))
        trades_list.append(cur_dict)

    return trades_list


def prep_civ_servant(dfs: dict, need_year: int, split: bool, fio1: str, fio2: str, fio3: str, iin: str) -> tuple:
    if 'Trades' not in dfs.keys():
        return ([], [])

    cur_trades = dfs['Trades']
    cur_trades['Comment'] = cur_trades['Comment'].astype(str)
    cur_trades = cur_trades[
        ~cur_trades['Security_ID'].str.contains('.SWAP', na=False) &
        ~cur_trades['Security_ID'].str.contains('.REPO', na=False) &
        ~cur_trades['Comment'].str.contains('Transfer', na=False) &
        ~cur_trades['Asset_Category'].isin(['Currency', 'FOREX', 'FX_SPOT'])
    ].copy()
    print(f'{Fore.RED}Forex trades will not be indicated{Style.RESET_ALL}')
    if len(cur_trades) == 0:
        return ([], [])

    # Prep df
    cur_trades.loc[cur_trades['Quantity'] > 0, 'Oper_Type'] = 'Покупка'
    cur_trades.loc[cur_trades['Quantity'] < 0, 'Oper_Type'] = 'Продажа'

    mask = cur_trades['Asset_Category'].str.contains('ption')
    cur_trades.loc[mask, 'Asset_Type'] = 'производные финансовые инструменты'
    cur_trades['Asset_Type'] = cur_trades['Asset_Type'].fillna('ценные бумаги')

    # Calc sources of funds
    cur_trades['Date_Time'] = cur_trades['Date_Time'].dt.date
    need_cols = ['Oper_Type', 'Asset_Type', 'Quantity', 'Security_ID', 'Date_Time', 'Country', 'Currency',
                 'Invest', 'KZT']
    cur_trades = cur_trades[need_cols]
    cur_trades = cur_trades.groupby(list(set(need_cols) - {'Quantity', 'Invest'}))[['Quantity', 'Invest']]\
        .sum().reset_index().sort_values('Date_Time').reset_index(drop=True)
    cur_trades['InvestKZT'] = cur_trades['Invest'] * cur_trades['KZT']
    cur_trades['SellFundsKZT'] = np.where(cur_trades['InvestKZT'] > 0, 0, cur_trades['InvestKZT']).cumsum()

    source = []
    for idx, row in enumerate(cur_trades.itertuples(index=False)):
        invest = row.InvestKZT
        fund = cur_trades.at[idx, 'SellFundsKZT']
        if (abs(fund) > invest) and (invest > 0):
            cur_trades.loc[idx:, 'SellFundsKZT'] += invest
            source.append('денежные средства от реализации активов')
        elif invest > 0:
            source.append('собственные средства (денежные средства, полученный доход с момента представления '
                          'первоначальной Декларации об активах и обязательствах)')
        else:
            source.append('')
    cur_trades['Source'] = source
    cur_trades['SourceID'] = np.where(cur_trades['Source'] != '', iin, '')
    cur_trades['SourceName'] = np.where(cur_trades['Source'] != '', f'{fio1} {fio2} {fio3}', '')

    cur_trades = cur_trades[pd.to_datetime(cur_trades['Date_Time']).dt.year == need_year].copy()
    if len(cur_trades) == 0:
        return ([], [])

    # Create data
    buy_list = []
    sell_list = []
    for _, row in cur_trades.iterrows():
        if row['Oper_Type'] == 'Покупка':
            buy_sum = row['Invest'] / 2 if split else row['Invest']

            cur_dict = {}
            cur_dict['field_270_05_B_A'] = str(len(buy_list) + 1)
            cur_dict['field_270_05_B_B'] = row['Asset_Type']
            cur_dict['field_270_05_B_C'] = row['Security_ID']
            cur_dict['field_270_05_B_D'] = row['Date_Time'].strftime('%d.%m.%Y')
            cur_dict['field_270_05_B_E'] = row['Country']
            cur_dict['field_270_05_B_F'] = '-'
            cur_dict['field_270_05_B_G'] = row['Currency']
            cur_dict['field_270_05_B_H'] = str(round(buy_sum, 2))
            cur_dict['field_270_05_B_I'] = row['Source']
            cur_dict['field_270_05_B_J'] = row['SourceID']
            cur_dict['field_270_05_B_K'] = row['SourceName']
            cur_dict['field_270_05_B_L'] = row['Currency']
            cur_dict['field_270_05_B_M'] = str(round(buy_sum, 2))
            buy_list.append(cur_dict)
        elif row['Oper_Type'] == 'Продажа':
            sell_sum = abs(row['InvestKZT'] / 2) if split else abs(row['InvestKZT'])

            cur_dict = {}
            cur_dict['field_270_05_C_A'] = str(len(sell_list) + 1)
            cur_dict['field_270_05_C_B'] = row['Asset_Type']
            cur_dict['field_270_05_C_C'] = row['Security_ID']
            cur_dict['field_270_05_C_D'] = row['Date_Time'].strftime('%d.%m.%Y')
            cur_dict['field_270_05_C_E'] = row['Country']
            cur_dict['field_270_05_C_F'] = '-'
            cur_dict['field_270_05_C_G'] = '-'
            cur_dict['field_270_05_C_H'] = '-'
            cur_dict['field_270_05_C_I'] = str(round(sell_sum, 2))
            sell_list.append(cur_dict)
        else:
            raise Exception(f"Unspecified Oper_Type - {row['Oper_Type']}")

    return buy_list, sell_list


def prep_assets(dfs: dict, need_year: int) -> list:
    if 'Positions' not in dfs.keys():
        return []

    cur_positions = dfs['Positions'][dfs['Positions']['Year'] == need_year].copy()
    cur_positions = cur_positions[
            (cur_positions['Country'] != 'KZ') &
            ~cur_positions['Isin'].str.contains('.SWAP') & ~cur_positions['Isin'].str.contains('.REPO')
        ]
    if len(cur_positions) == 0:
        return []

    cur_positions = cur_positions.drop_duplicates(subset=['Asset', 'Isin', 'Country']).reset_index(drop=True)

    mask = cur_positions['Asset'].str.contains('ption')
    cur_positions.loc[mask, 'Asset_Type'] = 'производные финансовые инструменты'
    cur_positions['Asset_Type'] = cur_positions['Asset_Type'].fillna('ценные бумаги')

    assets_list = []
    for idx, row in cur_positions.iterrows():
        cur_dict = {}
        cur_dict['field_270_04_E_A'] = str(idx + 1)
        cur_dict['field_270_04_E_B'] = row['Asset_Type']
        cur_dict['field_270_04_E_C'] = row['Isin']
        cur_dict['field_270_04_E_D'] = row['Country']
        cur_dict['field_270_04_E_E'] = row['Country']
        assets_list.append(cur_dict)

    return assets_list


def fill_270_04(root: etree.Element, dfs: dict, tax_data: dict, need_year: int, template_form, split, civ_servant
                ) -> dict:

    if civ_servant:
        trades_list = []
    else:
        trades_list = prep_trades(dfs, need_year, split)
    num_trades = len(trades_list)

    assets_list = prep_assets(dfs, need_year)
    num_assets = len(assets_list)

    if (num_trades > 0) or (num_assets > 0):
        tax_data['form_270_00']['page_270_00_01']['pril_4'] = 'true'

    new_forms = []
    trade_idx = 0
    asset_idx = 0
    while (trade_idx < num_trades) or (asset_idx < num_assets):
        new_form = deepcopy(template_form)
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

    if len(new_forms) > 0:
        root.remove(template_form)
        for form in new_forms:
            root.append(form)

    return tax_data


def fill_270_05(root: etree.Element, dfs: dict, tax_data: dict, need_year: int, template_form, split, fio1, fio2, fio3,
                iin):
    buy_list, sell_list = prep_civ_servant(dfs, need_year, split, fio1, fio2, fio3, iin)
    num_buys = len(buy_list)
    num_sells = len(sell_list)
    if (num_buys > 0) or (num_sells > 0):
        tax_data['form_270_00']['page_270_00_01']['pril_5'] = 'true'

    new_forms = []
    buy_idx = 0
    sell_idx = 0
    while (buy_idx < num_buys) or (sell_idx < num_sells):
        new_form = deepcopy(template_form)
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

    if len(new_forms) > 0:
        root.remove(template_form)
        for form in new_forms:
            root.append(form)

    return tax_data


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


def fill_taxes(root: etree.Element, dfs: dict, tax_data: dict, need_year: int, split: bool) -> None:
    cur_date = datetime.datetime.now().strftime('%d.%m.%Y')
    tax_data['form_270_00']['page_270_00_01']['accept_date'] = cur_date
    tax_data['form_270_00']['page_270_00_01']['submit_date'] = cur_date

    kaz_exch = ['ITS', 'AIX', 'KASE']
    calc_dict = {
        'trades_profit': 0, 'trades_profit_kz': 0, 'swaps': 0, 'repos': 0, 'corp_profit': 0, 'divs': 0,
        'divs_kz_correct': 0, 'withhold': 0, 'deposits': 0, 'coupons': 0, 'others': 0
    }
    if 'Yearly Trades' in dfs.keys():
        cur_df = dfs['Yearly Trades'][dfs['Yearly Trades']['Year'] == need_year]
        if 'Exchange' in cur_df.columns:
            calc_dict['trades_profit'] = cur_df[~cur_df['Exchange'].isin(kaz_exch)]['OnlyProfit_KZT'].sum()
            calc_dict['trades_profit_kz'] = cur_df[cur_df['Exchange'].isin(kaz_exch)]['OnlyProfit_KZT'].sum()
        else:
            calc_dict['trades_profit'] = cur_df['OnlyProfit_KZT'].sum()
            calc_dict['trades_profit_kz'] = 0
    if 'Yearly Swaps' in dfs.keys():
        cur_df = dfs['Yearly Swaps'][dfs['Yearly Swaps']['Year'] == need_year]
        calc_dict['swaps'] = cur_df[cur_df['Country'] != 'KZ']['OnlyProfit_KZT'].sum()
    if 'Yearly Repo' in dfs.keys():
        cur_df = dfs['Yearly Repo'][dfs['Yearly Repo']['Year'] == need_year]
        calc_dict['repos'] = cur_df[cur_df['Country'] != 'KZ']['OnlyProfit_KZT'].sum()
    if 'Yearly Corp Actions' in dfs.keys():
        cur_df = dfs['Yearly Corp Actions'][dfs['Yearly Corp Actions']['Year'] == need_year]
        calc_dict['corp_profit'] = cur_df['OnlyProfit_KZT'].sum()
    if 'Yearly Dividends' in dfs.keys():
        cur_df = dfs['Yearly Dividends'][dfs['Yearly Dividends']['Year'] == need_year]
        calc_dict['divs'] = cur_df['OnlyProfit_KZT'].sum()
        calc_dict['divs_kz_correct'] = cur_df['OP_KZcorrect'].sum()

        tax_kz = cur_df['Tax_KZT'].sum()
        tax_kz_withhold = cur_df['Tax_KZT_Withhold'].sum()
        calc_dict['withhold'] += (tax_kz - tax_kz_withhold)
    if 'Yearly Deposits' in dfs.keys():
        cur_df = dfs['Yearly Deposits'][dfs['Yearly Deposits']['Year'] == need_year]
        calc_dict['deposits'] = cur_df['OnlyProfit_KZT'].sum()
    if 'Yearly Coupons' in dfs.keys():
        cur_df = dfs['Yearly Coupons'][dfs['Yearly Coupons']['Year'] == need_year]
        calc_dict['coupons'] = cur_df['OnlyProfit_KZT'].sum()
    if 'Yearly Taxes' in dfs.keys():
        cur_df = dfs['Yearly Taxes'][dfs['Yearly Taxes']['Year'] == need_year]
        calc_dict['withhold'] += cur_df['Withhold_forTrades_KZT'].sum()
    if 'Yearly SpinOff_Redemp' in dfs.keys():
        cur_df = dfs['Yearly SpinOff_Redemp'][dfs['Yearly SpinOff_Redemp']['Year'] == need_year]
        calc_dict['others'] = cur_df['OnlyProfit_KZT'].sum()

    if split:
        calc_dict = {k: round(v / 2, 0) for k, v in calc_dict.items()}
    else:
        calc_dict = {k: round(v, 0) for k, v in calc_dict.items()}

    tax_data['form_270_01'] = {}
    tax_data['form_270_01']['page_270_01_01'] = {}
    tax_page1 = tax_data['form_270_01']['page_270_01_01']

    tax_page1['field_270_01_A_1_2'] = calc_dict['trades_profit']
    tax_page1['field_270_01_A_1_1'] = calc_dict['trades_profit_kz']
    tax_page1['field_270_01_A_1'] = tax_page1['field_270_01_A_1_2'] + tax_page1['field_270_01_A_1_1']
    tax_page1['field_270_01_A'] = tax_page1['field_270_01_A_1']

    tax_page1['field_270_01_B_1_9'] = calc_dict['swaps'] + calc_dict['others']
    tax_page1['field_270_01_B_1_5'] = (calc_dict['coupons'] + calc_dict['deposits'] + calc_dict['corp_profit'] +
                                       calc_dict['repos'])
    tax_page1['field_270_01_B_1_4'] = calc_dict['divs']
    tax_page1['field_270_01_B_1'] = (tax_page1['field_270_01_B_1_4'] + tax_page1['field_270_01_B_1_5'] +
                                     tax_page1['field_270_01_B_1_9'])
    tax_page1['field_270_01_B'] = tax_page1['field_270_01_B_1']

    tax_page1['field_270_01_D'] = tax_page1['field_270_01_A'] + tax_page1['field_270_01_B']

    tax_page1['field_270_01_E_1'] = (calc_dict['coupons'] + calc_dict['corp_profit'] + calc_dict['divs_kz_correct'] +
                                     calc_dict['trades_profit_kz'])
    tax_page1['field_270_01_E'] = tax_page1['field_270_01_E_1']

    tax_page1['field_270_01_G'] = tax_page1['field_270_01_D'] - tax_page1['field_270_01_E']
    tax_page1['field_270_01_H'] = tax_page1['field_270_01_G'] * 0.1
    tax_page1['field_270_01_I'] = calc_dict['withhold']
    tax_page1['field_270_01_K'] = tax_page1['field_270_01_H'] - tax_page1['field_270_01_I']

    if tax_page1['field_270_01_D'] > 0:
        tax_data['form_270_00']['page_270_00_01']['pril_1'] = 'true'
        for k, v in tax_page1.items():
            tax_page1[k] = str(int(round(v, 0)))
    else:
        tax_data.pop('form_270_01')

    for form_name, pages in tax_data.items():
        for page, data in pages.items():
            update_field_values(root, form_name, page, data)

    return None


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


def save_new_form(root: etree.ElementTree, tree: etree.ElementTree, account: str, broker: str, need_year: int,
                  fio1: str, fio2: str, fio3: str) -> None:
    output_path = f"postProcessed/270_{need_year}_{account}_{broker}_{fio1}_{fio2}_{fio3}_filled.xml"
    xml_indents(root)
    tree.write(output_path, encoding="utf-8", pretty_print=True, xml_declaration=True)

    return None


def run(account, broker, need_year, fio1, fio2, fio3, iin, split=False, civ_servant=False):
    tax_data = {
        'form_270_00': {'page_270_00_01': {'fio1': fio1, 'fio2': fio2, 'fio3': fio3, 'iin': iin,
                                           'head_name': f"{fio1} {fio2} {fio3}", 'period_year': str(need_year)}}
    }

    dfs = load_brokers_data(account, broker)
    tree, root = load_xml_template(iin)

    form_270_04 = get_exist_form(root, 'form_270_04')
    tax_data = fill_270_04(root, dfs, tax_data, need_year, form_270_04, split, civ_servant)
    if civ_servant:
        form_270_05 = get_exist_form(root, 'form_270_05')
        fill_270_05(root, dfs, tax_data, need_year, form_270_05, split, fio1, fio2, fio3, iin)
    fill_taxes(root, dfs, tax_data, need_year, split)

    save_new_form(root, tree, account, broker, need_year, fio1, fio2, fio3)

    return None


if __name__ == "__main__":
    account: str = 'U1717377'  # U14219740, U5157275, U1134034, 1432280, 7F8339(D), 8A0627
    broker: str = 'IB'  # IB, Tsifra, Freedom, Exante, FBank
    need_year: int = 2024
    fio1: str = 'Ашихмин'
    fio2: str = 'Алексей'
    fio3: str = 'Михайлович'
    iin: str = '930113399037'

    split_form: bool = False
    fio21: str = 'Сейтмаганбетова'
    fio22: str = 'Эльмира'
    fio23: str = 'Нурлановна'
    iin2: str = '840811401930'

    civ_servant: bool = False

    if split_form:
        run(account, broker, need_year, fio1, fio2, fio3, iin, split_form, civ_servant)
        run(account, broker, need_year, fio21, fio22, fio23, iin2, split_form, civ_servant)
    else:
        run(account, broker, need_year, fio1, fio2, fio3, iin, split_form, civ_servant)
