import datetime
import os
import csv
import pandas as pd
import numpy as np
import re
import urllib.request
import urllib.parse
import json

from colorama import init, Fore, Style
from collections import defaultdict
from openpyxl.styles import Alignment, Font
from pprint import pprint


# Settings
pd.options.display.max_rows = 1_500
pd.options.display.max_columns = 500
pd.set_option('display.width', 1400)
pd.options.display.float_format = '{:.6f}'.format


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


def asset_type_replacer(series: pd.Series):
    replacer_dict = {
        'Comdty': 'Commodity',
        'Corp': 'Corporate Bond', 'Облигации': 'Corporate Bond',
        'Curncy': 'Currency', 'Forex': 'Currency',
        'Акции': 'Equity', 'Депозитарные расписки': 'Equity', 'Фонды': 'Equity', 'STOCK': 'Equity',
        'Govt': 'Government Bond',
        'M-Mkt': 'Money Market',
        'Mtge': 'Mortgage-Backed Security',
        'OPTION': 'Option',
        'FUTURE': 'Future'
    }
    series = series.replace(replacer_dict)

    return series


def openfigi_finder(figi: str):
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
        {"idType": "ID_BB_GLOBAL", "idValue": figi},
    ]
    print("Making a mapping request:", mapping_request)
    response = api_call("/v3/mapping", mapping_request)

    return response


def recalc_months_extractor(s: pd.Series) -> np.array:
    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
    }
    extracted = s.str.extract(r'TY(\d{4}) (\w+)-(\w+)')
    extracted.columns = ['Year', 'StartMonth', 'EndMonth']
    extracted['StartMonthNum'] = extracted['StartMonth'].map(month_map)
    extracted['EndMonthNum'] = extracted['EndMonth'].map(month_map)

    extracted['StartRecalc'] = pd.to_datetime(
        extracted['Year'] + '-' + extracted['StartMonthNum'].astype(str).str.zfill(2) + '-01'
    )
    extracted['EndRecalc'] = pd.to_datetime(
        extracted['Year'] + '-' + extracted['EndMonthNum'].astype(str).str.zfill(2) + '-01'
    ) + pd.DateOffset(months=1)

    return extracted[['StartRecalc', 'EndRecalc']].values


def load_data(account: str) -> dict:
    global max_year
    dfs = {'Transactions': pd.DataFrame(), 'FinInfo': pd.DataFrame(), 'Cash': pd.DataFrame(), 'Trades': pd.DataFrame()}

    cur_key = None
    for f in os.listdir('files'):
        if account in f and ('transfers' not in f):
            cur_year = int(f[-8:-4])
            max_year = max(cur_year, max_year)

            with open(f'files/{f}', encoding='UTF-16', newline='') as file:
                lines = file.readlines()
                lines_len = len(lines)

                for i, line in enumerate(lines):
                    row = [cell.strip('"') for cell in line.strip().split('\t')]

                    if cur_key is not None:
                        if (row[0] == '') or (i + 1 == lines_len) or (row[0] == 'Transaction ID'):
                            if i + 1 == lines_len:
                                cur_df.append(row)

                            cur_df = pd.DataFrame(columns=cur_df[0], data=cur_df[1:])
                            if cur_key in ['Cash']:
                                cur_df['Year'] = cur_year

                            dfs[cur_key] = pd.concat([dfs[cur_key], cur_df], ignore_index=True, sort=False)
                            cur_key = None

                    if 'Stocks & ETFs' in row[0]:
                        cur_key = 'FinInfo'
                        cur_df = []
                        continue
                    elif 'Transaction ID' in row[0]:
                        cur_key = 'Transactions'
                        cur_df = []
                    elif ('Cash Balance' in row[0]) and (f'{cur_year}-12-31' in row[0]):
                        cur_key = 'Cash'
                        cur_df = []
                        continue
                    elif row[0] == 'Time' and row[-1] == 'Exchange Order ID':
                        cur_key = 'Trades'
                        cur_df = []

                    if cur_key is not None:
                        cur_df.append(row)

    return dfs


