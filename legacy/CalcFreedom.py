import os
import csv
import pandas as pd
import numpy as np
import re
import financedatabase as fd
import json
import urllib.request
import urllib.parse
import questionary

from colorama import init, Fore, Style
from pandasgui import show
from collections import defaultdict
from openpyxl.styles import Alignment, Font
from pprint import pprint

# Settings
pd.options.display.max_rows = 1_500
pd.options.display.max_columns = 500
pd.set_option('display.width', 1400)
pd.options.display.float_format = '{:.6f}'.format


def openfigi_finder(isin: str, currency: str):
    """
    https://www.openfigi.com/api/documentation#v3-id-type-values
    Make search and mapping API requests and print the results to the console

    Returns:
        None
    """

    def api_call(path: str, data: dict | None = None, method: str = "POST"):
        """
        Make an api call to `api.openfigi.com`. Uses builtin `urllib` library, end users may prefer to swap out this
        function with another library of their choice

        Args:
            path (str): API endpoint, for example "search"
            method (str, optional): HTTP request method. Defaults to "POST".
            data (dict | None, optional): HTTP request data. Defaults to None.

        Returns:
            JsonType: Response of the api call parsed as a JSON object
        """

        OPENFIGI_API_KEY = '4d4d476f-1819-47a4-abca-84b650d70c24'
        OPENFIGI_BASE_URL = "https://api.openfigi.com"

        headers = {"Content-Type": "application/json"}
        if OPENFIGI_API_KEY:
            headers |= {"X-OPENFIGI-APIKEY": OPENFIGI_API_KEY}

        request = urllib.request.Request(
            url=urllib.parse.urljoin(OPENFIGI_BASE_URL, path),
            data=data and bytes(json.dumps(data), encoding="utf-8"),
            headers=headers,
            method=method,
        )

        with urllib.request.urlopen(request) as response:
            json_response_as_string = response.read().decode("utf-8")
            json_obj = json.loads(json_response_as_string)
            return json_obj

    # search_request = {"query": "APPLE"}
    # print("Making a search request:", search_request)
    # response = api_call("/v3/search", search_request)
    # print("Search response:", json.dumps(response, indent=2))

    mapping_request = [
        {"idType": "ID_ISIN", "idValue": isin, 'currency': currency},
    ]
    print("Making a mapping request:", mapping_request)
    response = api_call("/v3/mapping", mapping_request)

    return response


def asset_type_replacer(series: pd.Series):
    replacer_dict = {
        'Comdty': 'Commodity',
        'Corp': 'Corporate Bond', 'Облигации': 'Corporate Bond',
        'Curncy': 'Currency', 'Forex': 'Currency',
        'Акции': 'Equity', 'Депозитарные расписки': 'Equity', 'Фонды': 'Equity',
        'Govt': 'Government Bond',
        'M-Mkt': 'Money Market',
        'Mtge': 'Mortgage-Backed Security'
    }
    series = series.replace(replacer_dict)

    return series


def prep_currency_df():
    currency_df = pd.read_excel('Currency.xlsx')
    currency_df['Date'] = pd.to_datetime(currency_df['Date'], format='%d.%m.%Y')
    values = [col for col in currency_df.columns if ('quant' not in col) and ('Date' not in col)]
    currency_df = currency_df.melt(
        id_vars=['Date'],
        value_vars=values,
        var_name='Currency',
        value_name='KZT'
    )

    return currency_df


def read_files(account: str) -> dict:
    global max_year

    total_dict = {}
    for f in os.listdir('files'):
        matches = re.findall(fr'(?<![A-Za-z0-9]){account}(?![A-Za-z0-9])', f)
        if len(matches) == 0:
            continue

        if ('~' not in f) and ('transfer' not in f):
            print(f)
            cur_dict = pd.read_excel(os.path.join('files', f), sheet_name=None)
            account_sheets = np.sort([col for col in cur_dict.keys() if 'Account' in col])
            cur_year = pd.to_datetime(account_sheets[-1][-8:]).year
            max_year = max(max_year, cur_year)

            cur_dict['Account'] = cur_dict[account_sheets[-1]].copy()
            for del_sheet in account_sheets:
                cur_dict.pop(del_sheet)
            cur_dict.pop('Worksheet')

            cur_dict = {re.sub(r'\d{8} - \d{8}', '', key).strip(): data for key, data in cur_dict.items()}
            cur_dict = {key.replace(f' at {cur_year}1231', ''): data for key, data in cur_dict.items()}

            total_dict[cur_year] = cur_dict

    return total_dict


def prep_fininfo(dfs) -> pd.DataFrame:
    # Prep Securities
    if 'Securities' in dfs.keys():
        df = dfs['Securities'][['Тикер', 'ISIN', 'Тип актива', 'Валюта']] \
            .sort_values(['ISIN', 'Тикер', 'Тип актива', 'Валюта'])\
            .drop_duplicates(subset=['ISIN', 'Тикер'])\
            .reset_index(drop=True)
    else:
        df = pd.DataFrame(columns=['Тикер', 'ISIN', 'Тип актива', 'Валюта'])

    # Get data from trades
    df = dfs['Trades'][['Тикер', 'ISIN', 'Рынок', 'Валюта']]\
        .drop_duplicates()\
        .merge(df, how='outer', on=['Тикер', 'ISIN'])
    df['Валюта'] = df['Валюта_x'].fillna(df['Валюта_y'])
    df.drop(columns=['Валюта_x', 'Валюта_y'], inplace=True)

    # Get data from corpactions
    if 'Corpactions' in dfs.keys():
        df = dfs['Corpactions'][['Тикер', 'ISIN', 'Валюта']]\
            .drop_duplicates()\
            .merge(df, how='outer', on=['Тикер', 'ISIN'])
        df['Валюта'] = df['Валюта_x'].fillna(df['Валюта_y'])
        df.drop(columns=['Валюта_x', 'Валюта_y'], inplace=True)

    # Check Forex
    mask = df['Тикер'].str.contains('/') & (df['Рынок'] == 'OTC') & pd.isna(df['Тип актива'])
    df.loc[mask, 'Тип актива'] = 'Forex'

    # Get data from database
    db_fininfo = pd.read_csv('files/FreedomFinInfo.csv')
    df = df.merge(db_fininfo, how='left', on=['Тикер', 'ISIN', 'Рынок','Валюта'])
    df['Тип актива'] = df['Тип актива_x'].fillna(df['Тип актива_y'])
    df.drop(columns=['Тип актива_x', 'Тип актива_y'], inplace=True)

    # Get data from Openfigi
    empty_data = df[pd.isna(df['Тип актива'])]
    enriched_data = {'ISIN': [], 'Валюта': [], 'Тип актива': []}
    for idx, row in empty_data.iterrows():
        asset_type = []
        answ = openfigi_finder(row['ISIN'], row['Валюта'])[0]
        if 'warning' not in answ.keys():
            for v in answ['data']:
                asset_type.append(v['marketSector'])
        else:
            choices = ["Акции", "Облигации", "Депозитарные расписки"]
            print(answ['warning'])
            print(f"Уточните. {row['Тикер']}, {row['ISIN']}, {row['Валюта']} это:")
            for i, opt in enumerate(choices, 1):
                print(f"{i}. {opt}")
            choice = choices[int(input("Введите номер: "))-1]
            asset_type = [choice]

        asset_type = set(asset_type)
        if len(asset_type) > 1:
            print(row)
            raise Exception(f"OpenFigi has returned different marketSectors - {asset_type}")

        enriched_data['ISIN'].append(row['ISIN'])
        enriched_data['Валюта'].append(row['Валюта'])
        enriched_data['Тип актива'].append(list(asset_type)[0])

    enriched_data = pd.DataFrame(enriched_data).drop_duplicates()
    if len(enriched_data) > 0:
        df = df.merge(enriched_data, on=['ISIN', 'Валюта'], how='left')
        df['Тип актива'] = df['Тип актива_x'].fillna(df['Тип актива_y'])
        df.drop(columns=['Тип актива_x', 'Тип актива_y'], inplace=True)
    df['Тип актива'] = asset_type_replacer(df['Тип актива'])
    df['Country'] = df['ISIN'].str[:2].str.replace('XS', 'BE')

    # Enrich database
    db_fininfo_new = pd.concat([db_fininfo, df.dropna()]).drop_duplicates()
    if len(db_fininfo_new) > len(db_fininfo):
        db_fininfo_new.to_csv('files/FreedomFinInfo.csv', index=False)

    dfs['FinInfo'] = df.rename(columns={'Рынок': 'Exchange'})
    if 'Securities' in dfs.keys():
        dfs.pop('Securities')

    return dfs