def prep_fininfo_df(dfs) -> dict:
    db_fininfo = pd.read_csv('files/ExanteFinInfo.csv')

    main_df = dfs['FinInfo'].rename(columns={'Financial Instrument Global Identifier (FIGI)': 'FIGI'})\
        .drop_duplicates('FIGI')\
        [['Instrument', 'FIGI', 'Currency', 'ISIN']]
    main_df['Country'] = main_df['ISIN'].str[:2].str.replace('XS', 'BE')
    main_df[['Symbol', 'Exchange']] = main_df['Instrument'].str.split('.', expand=True)
    main_df.drop(columns=['Instrument'], inplace=True)
    main_df = main_df.merge(db_fininfo[['FIGI', 'Asset_Category']], how='left', on='FIGI')

    for idx, row in main_df[pd.isna(main_df['Asset_Category'])].iterrows():
        asset_type = []
        answ = openfigi_finder(row['FIGI'])[0]
        for v in answ['data']:
            asset_type.append(v['marketSector'])

        asset_type = set(asset_type)
        if len(asset_type) > 1:
            print(row)
            raise Exception(f"OpenFigi has returned different marketSectors - {asset_type}")
        main_df.loc[idx, 'Asset_Category'] = list(asset_type)[0]
    main_df['Asset_Category'] = asset_type_replacer(main_df['Asset_Category'])

    db_fininfo_new = pd.concat([db_fininfo, main_df.dropna()]).drop_duplicates()
    if len(db_fininfo_new) > len(db_fininfo):
        db_fininfo_new.to_csv('files/ExanteFinInfo.csv', index=False)

    # Add info from trades
    df_trades = dfs['Trades'][['Currency', 'Security_ID',  'Symbol', 'Exchange', 'Asset_Category']]\
        .drop_duplicates()\
        .reset_index(drop=True)\
        .rename(columns={'Security_ID': 'ISIN'})
    df_trades['Asset_Category'] = asset_type_replacer(df_trades['Asset_Category'])
    df_trades['ISIN'] = np.where(df_trades['ISIN'] == 'None', None, df_trades['ISIN'])
    df_trades['Country'] = df_trades['ISIN'].str[:2].str.replace('XS', 'BE')
    df_trades['Country'] = np.where(
        df_trades['Exchange'].str.contains('CME') | df_trades['Exchange'].str.contains('CBOE'),
        'US',
        df_trades['Country']
    )
    df_trades['ISIN'] = np.where(
        pd.isna(df_trades['ISIN']),
        df_trades['Symbol'] + '.' + df_trades['Exchange'],
        df_trades['ISIN']
    )
    df_trades['Country'] = np.where(
        df_trades['Asset_Category'].isin(['FX_SPOT', 'FOREX', 'CFD']) | df_trades['Exchange'].isin(['EXANTE']),
        'CY',
        np.where(
            df_trades['Exchange'].str.contains('NYMEX|COMEX|CBOT|CME|CBOE'),
            'US',
            df_trades['Country']
        )
    )
    main_df = main_df.merge(
        df_trades, how='outer', on=['Currency', 'ISIN', 'Country', 'Symbol', 'Exchange', 'Asset_Category']
    )
    dfs['FinInfo'] = main_df

    return dfs


def add_fininfo_data(dfs):
    fi_df = dfs['FinInfo']
    fi_df['Symbol ID'] = fi_df['Symbol'] + '.' + fi_df['Exchange']

    if 'Trades' in dfs.keys():
        cur_len = len(dfs['Trades'])
        dfs['Trades'] = dfs['Trades'].merge(
            fi_df[['Currency', 'ISIN', 'Country', 'Symbol', 'Exchange']],
            left_on=['Currency', 'Symbol', 'Exchange'],
            right_on=['Currency', 'Symbol', 'Exchange'],
            how='left'
        )
        dfs['Trades']['Security_ID'] = np.where(
            dfs['Trades']['Security_ID'] == 'None',
            dfs['Trades']['ISIN'],
            dfs['Trades']['Security_ID']
        )
        dfs['Trades'].drop(columns=['ISIN'], inplace=True)
        new_len = len(dfs['Trades'])
        assert cur_len == new_len, f"Trades df len changed from {cur_len} to {new_len}\n{dfs['Trades']}"

    if 'Dividends' in dfs.keys():
        cur_len = len(dfs['Dividends'])
        dfs['Dividends'] = dfs['Dividends'].merge(
            fi_df[['ISIN', 'Country', 'Symbol ID']],
            on=['Symbol ID'],
            how='left'
        )
        new_len = len(dfs['Dividends'])
        assert cur_len == new_len, f"Dividends df len changed from {cur_len} to {new_len}\n{dfs['Dividends']}"

    if 'Transfers' in dfs.keys():
        cur_len = len(dfs['Transfers'])
        dfs['Transfers'] = dfs['Transfers'].merge(
            fi_df[['Currency', 'ISIN', 'Country', 'Symbol ID']],
            on=['Symbol ID', 'ISIN'],
            how='left'
        )
        new_len = len(dfs['Transfers'])
        assert cur_len == new_len, f"Transfers df len changed from {cur_len} to {new_len}\n{dfs['Transfers']}"

    return dfs


def prep_trades_types(dfs):
    main_df = dfs['Trades']
    float_cols = ['Price', 'Quantity', 'Commission']

    main_df['Time'] = pd.to_datetime(main_df['Time'])
    main_df['Commission'] = np.where(main_df['Commission'] == 'None', 0, main_df['Commission'])
    main_df[float_cols] = main_df[float_cols].astype(float)

    return dfs


def prep_transactions_df(dfs):
    main_df = dfs['Transactions']
    main_df = main_df[main_df['Operation type'] != 'TRADE'].copy()
    main_df['Date_Time'] = pd.to_datetime(main_df['When'])
    main_df['Sum'] = main_df['Sum'].astype(float)
    main_df.drop(columns=['When'], inplace=True)

    # Transfers
    mask = (main_df['Operation type'] == 'SECURITY TRANSFER') | (main_df['Comment'] == 'Securities transfer')
    transfers = main_df[mask].copy()
    if len(transfers) > 0:
        transfers['Direction'] = np.where(transfers['Sum'] > 0, 'In', 'Out')
        transfers[['Symbol', 'Exchange']] = transfers['Symbol ID'].str.split('.', expand=True)
        drop_cols = ['Transaction ID', 'Account ID', 'Operation type', 'Asset', 'EUR equivalent', 'Comment', 'UUID',
                     'Parent UUID', 'Merchant Name']
        for col in drop_cols:
            if col in transfers.columns:
                transfers.drop(columns=[col], inplace=True)
        dfs['Transfers'] = transfers.rename(columns={'Sum': 'Qty'})

    # MoneyTrans
    main_df = main_df[~mask]
    mask = main_df['Operation type'].isin(['FUNDING/WITHDRAWAL', 'ELECTRONIC TRANSFER', 'AUTOCONVERSION', 'ROLLOVER'])
    money = main_df[mask]
    if len(money) > 0:
        dfs['MoneyTrans'] = money.rename(columns={'Asset': 'Currency'})

    # Commissions
    com_types = [
        'COMMISSION', 'ISSUANCE FEE', 'BANK CHARGE', 'FEE', 'MARKET DATA FEE', 'MANUAL CLOSE-OUT FEE',
        'PERFORMANCE FEE', 'EXCESS MARGIN FEE', 'BALANCE WRITE-OFF'
    ]
    main_df = main_df[~mask]
    mask = main_df['Operation type'].isin(com_types)
    commission = main_df[mask]
    if len(commission) > 0:
        dfs['Commissions'] = commission.rename(columns={'Asset': 'Currency'})

    # Deposit
    main_df = main_df[~mask]
    mask = main_df['Operation type'] == 'INTEREST'
    deposits = main_df[mask]
    if len(deposits) > 0:
        deposits = deposits.rename(columns={'Asset': 'Currency', 'Sum': 'Amount'})
        drop_cols = ['Transaction ID', 'Transaction ID', 'Symbol ID', 'ISIN', 'EUR equivalent', 'Comment', 'UUID',
                     'Parent UUID', 'Merchant Name', 'Account ID']
        for col in drop_cols:
            if col in deposits.columns:
                deposits.drop(columns=[col], inplace=True)
        deposits['Country'] = 'CY'
        dfs['Deposits'] = deposits

    # Dividends & Tax
    main_df = main_df[~mask]
    mask = main_df['Operation type'].isin(['DIVIDEND', 'US TAX', 'TAX'])
    div_tax = main_df[mask].copy()
    if len(div_tax) > 0:
        div_tax['Operation type'] = np.where(div_tax['Operation type'] == 'TAX', 'US TAX', div_tax['Operation type'])
        div_tax['Date_Time'] = div_tax['Date_Time'].dt.date
        div_tax['exDiv'] = div_tax['Comment'].str.extract(r'ExD (\d{4}-\d{2}-\d{2})')
        div_tax['payDiv'] = div_tax['Comment'].str.extract(r'PD (\d{4}-\d{2}-\d{2})')
        div_tax[['rollbackID', 'rollbackDate']] = div_tax['Comment'].str.extract(r'#(\d+)\s+(\d{4}-\d{2}-\d{2})')
        div_tax = div_tax[
                ~div_tax['Transaction ID'].isin(div_tax['rollbackID'].unique()) & pd.isna(div_tax['rollbackID'])
            ].drop(columns=['rollbackID', 'rollbackDate'])

        # Divs
        divs = div_tax[div_tax['Operation type'] == 'DIVIDEND'].copy()
        divs['Amount'] = divs['Comment'].str.extract(r"dividend [\w.]+ (?P<Dividend>[\d.]+) [A-Z]{3}.*?").astype(float)
        divs['Withhold'] = divs['Comment'].str.extract(r"tax (?P<Tax>-?[\d.]+) [A-Z]{3}").astype(float)

        recalc_mask = div_tax['Comment'].str.contains('Tax recalculation')
        recalc = div_tax[['Symbol ID', 'Sum', 'exDiv', 'payDiv']][recalc_mask].rename(columns={'Sum': 'Recalc'})\
            .groupby(['Symbol ID', 'exDiv', 'payDiv'])['Recalc'].sum().reset_index()
        divs = divs.merge(recalc, how='left', on=['Symbol ID', 'exDiv', 'payDiv'])
        divs['Recalc'] = divs['Recalc'].fillna(0)
        divs['Withhold'] += divs['Recalc']
        divs.drop(columns=['Recalc'], inplace=True)

        ty_recalc_mask = div_tax['Comment'].str.contains('TY') & div_tax['Comment'].str.contains('recalculation')
        ty_recalc = div_tax[ty_recalc_mask].copy()
        ty_recalc[['StartRecalc', 'EndRecalc']] = recalc_months_extractor(ty_recalc['Comment'])
        ty_recalc = ty_recalc[['Symbol ID', 'Sum', 'Asset', 'StartRecalc', 'EndRecalc']].rename(columns={'Sum': 'RecalcSum'})
        divs = divs.merge(ty_recalc, how='left', on=['Symbol ID', 'Asset'])
        divs = divs[
                ((divs['exDiv'] >= divs['StartRecalc']) & (divs['exDiv'] < divs['EndRecalc'])) |
                pd.isna(divs['RecalcSum'])
            ]
        divs['WithholdCumSum'] = divs.groupby(['Symbol ID', 'Asset', 'RecalcSum'])['Withhold'].transform('cumsum')
        divs['Withhold'] = np.where(divs['RecalcSum'] >= divs['WithholdCumSum'].abs(), 0, divs['Withhold'])
        divs['TaxLoad'] = (divs['Withhold'] / divs['Amount']).abs()
        divs['IsliquidKASE'] = 0

        drop_cols = ['Transaction ID', 'Transaction ID',  'Operation type', 'Sum', 'EUR equivalent', 'Account ID', 'ISIN',
                     'UUID', 'Parent UUID', 'Merchant Name']
        for col in drop_cols:
            if col in divs.columns:
                divs.drop(columns=[col], inplace=True)
        divs = divs.rename(columns={'Asset': 'Currency'})
        dfs['Dividends'] = divs

    # Options
    main_df = main_df[~mask]
    mask = main_df['Operation type'].isin(['EXERCISE'])
    options = main_df[mask]
    if len(options) > 0:
        dfs['Options'] = options

    # Splits
    main_df = main_df[~mask]
    mask = main_df['Operation type'].isin(['STOCK SPLIT'])
    split_df = main_df[mask].copy()
    if len(split_df) > 0:
        split_df.sort_values('Date_Time', inplace=True)
        split_df = split_df[split_df['Sum'] > 0].copy()
        split_df['Ratio'] = split_df['Comment'].str.extract(r'Stock split\s(\d+\sfor\s\d+)')[0]
        split_df['Ratio'] = split_df['Ratio'].str.split(' for ').apply(lambda x: float(x[0]) / float(x[1]))

        trades = dfs['Trades']
        for idx, row in split_df.iterrows():
            cur_mask = ((trades['ISIN'] == row['ISIN']) & (trades['Symbol ID'] == row['Symbol ID'])
                        & (trades['Time'] < row['Date_Time']))
            trades.loc[cur_mask, 'Price'] = trades['Price'] / row['Ratio']
            trades.loc[cur_mask, 'Quantity'] = trades['Quantity'] * row['Ratio']
            if 'Comment' not in trades.columns:
                trades['Comment'] = None
            comm_mask = cur_mask & pd.isna(trades['Comment'])
            trades.loc[comm_mask, 'Comment'] = f"Convert {row['Symbol ID']}/Split {row['Ratio']}"

    main_df = main_df[~mask]
    mask = main_df['Operation type'].isin(['CORPORATE ACTION'])
    rename_df = main_df[mask].copy()
    if len(rename_df) > 0:
        rename_df[['OldSymbol', 'NewSymbol']] = rename_df['Comment'].str.split(' -> ', expand=True)
        rename_df['Type'] = np.where(
            np.char.find(rename_df['Symbol ID'].to_numpy().astype(str),
                         rename_df['OldSymbol'].to_numpy().astype(str)) >= 0,
            'old',
            'new')
        rename_df = rename_df.pivot(
            index=['Comment', 'OldSymbol', 'NewSymbol'],
            columns='Type',
            values=['Symbol ID', 'ISIN', 'Sum', 'Asset', 'Date_Time']
        )
        rename_df.columns = ['_'.join(col).strip() for col in rename_df.columns]
        rename_df.reset_index(inplace=True)

        trades = dfs['Trades']
        for idx, row in rename_df.iterrows():
            old_mask = (trades['Symbol ID'] == row['Symbol ID_old']) & (trades['ISIN'] == row['ISIN_old'])\
                & (trades['Time'] < row['Date_Time_old'])
            trades.loc[old_mask, 'Symbol ID'] = row['Symbol ID_new']
            trades.loc[old_mask, 'ISIN'] = row['ISIN_new']
            if 'Comment' not in trades.columns:
                trades['Comment'] = None
            comm_mask = old_mask & pd.isna(trades['Comment'])
            trades.loc[comm_mask, 'Comment'] = f"Convert {row['OldSymbol']} to {row['NewSymbol']}"

    # Check
    main_df = main_df[~mask]
    assert len(main_df) == 0, f"Transactions df still has some rows\n{main_df}"

    dfs.pop('Transactions')
    return dfs