def check_sec_in_out(dfs):
    if 'Sec In Out' not in dfs.keys():
        return dfs

    df = dfs['Sec In Out'][~dfs['Sec In Out']['Комментарий'].str.contains('места хранения')]

    proceed_in_corpactions = ['Сплит', 'Погашение', 'Конвертация', 'Спин-офф', 'Перевод ценных бумаг',
                              'Зачисление прав']
    df = df[~df['Тип'].isin(proceed_in_corpactions)]

    mask = df['Комментарий'].isin(['Cмена тикера', 'Смена тикера'])
    t_change = df[mask]
    if len(t_change) > 0:
        t_change = t_change.merge(dfs['FinInfo'], how='left', on=['ISIN', 'Тикер'])
        t_change['Exchange'] = t_change['Exchange'].fillna('None')
        check_nan = t_change.groupby('ISIN')[['Exchange']].agg(lambda x: x.isna().sum()).reset_index()
        nan_isins = check_nan[check_nan['Exchange'] > 0]['ISIN']
        t_change = t_change[~t_change['ISIN'].isin(nan_isins)]

        if len(t_change) > 0:
            t_change['Sorter'] = np.where(t_change['Количество'] < 0, 'old', 'new')
            t_change = t_change.pivot(index='ISIN', columns='Sorter', values=['Тикер', 'Валюта', 'Количество'])

            trades_df = dfs['Trades']
            for idx, row in t_change.iterrows():
                old_ticker = row[('Тикер', 'old')]
                new_currency = row[('Валюта', 'new')]
                trades_df.loc[trades_df['Symbol'] == old_ticker, 'Currency'] = new_currency
            trades_df = add_currency({'Trades': trades_df})['Trades']
            for idx, row in t_change.iterrows():
                old_ticker = row[('Тикер', 'old')]
                new_ticker = row[('Тикер', 'new')]
                trades_df.loc[trades_df['Symbol'] == old_ticker, 'T._Price'] = trades_df['T._Price'] / trades_df['KZT']
                trades_df.loc[trades_df['Symbol'] == old_ticker, 'Сумма'] = trades_df['T._Price'] * trades_df['Quantity']
                trades_df.loc[trades_df['Symbol'] == old_ticker, 'Symbol'] = new_ticker

            trades_df.drop(columns=['KZT'], inplace=True)
            dfs['Trades'] = trades_df

    df = df[~mask]
    assert len(df) == 0, f"Unprocessed events in 'Sec In Out\n{df}"

    return dfs


def create_transfers_in_df(dfs):
    if 'Sec In Out' not in dfs.keys():
        return dfs

    df = dfs['Sec In Out'][~dfs['Sec In Out']['Комментарий'].str.contains('места хранения')]
    mask = df['Тип'].isin(['Блокировка', 'Вывод в другой депозитарий', 'Перевод из другого депозитария',
                           'Перевод ценных бумаг'])
    transfers = df[mask].copy()
    if len(transfers) > 0:
        transfers['TransNumber'] = transfers['Комментарий'].str.extract(r'\s+(\d+)', expand=False)
        trans_group = transfers.groupby('TransNumber')['Количество'].sum()
        trans_group = trans_group[trans_group != 0].index
        transfers = transfers[transfers['TransNumber'].isin(trans_group)].sort_values(['TransNumber', 'Дата'])
        transfers = transfers.groupby('TransNumber').last()
        transfers['Direction'] = np.where(transfers['Количество'] < 0, 'Out', 'In')
        transfers['Дата'] = pd.to_datetime(transfers['Дата'])
        transfers.reset_index(inplace=True)

        cur_fininfo = dfs['FinInfo'][['ISIN', 'Тип актива', 'Country', 'Валюта']].drop_duplicates()
        transfers = transfers.merge(cur_fininfo, how='left', left_on=['ISIN'], right_on=['ISIN'])
        transfers.rename(
            columns={'Дата': 'Date_Time', 'Количество': 'Qty', 'Тикер': 'Symbol ID', 'Валюта': 'Currency'},
            inplace=True
        )
        dfs['Transfers'] = transfers

    return dfs


def transfers_in(dfs):
    if 'Transfers' not in dfs.keys():
        return dfs

    trans_in = dfs['Transfers'][dfs['Transfers']['Direction'] == 'In']
    if len(trans_in) == 0:
        return dfs

    print(f"{Fore.BLUE} У вас имеются акции, которые поступили путём перевода между брокерскими счетами."
          f"{Style.RESET_ALL}")
    for idx, row in trans_in.iterrows():
        print(f"{row['Date_Time']} {row['ISIN']} {row['Symbol ID']} количество {row['Qty']}")
    print(f"{Fore.BLUE}"
          f"Отправьте excel/csv файл, с информацией о дате отправки и цене приобретения данных ценных бумаг."
          f"Имя колонки[пример данных] - "
          f"isin_code[US7496552057],currency[RUB],t_date[2024-03-20T11:35:37],t_q[10],t_price[1384.40]"
          f"{Style.RESET_ALL}")
    trans_data = pd.read_excel(f'files/transfers_to_Freedom_{account}.xlsx', parse_dates=['t_date'])
    del_cols = ['Year', 'Comm_per_one']
    trans_data = trans_data[trans_data.columns.difference(del_cols)]

    # Check
    trans_data_group = trans_data.groupby(['t_date', 'isin_code', 'currency']).agg(TotalQty=('t_q', 'sum')).reset_index()
    trans_in_group = trans_in.groupby(['Date_Time', 'ISIN', 'Currency']).agg(TotalQty=('Qty', 'sum')).reset_index()
    check = trans_in_group.merge(
            trans_data_group,
            left_on=['ISIN', 'Currency', 'TotalQty'], right_on=['isin_code', 'currency', 'TotalQty'],
            how='left'
        )
    dont_have_isins = check[pd.isna(check['t_date'])]
    assert len(dont_have_isins) == 0, (f"По данным тикерам нет данных или предоставлено неверное суммарное количество "
                                       f"бумаг\n{dont_have_isins}")

    trans_in = trans_in.merge(
            trans_in_group,
            on=['Date_Time', 'ISIN', 'Currency'],
            how='left'
        )
    trans_data = trans_data.merge(
            trans_data_group,
            on=['t_date', 'isin_code', 'currency'],
            how='left'
        ).merge(
            trans_in[['Date_Time', 'ISIN', 'Currency', 'TotalQty']],
            left_on=['isin_code', 'currency', 'TotalQty'], right_on=['ISIN', 'Currency', 'TotalQty'],
            how='left'
        ).drop(columns=['t_date', 'isin_code', 'currency', 'TotalQty'])\
        .rename(columns={
            'symbol': 'Symbol',  't_q': 'Quantity', 't_price': 'T._Price', 'ISIN': 'Security_ID',
            'Asset': 'Asset_Category', 'Comm_per_one': 'Comm_Per_One',
        })
    trans_data['Oper_Type'] = 'Покупка'
    trans_data['Comm_Per_One'] = 0
    trans_data['Comment'] = 'TransferIn'

    dfs['Trades'] = pd.concat([dfs['Trades'], trans_data], sort=False, ignore_index=True)\
        .sort_values('Date_Time')\
        .reset_index(drop=True)

    return dfs