def prep_trades(dfs):
    main_df = dfs['Trades']
    main_df['Commission Currency'] = np.where(
        (main_df['Commission Currency'] == 'None') & (main_df['Commission'] == 0),
        main_df['Currency'],
        main_df['Commission Currency']
    )
    main_df['Quantity'] = np.where(main_df['Side'] == 'buy', main_df['Quantity'], -main_df['Quantity'])
    main_df['Asset_Category'] = asset_type_replacer(main_df['Type'])
    main_df['Comment'] = None
    mask = main_df['Commission Currency'] == main_df['Currency']
    assert mask.all(), f"Not all values match between 'Commission Currency' and 'Currency'\n{main_df[~mask]}"

    main_df.drop(
        columns=['Account ID', 'P&L', 'Traded Volume', 'Order Id', 'Order pos', 'Value Date', 'Side', 'Type',
                 'Unique Transaction Identifier (UTI)', 'Trade type', 'Exchange Order ID', 'Commission Currency'],
        errors='ignore',
        inplace=True
    )
    main_df.rename(
        columns={'Time': 'Date_Time', 'Symbol ID': 'Symbol', 'ISIN': 'Security_ID', 'Price': 'T._Price',
                 'Commission': 'Comm_Fee'},
        inplace=True
    )
    main_df[['Symbol', 'Exchange']] = main_df['Symbol'].str.split('.', n=1, expand=True)

    main_df['Quantity'] = np.where(
        (main_df['Asset_Category'] == 'Option') & main_df['Exchange'].str.contains('CBOE'),
        main_df['Quantity'] * 100,
        main_df['Quantity']
    )

    futures_data = {'MES': 5, 'CL': 1000, 'GC': 100, 'LO': 1000}
    futures_mask = (
            (main_df['Asset_Category'] == 'Future') |
            ((main_df['Asset_Category'] == 'Option') & main_df['Exchange'].str.contains('NYMEX'))
    )
    futures_df = main_df[futures_mask]
    if len(futures_df) > 0:
        cur_futures = futures_df['Symbol'].str[:2].unique()
        check_futures = set(cur_futures) - set(futures_data.keys())
        assert len(check_futures) == 0, f"futures_data doesn't have info about {check_futures}\n{futures_df}"

        for cur_fut in cur_futures:
            mask = futures_mask & main_df['Symbol'].str.contains(cur_fut)
            main_df.loc[mask, 'Quantity'] *= futures_data[cur_fut]

    dfs['Trades'] = main_df

    return dfs


def transfers_in(dfs, account):
    if 'Transfers' not in dfs.keys():
        return dfs

    trans_in = dfs['Transfers'][dfs['Transfers']['Direction'] == 'In']
    if len(trans_in) == 0:
        return dfs

    print(f"{Fore.BLUE}"
          f"У вас имеются акции, которые поступили путём перевода между брокерскими счетами."
          f"{Style.RESET_ALL}")
    for idx, row in trans_in.iterrows():
        print(f"{row['Date_Time']} {row['ISIN']} {row['Symbol ID']} количество {row['Qty']}")
    print(f"{Fore.BLUE}"
          f"Отправьте excel/csv файл, с информацией о дате отправки и цене приобретения данных ценных бумаг."
          f"Имя колонки[пример данных] - "
          f"isin_code[US7496552057],currency[RUB],t_date[2024-03-20T11:35:37],t_q[10],t_price[1384.40]"
          f"{Style.RESET_ALL}")
    trans_data = pd.read_excel(f'files/transfers_to_Exante_{account}.xlsx', parse_dates=['t_date'])

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
        .rename(columns={'symbol': 'Symbol',  't_q': 'Quantity', 't_price': 'T._Price', 'ISIN': 'Security_ID'})
    trans_data['Comment'] = 'TransferIn'


    dfs['Trades'] = pd.concat([dfs['Trades'], trans_data], sort=False, ignore_index=True)\
        .sort_values('Date_Time')\
        .reset_index(drop=True)

    return dfs