def prep_trades(dfs) -> dict:
    df = dfs['Trades'].rename(columns={'Рынок': 'Exchange'})
    df = df.merge(dfs['FinInfo'], how='left', on=['Тикер', 'ISIN', 'Exchange', 'Валюта'])
    check = df[pd.isna(df['Тип актива'])]
    assert len(check) == 0, f"Trades df has empty values in 'Тип актива'\n{check}"

    # Correct swaps
    df.loc[df['Операция'].str.contains('своп') & (df['Тип актива'] == 'Currency'), 'ISIN'] = df['Тикер'] + '.SWAP'
    df.loc[df['Операция'].str.contains('своп') & (df['Тип актива'] == 'Equity'), 'ISIN'] = df['ISIN'] + '.SWAP'
    df.loc[df['Операция'].str.contains('репо'), 'ISIN'] = df['ISIN'] + '.REPO'

    # Correct bonds' prices
    mask = df['Тип актива'].str.contains('Bond')
    bond_correct = round((df[mask]['Сумма'] - df[mask]['Комиссия']) / df[mask]['Цена'] / df[mask]['Количество'], 0)
    df.loc[bond_correct.index, 'Цена'] = df['Цена'] * bond_correct

    # Correct cols
    df['Дата сделки'] = pd.to_datetime(df['Дата сделки'])
    df['Операция'] = df['Операция'].str.strip()
    df['Количество'] = np.where(df['Операция'].str.contains('Покупка'), df['Количество'], -df['Количество'])\
        .astype(float)
    df['Comm_Per_One'] = df['Комиссия'] / df['Количество'].abs()
    df['Comment'] = None
    df = df.rename(columns={
        'ISIN': 'Security_ID', 'Дата сделки': 'Date_Time', 'Тикер': 'Symbol', 'Тип актива': 'Asset_Category',
        'Валюта': 'Currency', 'Количество': 'Quantity', 'Цена': 'T._Price', 'Операция': 'Oper_Type'
    })

    dfs['Trades'] = df

    return dfs


def check_kase_liquid(df):
    liquid_df = pd.DataFrame(
        data=[
            [2024, 'BITO_KZ.KZ', 1],
        ],
        columns=['Year', 'Тикер', 'IsliquidKASE']
    )

    df['Дата'] = pd.to_datetime(df['Дата'])
    df['Year'] = df['Дата'].dt.year
    df = df.merge(liquid_df, how='left', on=['Year', 'Тикер'])
    df = df.fillna({'IsliquidKASE': 0}).drop(columns=['Year'])

    return df