def prep_positions_df(df):
    group_cols = ['Asset', 'Symbol', 'Isin', 'Currency', 'Exchange', 'Country', 'Date_Time', 'Type', 'Price', 'Year']
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


def calc_forex_convesion(df_main, df_money):
    # Forex DF
    df_forex = df_main.copy()
    df_forex['Date_Time'] = pd.to_datetime(df_forex['Date_Time'].dt.date)
    df_forex['vsQnt'] = df_forex['Quantity'] * df_forex['T._Price']
    df_forex['vsQntComm'] = (df_forex['vsQnt'].abs() - df_forex['Comm_Fee']) * np.sign(df_forex['vsQnt'])
    df_forex['Quantity'] *= -1

    need_cols = ['Date_Time', 'Symbol', 'Security_ID', 'Currency', 'Asset_Category', 'Exchange', 'Country']
    df_forex = df_forex.groupby(need_cols)[['Quantity', 'Comm_Fee', 'vsQnt', 'vsQntComm']].sum().reset_index()
    df_forex['vsCurrency'] = df_forex['Symbol'].str.split('/', expand=True)[0]
    df_forex[['vsQnt', 'vsQntComm']] = df_forex[['vsQnt', 'vsQntComm']].astype(int)

    # Money df
    df_money = df_money[df_money['Operation type'] == 'AUTOCONVERSION'].sort_values('Date_Time').copy()
    df_money['conversion_rate'] = df_money['Comment'].str.extract(r'conversion rate=([\d.]+)')
    df_money['Sum'] = np.where(df_money['Currency'] != 'USD', df_money['Sum'].astype(int), df_money['Sum'])
    group_money = df_money.pivot_table(
        index=['Comment', 'Date_Time', 'conversion_rate'],
        columns='Currency',
        values='Sum',
        aggfunc='sum'
    ).reset_index() \
        .drop(columns=['Comment']) \
        .sort_values('Date_Time')

    # Connect
    for idx, row in df_forex.iterrows():
        cur_money = group_money[group_money['Date_Time'] > row['Date_Time']]

        con_rate = cur_money[cur_money[row['Currency']] == row['vsQnt']][['Date_Time', 'USD']]
        if len(con_rate) == 0:
            con_rate = cur_money[cur_money[row['Currency']] == row['vsQntComm']][['Date_Time', 'USD']]

        assert len(con_rate) == 1, f"conversation rate length is not equal one.\n{row}\n{con_rate}"
        df_forex.loc[idx, ['DateExit', 'USD']] = con_rate.iloc[0].values

        con_rate = cur_money[cur_money[row['vsCurrency']] == row['Quantity']][['Date_Time', 'USD']]
        assert len(con_rate) == 1, f"conversation rate length is not equal one.\n{row}\n{con_rate}"
        df_forex.loc[idx, ['DateExit', 'vsUSD']] = con_rate.iloc[0].values
    df_forex['result'] = df_forex['USD'] + df_forex['vsUSD']

    df_forex = df_forex.drop(columns=['Date_Time', 'vsQnt', 'vsQntComm', 'vsCurrency', 'USD', 'vsUSD']) \
        .rename(columns={'DateExit': 'Date_Time', 'exitPrice': 'T._Price'})
    df_forex['Comment'] = None
    df_forex[['Comm_Per_One', 'Comm_Fee']] = 0

    # Fifo
    df_main.rename()

    print(df_main)
    print(df_forex)
    raise Exception
    # df_forex = df_forex[df_main.columns]

    return df_forex