def prep_div_coupons(dfs):
    df = dfs['Cash In Out']
    fininfo = dfs['FinInfo']

    # Add FinInfo to each row
    # ticker_pat = r'\((?:[^()]*\s)?([A-Z0-9]+\.[A-Z]{2,3})\)'
    ticker_pat = r'\b([A-Z0-9]+(?:\.[A-Z0-9]+)+)\b'
    tax_pat = r'Ставка налога\s+(\d+(?:\.\d+)?)'
    amount_pat = r'На одну бумагу\s+([\d.]+)'
    isin_pat = r'\b(?:ISIN\s*)?\(?([A-Z]{2}[0-9A-Z]{9}[0-9])\)?\b(?![A-Z0-9])'

    df['Тикер'] = df['Комментарий'].str.extract(ticker_pat, expand=False)
    df['ISIN'] = df['Комментарий'].str.extract(isin_pat, expand=False)
    df['Tax_rates'] = df['Комментарий'].str.extract(tax_pat, expand=False).astype(float)
    amount = df['Комментарий'].str.extract(amount_pat)
    df['Amount_per_one'] = amount[0].astype(float)

    df = df.merge(fininfo, how='left', on='Тикер', suffixes=('', '_ticker'))\
        .merge(fininfo, how='left', on='ISIN', suffixes=('', '_isin'))
    df.fillna({'Тикер': df['Тикер_isin']}, inplace=True)
    df.fillna({'ISIN': df['ISIN_ticker']}, inplace=True)
    df.fillna({'Тип актива': df['Тип актива_isin']}, inplace=True)
    df.fillna({'Exchange': df['Exchange_isin']}, inplace=True)
    df.fillna({'Country': df['Country_isin']}, inplace=True)

    df = df[[col for col in df.columns if '_' not in col]]

    # If Freedom set incorrect ISIN
    mask = ~pd.isna(df['ISIN']) & pd.isna(df['Тикер'])
    if len(df[mask]) != 0:
        ticker_reserve_pat = r'(\b[A-Z]{2,5}(?:\.[A-Z]{2,5})+\b)'
        df.loc[mask, 'Тикер'] = df['Комментарий'].str.extract(ticker_reserve_pat, expand=False)
        for idx, row in df[mask].iterrows():
            cur_fi = fininfo[fininfo['Тикер'] == row['Тикер']]
            assert len(cur_fi) == 1, f"{cur_fi}\n Must be just one asset"

            df.loc[idx, 'ISIN'] = cur_fi['ISIN'].iloc[0]
            df.loc[idx, 'Exchange'] = cur_fi['Exchange'].iloc[0]
            df.loc[idx, 'Тип актива'] = cur_fi['Тип актива'].iloc[0]
            df.loc[idx, 'Country'] = cur_fi['Country'].iloc[0]

    # Check duplicates
    duplicates = df[~pd.isna(df['ISIN'])]
    mask = duplicates.duplicated(['Тип', 'Дата', 'Сумма', 'Комментарий'], keep=False)
    assert (mask.any() == False).all(), f"Cash In Out has duplicates after FinInfo connection\n{duplicates[mask]}"

    # Money
    money_actions = [
        'Перевод внутри компании', 'Карточный платеж', 'Банковский перевод', 'Перевод внутри банка', 'Операции по карте'
    ]
    mask = df['Тип'].isin(money_actions)
    if mask.any():
        dfs['MoneyTrans'] = df[mask].drop(columns=['Тикер', 'ISIN', 'Exchange', 'Тип актива'])
    df = df[~mask]

    # Coupons
    mask = (df['Тип'] == 'Купон')
    if mask.any():
        coupons = df[mask].rename(columns={'Сумма': 'Amount'})
        coupons['Комментарий'] = coupons['Комментарий'].str.replace('дата фиксации', 'дата среза')\
            .str.replace('  ', ' ')
        coupons['Дата среза'] = coupons['Комментарий'].str.extract(r'дата среза (\d{4}-\d{2}-\d{2})')\
            .fillna(coupons['Комментарий'].str.extract(r'дата среза (\d{2}.\d{2}.\d{4})'))
        coupons['Дата среза'] = pd.to_datetime(coupons['Дата среза'], format='mixed')
        coupons['Type'] = np.where(coupons['Комментарий'].str.contains('Reverted'), 'Revert', 'Coupon')

        idx_cols = ['Дата', 'Тикер', 'Валюта', 'ISIN', 'Exchange', 'Country', 'Тип актива', 'Дата среза']
        coupons = coupons.pivot_table(index=idx_cols, columns="Type", values="Amount", aggfunc="sum")\
            .reset_index()\
            .sort_values(['ISIN', 'Дата'])\
            .fillna(0)
        coupons['Amount'] = coupons['Coupon'] + coupons['Revert'] if 'Revert' in coupons.columns else coupons['Coupon']
        dfs['Coupons'] = coupons
    df = df[~mask]

    # Redemption
    mask = (df['Тип'] == 'Погашение')
    if mask.any():
        redempt = df[mask].copy()
        redempt['Дата'] = pd.to_datetime(redempt['Дата'])
        redempt['T._Price'] = redempt['Комментарий'].str.extract(r'бумагу:\s*(\d+(?:\.\d+)?)\s*[A-Z]{3}')
        redempt['T._Price'] = redempt['T._Price'].fillna(
            redempt['Комментарий'].str.extract(r'per security:\s*[A-Z]{3}\s*(\d+(?:\.\d+)?)')[0]
        ).astype(float)

        trades_df = dfs['Trades']
        for idx, row in redempt.iterrows():
            cur_df = trades_df[trades_df['Security_ID'] == row['ISIN']].iloc[:1].copy()
            idx = cur_df.index[0]

            cur_df.loc[idx, 'Symbol'] = row['Тикер']
            cur_df.loc[idx, 'Oper_Type'] = 'Продажа'
            cur_df.loc[idx, 'Quantity'] = -(row['Сумма'] / row['T._Price'])
            cur_df.loc[idx, 'T._Price'] = row['T._Price']
            cur_df.loc[idx, 'Сумма'] = row['Сумма']
            cur_df.loc[idx, 'Date_Time'] = row['Дата']
            cur_df.loc[idx, 'Comment'] = 'Bond Full Call' if row['Тип актива'] == 'Bond' else 'Stock Redemption'
            nan_cols = ['P/L по закрытым сделкам', 'Комиссия', 'Расчеты', 'Order ID', 'Tax', 'SMAT']
            for col in nan_cols:
                cur_df.loc[idx, col] = None
            zero_cols = ['Комиссия', 'Comm_Per_One']
            for col in zero_cols:
                cur_df.loc[idx, col] = 0

            dfs['Trades'] = pd.concat([dfs['Trades'], cur_df], ignore_index=True)
    df = df[~mask]

    # Type for Divs
    df['Type'] = np.where(
        (df['Тип'] == 'Дивиденды') & ~df['Комментарий'].str.contains('Reverted'),
        'Div',
        np.where(
            (df['Тип'] == 'Налоги') & ~df['Комментарий'].str.contains('Reverted'),
            'W_Tax',
            np.where(
                (df['Тип'] == 'Дивиденды') & df['Комментарий'].str.contains('Reverted'),
                'Div Revert',
                np.where(
                    (df['Тип'] == 'Налоги') & df['Комментарий'].str.contains('Reverted'),
                    'W_Tax Revert',
                    None
                )
            )
        )
    )

    # Other money trans
    mask = pd.isna(df['Type'])
    check = df[mask][~df[mask]['Тип'].isin([
        'Блокировка', 'Оплата по сделке', 'Разблокировка', 'Сборы агента при выплате дивидендов',
        'Вывод денег на карту', 'Перевод внутри холдинга', 'Корректировочная проводка',
        'Компенсация по корпоративному действию', 'Конвертация'
    ])]
    assert len(check) == 0, f'{check}\n Unprocessed type in df OtherMoneyTrans'
    if mask.any():
        dfs['OtherMoneyTrans'] = df[mask]

    # Divs
    df = df[~mask]
    if len(df) > 0:
        df['RecDate'] = df['Комментарий'].str.extract(r'record date\s(\d{4}-\d{2}-\d{2})')
        df = df.sort_values(['ISIN', 'RecDate', 'Дата'])

        correct_date = df[(df['Type'] == 'Div') & (df['Дата'] > df['RecDate'])]\
            .groupby(['ISIN', 'RecDate', 'Валюта', 'Country'])\
            [['Дата']]\
            .first()\
            .reset_index()
        df = df.drop(columns='Дата').merge(
            correct_date,
            on=['ISIN', 'RecDate', 'Валюта', 'Country'],
            how='left'
        )

        df = df.pivot_table(index=['Дата', 'RecDate', 'Тикер', 'Валюта'], columns="Type", values="Сумма", aggfunc="sum")\
            .reset_index()\
            .sort_values(['Тикер', 'RecDate', 'Дата'])\
            .fillna(0)
        for col in ['W_Tax', 'Div Revert', 'W_Tax Revert']:
            if col not in df:
                df[col] = 0

        df['Amount'] = (df['Div'] + df['Div Revert']).clip(lower=0)
        df['Withhold'] = df['W_Tax'] + df['W_Tax Revert']
        df['Withhold'] = np.where((df['Withhold'] < 0) & (df['Amount'] > 0), df['Withhold'], 0)
        df['W_TaxRate'] = df['Withhold'] / df['Amount']
        df = df.merge(fininfo.drop(columns=['Валюта']), how='left', on='Тикер')
        df = check_kase_liquid(df)
        dfs['Dividends'] = df

    return dfs


def prep_cols(df) -> pd.DataFrame:
    df['Date_Time'] = pd.to_datetime(df['Дата'])
    df = df.drop('Дата', axis=1).rename(columns={'Валюта': 'Currency'})

    return df


def prep_corp_actions(dfs):
    if 'Corpactions' not in dfs.keys():
        return dfs

    main_df = dfs['Corpactions'].copy()
    main_df['Сумма'] = main_df['Сумма'].astype(float)

    processed_actions = {'Дивиденды', 'Сборы агента при выплате дивидендов', 'Купон', 'Погашение'}
    current_actions = set(main_df['Тип'].unique())
    unprocessed_actions = current_actions - processed_actions
    main_df = main_df[main_df['Тип'].isin(unprocessed_actions)]

    mask = main_df['Тип'].isin(['Конвертация', 'Сплит']) & (main_df['Актив'].str.strip() != 'Деньги')
    convert_df = main_df[mask].copy()
    if len(convert_df) > 0:
        convert_df.sort_values('Дата', inplace=True)
        convert_df['Type'] = np.where(convert_df['Бумаг на дату фиксации'] == 0, 'new', 'old')
        convert_df['Ratio'] = convert_df['Комментарий'].str.extract(r'ratio:\s(\d+/\d+)')[0]
        convert_df['Ratio'] = convert_df['Ratio'].str.split('/').apply(lambda x: float(x[0]) / float(x[1]))
        convert_df = convert_df.pivot(
            index=['Комментарий', 'Валюта', 'Дата', 'Ratio'],
            columns='Type',
            values=['Сумма', 'Тикер', 'ISIN']
        )
        convert_df.columns = ['_'.join(col).strip() for col in convert_df.columns]
        convert_df.reset_index(inplace=True)

        trades = dfs['Trades']
        for idx, row in convert_df.iterrows():
            cur_mask = ((trades['Security_ID'] == row['ISIN_old']) & (trades['Currency'] == row['Валюта'])
                        & (trades['Date_Time'] < row['Дата']))
            trades.loc[cur_mask, 'Symbol'] = row['Тикер_new']
            trades.loc[cur_mask, 'Security_ID'] = row['ISIN_new']
            trades.loc[cur_mask, 'T._Price'] = trades['T._Price'] * row['Ratio']
            trades.loc[cur_mask, 'Quantity'] = trades['Quantity'] / row['Ratio']
            comm_mask = cur_mask & pd.isna(trades['Comment'])
            trades.loc[comm_mask, 'Comment'] = f"Convert {row['Тикер_old']}/Split {row['Ratio']}"

    main_df = main_df[~mask]
    mask = main_df['Тип'].isin(['Конвертация']) & (main_df['Актив'].str.strip() == 'Деньги')
    compens_df = main_df[mask].copy()
    if len(compens_df) > 0:
        compens_df['QtyToBe'] = compens_df['Комментарий'].str.extract(r'получению\s(\d+(?:\.\d+)?),')[0].astype(float)
        compens_df['QtyReceived'] = compens_df['Комментарий'].str.extract(r'получено\s(\d+),')[0].astype(float)
        compens_df['Qty'] = compens_df['QtyReceived'] - compens_df['QtyToBe']
        compens_df['Ratio'] = compens_df['Бумаг на дату фиксации'] / compens_df['QtyToBe']
        compens_df['Тикер'] = compens_df['Комментарий'].str.extract(r'количество бумаг\s(\b[A-Z0-9]+\.[A-Z]{2,}\b)')
        compens_df['T._Price'] = compens_df['На 1'] * compens_df['Ratio']

        trades = dfs['Trades']
        for idx, row in compens_df.iterrows():
            new_row = trades[trades['Symbol'] == row['Тикер']].iloc[:1].copy()
            idx = new_row.index[0]

            new_row.loc[idx, 'Oper_Type'] = 'Продажа'
            new_row.loc[idx, 'Quantity'] = row['Qty']
            new_row.loc[idx, 'T._Price'] = row['T._Price']
            new_row.loc[idx, 'Сумма'] = row['T._Price'] * row['Qty']
            new_row.loc[idx, 'Date_Time'] = row['Дата']
            new_row.loc[idx, 'Comment'] = 'Compensation'
            nan_cols = ['P/L по закрытым сделкам', 'Комиссия', 'Расчеты', 'Order ID', 'Tax', 'SMAT']
            for col in nan_cols:
                new_row.loc[idx, col] = None
            zero_cols = ['Комиссия', 'Comm_Per_One']
            for col in zero_cols:
                new_row.loc[idx, col] = 0

            dfs['Trades'] = pd.concat([dfs['Trades'], new_row], ignore_index=True).sort_values('Date_Time')

    main_df = main_df[~mask]
    mask = (main_df['Тип'] == 'Спин-офф')
    spin_off = main_df[mask].copy()
    if len(spin_off) > 0:
        spin_off['Комментарий'] = spin_off['Комментарий'].str.replace('  ', ' ').str.strip()
        spin_off['Revert'] = spin_off['Комментарий'].str.contains('Reverted: ')
        spin_off['Комментарий'] = spin_off['Комментарий'].str.replace('Reverted: ', '')

        delete = spin_off[spin_off['Комментарий'].duplicated(keep=False)].copy()
        delete['Revert'] = True if delete['Revert'].any() else False
        del_idx = delete['Revert'].index

        spin_off = spin_off[~spin_off.index.isin(del_idx)]
        trades = dfs['Trades']
        for idx, row in spin_off.iterrows():
            if row['ISIN'] in trades['Security_ID'].values:
                new_row = trades[trades['Security_ID'] == row['ISIN']].iloc[:1].copy()
            else:
                new_row = trades.iloc[:1].copy()
                new_row['Exchange'] = 'None'
            idx = new_row.index[0]

            new_row.loc[idx, 'Date_Time'] = pd.to_datetime(row['Дата'])
            new_row.loc[idx, 'Symbol'] = row['Тикер']
            new_row.loc[idx, 'Security_ID'] = row['ISIN']
            new_row.loc[idx, 'Quantity'] = row['Сумма']
            new_row.loc[idx, 'Oper_Type'] = 'Покупка'
            new_row.loc[idx, 'T._Price'] = 0
            new_row.loc[idx, 'Comment'] = 'Spinoff'
            nan_cols = ['P/L по закрытым сделкам', 'Комиссия', 'Расчеты', 'Order ID', 'Tax', 'SMAT']
            for col in nan_cols:
                new_row.loc[idx, col] = None
            zero_cols = ['Комиссия', 'Comm_Per_One']
            for col in zero_cols:
                new_row.loc[idx, col] = 0

            dfs['Trades'] = pd.concat([dfs['Trades'], new_row], ignore_index=True).sort_values('Date_Time')

    main_df = main_df[~mask]
    mask = (main_df['Тип'] == 'Компенсация по корпоративному действию')
    spinoff_compens = main_df[mask].copy()
    if len(spinoff_compens) > 0:
        spinoff_compens = spinoff_compens.drop(columns=['Актив', 'На 1', 'Дата фиксации', 'Бумаг на дату фиксации',
                                                        'Налог у источника', 'Налог у брокера'])
        spinoff_compens.rename(
            columns={'Тикер': 'Symbol', 'Сумма': 'Amount', 'Тип': 'Type', 'Комментарий': 'Comment'},
            inplace=True
        )
        fininfo = dfs['FinInfo'][['Тикер', 'ISIN', 'Валюта', 'Country']].drop_duplicates()
        spinoff_compens = spinoff_compens.merge(
            fininfo,
            left_on=['Symbol', 'ISIN', 'Валюта'], right_on=['Тикер', 'ISIN', 'Валюта'],
            how='inner'
        )
        dfs['SpinOff_Redemp'] = spinoff_compens

    main_df = main_df[~mask]
    mask = (main_df['Тип'] == 'Зачисление прав')
    new_rights = main_df[mask].copy()
    if len(new_rights) > 0:
        pattern = r"Corporate action\s+([A-Z0-9]+\.[A-Z]{2})\s+\(([A-Z]{2}[A-Z0-9]{9}[0-9])\)"
        new_rights[['Тикер_old', 'ISIN_old']] = new_rights['Комментарий'].str.extract(pattern, expand=False)

        trades = dfs['Trades']
        for idx, row in new_rights.iterrows():
            new_row = trades[trades['Security_ID'] == row['ISIN_old']].iloc[:1].copy()
            idx = new_row.index[0]

            new_row.loc[idx, 'Symbol'] = row['Тикер']
            new_row.loc[idx, 'Security_ID'] = row['ISIN']
            new_row.loc[idx, 'Oper_Type'] = 'Покупка'
            new_row.loc[idx, 'Quantity'] = row['Сумма']
            new_row.loc[idx, 'T._Price'] = 0
            new_row.loc[idx, 'Сумма'] = 0
            new_row.loc[idx, 'Date_Time'] = row['Дата']
            new_row.loc[idx, 'Comment'] = 'Enrolment of rights'
            nan_cols = ['P/L по закрытым сделкам', 'Комиссия', 'Расчеты', 'Order ID', 'Tax', 'SMAT']
            for col in nan_cols:
                new_row.loc[idx, col] = None
            zero_cols = ['Комиссия', 'Comm_Per_One']
            for col in zero_cols:
                new_row.loc[idx, col] = 0

            dfs['Trades'] = pd.concat([dfs['Trades'], new_row], ignore_index=True).sort_values('Date_Time')

    main_df = main_df[~mask]
    assert len(main_df) == 0, f"Corpactions has unprocessed events\n{main_df}"

    dfs['Trades'].sort_values('Date_Time', inplace=True)

    return dfs