def fifo_calc(dfs) -> dict:
    global max_year

    df = dfs['Trades'].copy()
    df['Comm_Per_One'] = (df['Comm_Fee'] / df['Quantity'].abs()).fillna(0)

    # mask = df['Asset_Category'].isin(['FOREX'])
    # if len(df[mask]) != 0:
    #     new_trades = calc_forex_convesion(df[mask], dfs['MoneyTrans'])
    #     df = pd.concat([df, new_trades], ignore_index=True)
    df = df[~df['Asset_Category'].isin(['FOREX'])].sort_values(['Security_ID', 'Date_Time'])

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
            currency = row['Currency']
            country = row['Country']
            exch = row['Exchange']
            qty = row['Quantity']
            date = row['Date_Time']
            price = row['T._Price']
            comm_per_one = row['Comm_Per_One']

            if qty > 0:
                if shorts:
                    buy_qty = qty
                    while buy_qty > 0 and shorts:
                        short = shorts[0]
                        matched_qty = min(buy_qty, short['Qty'])
                        total_comm = matched_qty * short['Comm_per_one'] + matched_qty * comm_per_one
                        result = {
                            'Asset': asset,  'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
                            'Exchange': exch,
                            'Position_Type': 'short',
                            'Enter_Date': short['Date_Time'], 'Enter_Price': short['Price'],
                            'Exit_Date': date, 'Exit_Price': price,
                            'Quantity': matched_qty,
                            'Commission': total_comm,
                            'PnL': (short['Price'] - price) * matched_qty
                        }
                        results.append(result)

                        short['Qty'] = round(short['Qty'] - matched_qty, 8)
                        buy_qty = round(buy_qty - matched_qty, 8)
                        if short['Qty'] == 0:
                            shorts.pop(0)

                        if not shorts and buy_qty > 0:
                            longs.append({
                                'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
                                'Exchange': exch,
                                'Date_Time': date, 'Type': 'покупка', 'Qty': buy_qty, 'Price': price,
                                'Comm_per_one': comm_per_one
                            })
                            buy_qty = 0
                else:
                    longs.append({
                        'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
                        'Exchange': exch,
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
                            'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
                            'Exchange': exch,
                            'Position_Type': 'long',
                            'Enter_Date': buy['Date_Time'], 'Enter_Price': buy['Price'],
                            'Exit_Date': date, 'Exit_Price': price,
                            'Quantity': matched_qty,
                            'Commission': total_comm,
                            'PnL': (price - buy['Price']) * matched_qty
                        }
                        results.append(result)

                        buy['Qty'] = round(buy['Qty'] - matched_qty, 8)
                        sell_qty = round(sell_qty - matched_qty, 8)
                        if buy['Qty'] == 0:
                            longs.pop(0)

                        if not longs and sell_qty > 0:
                            shorts.append({
                                'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
                                'Exchange': exch,
                                'Date_Time': date, 'Type': 'продажа', 'Qty': sell_qty, 'Price': price,
                                'Comm_per_one': comm_per_one
                            })
                            sell_qty = 0
                else:
                    shorts.append({
                        'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
                        'Exchange': exch,
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
        df_fifo['Comment'] = None
        dfs[f'Fifo'] = df_fifo

    total_positions = pd.concat(total_positions, ignore_index=True)
    if len(total_positions) > 0:
        total_positions = prep_positions_df(total_positions)
        dfs['Positions'] = total_positions

    return dfs


def add_currency(dfs):
    currency_df = prep_currency_df()

    for k, v in dfs.items():
        if len(v) == 0:
            continue

        if k in ['Trades', 'MoneyTrans', 'Commissions', 'Deposits']:
            v['Date'] = pd.to_datetime(v['Date_Time'].dt.date)
        elif k in ['Fifo']:
            v['Date'] = pd.to_datetime(v['Exit_Date'].dt.date)
        elif k in ['Positions', 'Dividends']:
            v['Date'] = pd.to_datetime(v['Date_Time'])
        else:
            print(f"{Fore.BLUE}{k} - doesn't require currency data{Style.RESET_ALL}")
            continue

        v = v.merge(currency_df, on=['Date', 'Currency'], how='left')
        dfs[k] = v.drop(['Date'], axis=1)
        dfs[k]['KZT'] = np.where(dfs[k]['Currency'] == 'KZT', 1, dfs[k]['KZT'])

    return dfs


def final_preparations(dfs):
    offshores = pd.read_excel('Offshores_iso2.xlsx')

    for k, v in dfs.items():
        if len(v) == 0:
            continue

        if 'Trades' in k:
            v['Invest'] = v['Quantity'] * v['T._Price']
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

        elif k in ['Coupons']:
            v['Amount_KZT'] = v['Amount'] * v['KZT']
            v['OnlyProfit'] = np.where(v['Amount'] > 0, v['Amount'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax_KZT'] = v['OnlyProfit_KZT'] * 0.1
            for col in ['OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']:
                mask = (v['Country'] == 'KZ')
                v.loc[mask, col] = 0

        elif k in ['Dividends']:
            v['Date_Time'] = pd.to_datetime(v['Date_Time'])
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
                mask = (v['Country'] == 'KZ')
                v.loc[mask, col] = 0

        elif k in ['Deposits']:
            v['Amount_KZT'] = v['Amount'] * v['KZT']
            v['OnlyProfit'] = np.where(v['Amount'] > 0, v['Amount'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax_KZT'] = v['OnlyProfit_KZT'] * 0.1

        dfs[k] = v

    years_results_dict = {}
    idx_names = ['Year', 'Country', 'Currency']
    if 'Fifo' in dfs.keys():
        swap_df = dfs['Fifo'][dfs['Fifo']['Comment'] == 'SWAP']
        repo_df = dfs['Fifo'][dfs['Fifo']['Comment'] == 'REPO']
        trades_df = dfs['Fifo'][~dfs['Fifo'].index.isin(swap_df.index.union(repo_df.index))]

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
        if len(trades_df) > 0:
            isin_trades = trades_df.groupby(
                    [trades_df['Exit_Date'].dt.year, trades_df['Country'], trades_df['Currency']]
                )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
                .sum() \
                .rename_axis(idx_names)
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
        isin_coupons = cur_df.groupby([cur_df['Date_Time'].dt.year, cur_df['Country'], cur_df['Currency']]) \
            [['Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
            .sum() \
            .rename_axis(idx_names)
        years_results_dict['Yearly Coupons'] = isin_coupons

    if 'Deposits' in dfs.keys():
        # Deposits yearly results
        cur_df = dfs['Deposits']
        isin_deposits = cur_df.groupby([cur_df['Date_Time'].dt.year, cur_df['Country'], cur_df['Currency']])\
            [['Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']]\
            .sum()\
            .rename_axis(idx_names)
        years_results_dict['Yearly Deposits'] = isin_deposits

    dfs['Years_Results'] = years_results_dict

    return dfs


def excel_writer(dfs: dict, file_name: str) -> None:
    with pd.ExcelWriter(f'postProcessed/Exante_{file_name}.xlsx', engine='openpyxl') as writer:
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


def prep_dfs(dfs):
    dfs = prep_trades_types(dfs)
    dfs = prep_transactions_df(dfs)
    dfs = prep_trades(dfs)
    dfs = prep_fininfo_df(dfs)
    dfs = add_fininfo_data(dfs)
    dfs = transfers_in(dfs, account)

    return dfs


if __name__ == "__main__":
    account: str = 'PHE0351'  # HXR2208.001,  FXC3362.001, FLY3044.001
    max_year = 0

    dfs = load_data(account)
    dfs = prep_dfs(dfs)
    dfs = fifo_calc(dfs)
    dfs = add_currency(dfs)
    dfs = final_preparations(dfs)
    dfs = excel_writer(dfs, account)