def fill_nan_in_trades(dfs):
    df = dfs['Trades']

    # Add Exchange
    len_trades = len(df)
    fininfo = dfs['FinInfo'][['ISIN', 'Тип актива', 'Country', 'Валюта', 'Exchange']]
    fininfo = fininfo[~pd.isna(fininfo['Exchange'])].drop_duplicates()

    df = df.merge(
        fininfo,
        left_on=['Security_ID', 'Currency'], right_on=['ISIN', 'Валюта'],
        how='left',
        suffixes=('', '_y')
    )
    df['Exchange'] = df['Exchange'].fillna(df['Exchange_y'])
    df['Asset_Category'] = df['Asset_Category'].fillna(df['Тип актива'])
    df['Country'] = df['Country'].fillna(df['Country_y'])
    df.drop(columns=['ISIN', 'Тип актива', 'Country_y', 'Валюта', 'Exchange_y'], inplace=True)
    assert len(df) == len_trades, f"FinInfo creates dublicates in Trades df. Old trades is {len_trades}, new {len(df)}"

    # Add Asset Type and Country
    len_trades = len(df)
    fininfo = dfs['FinInfo'][['ISIN', 'Тип актива', 'Валюта', 'Country']].drop_duplicates()

    df = df.merge(
        fininfo,
        left_on=['Security_ID', 'Currency'], right_on=['ISIN', 'Валюта'],
        how='left',
        suffixes=('', '_y')
    )
    df['Asset_Category'] = df['Asset_Category'].fillna(df['Тип актива'])
    df['Country'] = df['Country'].fillna(df['Country_y'])
    df.drop(columns=['ISIN', 'Тип актива', 'Валюта', 'Country_y'], inplace=True)
    assert len(df) == len_trades, f"FinInfo creates dublicates in Trades df. Old trades is {len_trades}, new {len(df)}"

    df['Exchange'] = df['Exchange'].fillna('None')
    dfs['Trades'] = df

    return dfs


def prep_data(total_dict: dict):
    dfs = {}
    for year, sheets in total_dict.items():
        for k, v in sheets.items():
            if len(v) == 0:
                continue

            if k not in dfs.keys():
                dfs[k] = pd.DataFrame()

            if k == 'Account':
                v = v.T.reset_index()
                v.columns = v.iloc[0]
                v = v.drop(0)
                v.index = [year]
                v.index.name = 'Year'
            elif k in ['Cash Flows', 'Sec Flows']:
                v['Year'] = year

            v.columns = v.columns.str.strip()
            dfs[k] = pd.concat([dfs[k], v])

    # Prep each df separately
    dfs['Account'] = dfs['Account'].reset_index()
    dfs = prep_fininfo(dfs)
    dfs = prep_trades(dfs)
    dfs = create_transfers_in_df(dfs)
    dfs = transfers_in(dfs)
    dfs = check_sec_in_out(dfs)
    dfs = prep_div_coupons(dfs)
    dfs = prep_corp_actions(dfs)
    dfs = fill_nan_in_trades(dfs)

    need_k = [
        'Commissions', 'Corpactions', 'Cash In Out', 'MoneyTrans', 'Coupons', 'Dividends', 'OtherMoneyTrans',
        'SpinOff_Redemp'
    ]
    for k in need_k:
        if k in dfs.keys():
            dfs[k] = prep_cols(dfs[k])

    return dfs


def prep_positions_df(df: pd.DataFrame):
    df['Symbol'] = df['Symbol'].fillna('')

    sum_cols = ['Qty', 'Comm_per_one']
    group_cols = [col for col in df.columns if col not in sum_cols]
    df['Date_Time'] = df['Date_Time'].dt.date
    df['Group_Qty'] = df.groupby(group_cols)['Qty'].transform('sum')
    df['group_comm'] = df['Qty'] / df['Group_Qty'] * df['Comm_per_one']
    df = df.groupby(group_cols)[['Qty', 'group_comm']]\
        .sum()\
        .reset_index()\
        .rename(columns={'group_comm': 'Comm_per_one'})

    expand = df['Isin'].str.split('.')
    df['Isin'] = expand.str[0]
    df['Comment'] = expand.str[1]
    df = df.sort_values(['Year', 'Asset', 'Isin', 'Date_Time'])

    return df


def fifo_calc(dfs: dict) -> dict:
    global max_year

    df = dfs['Trades'].sort_values(['Security_ID', 'Date_Time'])
    print(f"{Fore.MAGENTA}Forex trades have not been processed{Style.RESET_ALL}")
    df = df[~((df['Asset_Category'] == 'Currency') & ~df['Security_ID'].str.contains('.SWAP'))]

    results = []
    total_positions = []
    for isin, group in df.groupby(['Security_ID', 'Currency']):
        isin = isin[0]
        group = group.sort_values('Date_Time')
        longs = []
        shorts = []

        snapshots = {}
        current_year = None
        for _, row in group.iterrows():

            row_year = row['Date_Time'].year
            if (current_year is not None) and (row['Date_Time'] > pd.to_datetime(f"12/31/{current_year} 23:59:59")):
                while current_year < row_year:
                    snapshot_positions = longs + shorts
                    snapshots[current_year] = [p.copy() for p in snapshot_positions]
                    current_year += 1
            current_year = row_year

            asset = row['Asset_Category']
            symb = row['Symbol']
            exch = row['Exchange']
            country = row['Country']
            currency = row['Currency']
            qty = row['Quantity']
            date = row['Date_Time']
            price = row['T._Price']
            comm_per_one = row['Comm_Per_One']
            comment = row['Comment'] if asset == 'Corporate Bond' else None

            if qty > 0:
                if shorts:
                    buy_qty = qty
                    while buy_qty > 0 and shorts:
                        short = shorts[0]
                        matched_qty = min(buy_qty, short['Qty'])
                        total_comm = matched_qty * short['Comm_per_one'] + matched_qty * comm_per_one
                        result = {
                            'Asset': asset,  'Symbol': symb, 'Isin': isin, 'Exchange': exch, 'Country': country,
                            'Currency': currency,
                            'Position_Type': 'short',
                            'Enter_Date': short['Date_Time'], 'Enter_Price': short['Price'],
                            'Exit_Date': date, 'Exit_Price': price,
                            'Quantity': matched_qty,
                            'Commission': total_comm,
                            'PnL': (short['Price'] - price) * matched_qty,
                            'Comment': comment
                        }
                        results.append(result)

                        short['Qty'] = round(short['Qty'] - matched_qty, 8)
                        buy_qty = round(buy_qty - matched_qty, 8)
                        if short['Qty'] == 0:
                            shorts.pop(0)
                else:
                    longs.append({
                        'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Exchange': exch, 'Country': country,
                        'Currency': currency,
                        'Date_Time': date, 'Type': 'покупка', 'Qty': qty, 'Price': price,
                        'Comm_per_one': comm_per_one
                    })

            else:
                sell_qty = -qty
                if longs:
                    while sell_qty > 0 and longs:
                        buy = longs[0]
                        matched_qty = min(sell_qty, buy['Qty'])
                        total_comm = matched_qty * buy['Comm_per_one'] + matched_qty * comm_per_one

                        result = {
                            'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Exchange': exch, 'Country': country,
                            'Currency': currency,
                            'Position_Type': 'long',
                            'Enter_Date': buy['Date_Time'], 'Enter_Price': buy['Price'],
                            'Exit_Date': date, 'Exit_Price': price,
                            'Quantity': matched_qty,
                            'Commission': total_comm,
                            'PnL': (price - buy['Price']) * matched_qty,
                            'Comment': comment
                        }
                        results.append(result)

                        buy['Qty'] = round(buy['Qty'] - matched_qty, 8)
                        sell_qty = round(sell_qty - matched_qty, 8)
                        if buy['Qty'] == 0:
                            longs.pop(0)

                else:
                    shorts.append({
                        'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Exchange': exch, 'Country': country,
                        'Currency': currency,
                        'Date_Time': date, 'Type': 'продажа', 'Qty': sell_qty, 'Price': price,
                        'Comm_per_one': comm_per_one
                    })

        if current_year and (longs or shorts):
            while current_year <= max_year:
                snapshots[current_year] = [p.copy() for p in longs + shorts]
                current_year += 1

        rows = []
        for year, positions in snapshots.items():
            for pos in positions:
                pos_with_year = pos.copy()
                pos_with_year['Year'] = year
                rows.append(pos_with_year)
        total_positions.append(pd.DataFrame(rows))

    df_fifo = pd.DataFrame(results)
    if len(df_fifo) != 0:
        df_fifo = df_fifo.sort_values(['Isin', 'Exit_Date'])
        expand = df_fifo['Isin'].str.split('.')
        df_fifo['Isin'] = expand.str[0]
        df_fifo['Comment'] = df_fifo['Comment'].fillna(expand.str[1])
        dfs[f'Fifo'] = df_fifo

    total_positions = pd.concat(total_positions, ignore_index=True)
    if len(total_positions) > 0:
        total_positions = prep_positions_df(total_positions)
        dfs['Positions'] = total_positions

    return dfs


def transfers_out(dfs, account: str):
    if 'Transfers' not in dfs.keys():
        return dfs

    global max_year

    pos_df = dfs['Positions'].sort_values(['Isin', 'Date_Time', 'Year'])
    trans_out = dfs['Transfers'][dfs['Transfers']['Direction'] == 'Out']
    if len(trans_out) == 0:
        return dfs

    removed_rows = []
    for _, transfer in trans_out.iterrows():
        isin = transfer['ISIN']
        qty_to_remove = -transfer['Qty']
        trans_date = transfer['Date_Time']
        trans_year = trans_date.year
        pos_df = pos_df[~((pos_df['Isin'] == isin) & (pos_df['Year'] > trans_year))]

        matched_rows = pos_df[(pos_df['Isin'] == isin) & (pos_df['Qty'] > 0) & (pos_df['Year'] == trans_year)]
        matched_rows = matched_rows.sort_values('Date_Time')
        for idx, row in matched_rows.iterrows():
            if qty_to_remove <= 0:
                break

            row_copy = row.copy()
            row_copy['Date_Time'] = trans_date
            current_qty = row['Qty']
            if current_qty <= qty_to_remove:
                removed_rows.append(row_copy)
                qty_to_remove -= current_qty
                pos_df.loc[idx, 'Qty'] = 0
            else:
                row_copy['Qty'] = qty_to_remove
                removed_rows.append(row_copy)
                pos_df.loc[idx, 'Qty'] -= qty_to_remove
                qty_to_remove = 0

        new_data = pos_df[(pos_df['Isin'] == isin) & (pos_df['Year'] == trans_year)].copy()
        while trans_year < max_year:
            trans_year += 1
            new_data['Year'] = trans_year
            pos_df = pd.concat([pos_df, new_data])

    removed_df = pd.DataFrame(removed_rows)
    removed_df.rename(
        columns={'Isin': 'isin_code', 'Currency': 'currency', 'Date_Time': 't_date', 'Qty': 't_q', 'Price': 't_price'},
        inplace=True
    )
    removed_df.to_excel(f'files/transfers_from_Freedom_{account}.xlsx', index=False)

    dfs['Positions'] = pos_df[pos_df['Qty'] != 0].sort_values(['Year', 'Asset', 'Isin', 'Date_Time'])

    return dfs


def add_currency(dfs):
    currency_df = prep_currency_df()

    for k, v in dfs.items():
        if len(v) == 0:
            continue

        if k in ['Account']:
            continue
        elif k in ['Trades', 'Commissions', 'Corpactions', 'Cash In Out', 'Coupons', 'OtherMoneyTrans', 'Dividends',
                   'SpinOff_Redemp']:
            v['Date'] = pd.to_datetime(v['Date_Time'].dt.date)
        elif k in ['Fifo']:
            v['Date'] = pd.to_datetime(v['Exit_Date'].dt.date)
        elif k in ['Positions']:
            v['Date'] = pd.to_datetime(v['Date_Time'])
        else:
            continue

        v = v.merge(currency_df, on=['Date', 'Currency'], how='left')
        dfs[k] = v.drop(['Date'], axis=1)
        dfs[k]['KZT'] = np.where(dfs[k]['Currency'] == 'KZT', 1, dfs[k]['KZT'])

    return dfs


def final_preparations(dfs: dict):
    offshores = pd.read_excel('Offshores_iso2.xlsx')

    for k in ['Cash In Out', 'Cash Flows', 'Sec Flows', 'Corpactions']:
        if k in dfs.keys():
            dfs.pop(k)

    for k, v in dfs.items():
        if len(v) == 0:
            continue

        if k in ['Account']:
            continue
        elif 'Trades' in k:
            v['Invest'] = v['Quantity'] * v['T._Price']
            v.drop(['Сумма', 'P/L по закрытым сделкам'], axis=1, inplace=True)
        elif k in ['Fifo']:
            v.loc[(v['Comment'] == 'SWAP') & v['Symbol'].str.contains('/'), 'Country'] = 'US'

            v['PnL_KZT'] = v['PnL'] * v['KZT']
            v['OnlyProfit'] = np.where(v['PnL'] > 0, v['PnL'], 0)
            v['OnlyProfit'] = np.where(
                v['Country'].isin(offshores['ISO2']),
                v['Exit_Price'] * v['Quantity'],
                v['OnlyProfit']
            )
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax_KZT'] = v['OnlyProfit_KZT'] * 0.1

            for col in ['OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']:
                mask = (v['Exchange'].isin(['AIX', 'KASE', 'ITS'])) & (v['Comment'] == 'Bond Full Call')
                v.loc[mask, col] = 0

        elif k in ['Coupons']:
            v['Amount_KZT'] = v['Amount'] * v['KZT']
            v['OnlyProfit'] = np.where(v['Amount'] > 0, v['Amount'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax_KZT'] = v['OnlyProfit_KZT'] * 0.1
            for col in ['OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']:
                mask = (v['Country'] == 'KZ')
                v.loc[mask, col] = 0

        elif k in ['Dividends']:
            v['Amount_KZT'] = v['Amount'] * v['KZT']
            v['OnlyProfit'] = np.where(v['Amount'] > 0, v['Amount'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['OP_KZcorrect'] = np.where(v['IsliquidKASE'] == 1, v['OnlyProfit_KZT'], 0)
            v['Withhold_KZT'] = v['Withhold'] * v['KZT']
            v['Tax'] = v['Amount'] * 0.1
            v['Tax_KZT'] = v['Amount_KZT'] * 0.1
            v['Tax_KZT_Withhold'] = np.where(
                v['Withhold_KZT'].abs() > v['Tax_KZT'],
                0,
                v['Tax_KZT'] - v['Withhold_KZT'].abs()
            )
            for col in ['OnlyProfit', 'OnlyProfit_KZT', 'Tax', 'Tax_KZT', 'Tax_KZT_Withhold']:
                mask = (v['Exchange'].isin(['AIX', 'KASE', 'ITS']) & (v['IsliquidKASE'] == 1)) | (v['Country'] == 'KZ')
                v.loc[mask, col] = 0

        elif k in ['SpinOff_Redemp']:
            v['Amount_KZT'] = v['Amount'] * v['KZT']
            v['OnlyProfit'] = np.where(v['Amount'] > 0, v['Amount'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax'] = v['Amount'] * 0.1
            v['Tax_KZT'] = v['Amount_KZT'] * 0.1

        dfs[k] = v

    years_results_dict = {}
    idx_names = ['Year', 'Country', 'Currency']
    idx_names_kase = ['Year', 'Exchange', 'Country', 'Currency']
    if 'Fifo' in dfs.keys():
        swap_df = dfs['Fifo'][dfs['Fifo']['Comment'] == 'SWAP']
        repo_df = dfs['Fifo'][dfs['Fifo']['Comment'] == 'REPO']
        bond_df = dfs['Fifo'][dfs['Fifo']['Comment'] == 'Bond Full Call']
        trades_df = dfs['Fifo'][~dfs['Fifo'].index.isin(swap_df.index.union(repo_df.index).union(bond_df.index))]

        if len(swap_df) > 0:
            isin_swap = swap_df.groupby(
                    [swap_df['Exit_Date'].dt.year, swap_df['Country'], swap_df['Currency']]
                )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
                .sum() \
                .rename_axis(idx_names)
            years_results_dict['Yearly Swaps'] = isin_swap
        if len(repo_df) > 0:
            isin_repo = repo_df.groupby(
                    [repo_df['Exit_Date'].dt.year, repo_df['Country'], repo_df['Currency']]
                )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
                .sum() \
                .rename_axis(idx_names)
            years_results_dict['Yearly Repo'] = isin_repo
        if len(bond_df) > 0:
            isin_bonds = bond_df.groupby(
                    [bond_df['Exit_Date'].dt.year, bond_df['Exchange'], bond_df['Country'], bond_df['Currency']]
                )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
                .sum() \
                .rename_axis(idx_names_kase)
            years_results_dict['Yearly Corp Actions'] = isin_bonds
        if len(trades_df) > 0:
            isin_trades = trades_df.groupby(
                    [trades_df['Exit_Date'].dt.year, trades_df['Exchange'], trades_df['Country'], trades_df['Currency']]
                )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
                .sum() \
                .rename_axis(idx_names_kase)
            years_results_dict['Yearly Trades'] = isin_trades
    if 'Dividends' in dfs.keys():
        cur_df = dfs['Dividends']
        isin_divs = cur_df.groupby([cur_df['Date_Time'].dt.year, cur_df['Country'], cur_df['Currency']])[[
                'Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'OP_KZcorrect','Withhold_KZT', 'Tax_KZT',
                'Tax_KZT_Withhold'
            ]].sum()\
            .rename_axis(idx_names)
        years_results_dict['Yearly Dividends'] = isin_divs

    if 'Coupons' in dfs.keys():
        cur_df = dfs['Coupons']
        isin_coupons = cur_df.groupby(
                [cur_df['Date_Time'].dt.year, cur_df['Exchange'],cur_df['Country'], cur_df['Currency']]
            )[['Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
            .sum() \
            .rename_axis(idx_names_kase)
        years_results_dict['Yearly Coupons'] = isin_coupons

    if 'SpinOff_Redemp' in dfs.keys():
        cur_df = dfs['SpinOff_Redemp']
        isin_other = cur_df.groupby(
                [cur_df['Date_Time'].dt.year,cur_df['Country'], cur_df['Currency']]
            )[['Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
            .sum() \
            .rename_axis(idx_names)
        years_results_dict['Yearly SpinOff_Redemp'] = isin_other

    dfs['Years_Results'] = years_results_dict

    return dfs


def excel_writer(dfs: dict, file_name: str) -> None:
    with pd.ExcelWriter(f'postProcessed/Freedom_{file_name}.xlsx', engine='openpyxl') as writer:
        for name, df in dfs.items():
            if isinstance(df, pd.DataFrame):
                df.to_excel(writer, sheet_name=name, index=False)
            elif isinstance(df, dict):
                pd.DataFrame().to_excel(writer, sheet_name=name, index=False)
                ws = writer.sheets[name]
                current_row = 1

                for sub_name, sub_df in df.items():
                    n_cols = sub_df.reset_index().shape[1]
                    ws.merge_cells(start_row=current_row, end_row=current_row, start_column=1, end_column=n_cols)
                    cell = ws.cell(row=current_row, column=1)
                    cell.value = sub_name
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    current_row += 1

                    sub_df.to_excel(writer, sheet_name=name, startrow=current_row - 1, index=True, header=True)

                    current_row += len(sub_df) + 3
            else:
                raise Exception(f"Invalid object type for '{name}': expected DataFrame or dict of DataFrames.")

    return None


if __name__ == "__main__":
    account: str = 'D1463345'  # 7F8339, 8A0627

    max_year = 0
    total_dict = read_files(account)
    dfs = prep_data(total_dict)
    dfs = fifo_calc(dfs)
    dfs = transfers_out(dfs, account)
    dfs = add_currency(dfs)
    dfs = final_preparations(dfs)
    excel_writer(dfs, account)
