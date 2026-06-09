import os
import csv
import pandas as pd
import numpy as np
import re

from stdnum import isin
from colorama import init, Fore, Style
from collections import defaultdict
from openpyxl.styles import Alignment, Font
from pprint import pprint


# Settings
pd.options.display.max_rows = 1_500
pd.options.display.max_columns = 500
pd.set_option('display.width', 1400)
pd.options.display.float_format = '{:.6f}'.format


def time_converter(cur_col: pd.Series) -> pd.Series:
    cur_col = cur_col.astype(str).str.replace(',', '', regex=False).str.strip()

    return pd.to_datetime(cur_col, format='mixed')


def prep_fi_df(df):
    # CUSIP -> ISIN
    df['CUSIP'] = np.where(df['Security ID'].str.len() == 12, df['Security ID'].str[2:11], df['Security ID'])
    df['Country'] = np.where(df['Security ID'].str.len() == 12, df['Security ID'].str[0:2], '')
    only_countries = df[~pd.isna(df['Country'])][['CUSIP', 'Country']]\
        .sort_values(['CUSIP', 'Country'], ascending=[0, 0])\
        .drop_duplicates(['CUSIP'])
    df = df.merge(only_countries, on='CUSIP', how='left', suffixes=('_x', '')).drop(columns=['Country_x'])

    df['Security ID'] = np.where(
        df['Security ID'].str.len() == 9,
        df['Country'] + df['Security ID'],
        df['Security ID']
    )
    df['Security ID'] += df['Security ID'].map(
        lambda s: isin.calc_check_digit(s) if (type(s) is not float) and (len(s) == 11) else ''
    )
    df = df.sort_values(['Underlying', 'Symbol', 'Year', 'Security ID'], ascending=[1, 1, 0, 1])\
        .drop_duplicates(['Symbol', 'Year', 'CUSIP', 'Description'])\
        .sort_values(['Asset Category', 'Underlying', 'Symbol', 'Year', 'Security ID'], ascending=[1, 1, 1, 0, 1])\
        .reset_index(drop=True)

    duplicates = df[df.duplicated(['Symbol', 'Year'], keep=False)]
    assert len(duplicates) == 0, f"Financial instrument table has duplicates in Symbol and Year columns\n{duplicates}"

    # Bonds with same ISIN and Symbol but different Description
    isin_symbol_dup = df[df.duplicated(['Symbol', 'Security ID'], keep=False) & (df['Asset Category'] == 'Bonds')]
    isin_symbol_dup = isin_symbol_dup[isin_symbol_dup['Symbol'] != isin_symbol_dup['Description']]
    df.loc[isin_symbol_dup.index, 'Symbol'] = df['Description']

    # Country correction
    us_opt_exchanges = ['CBOE', 'ISE']
    us_futures = 'GC|ZC|ZW|ZC|CL|ES'
    df['Country'] = np.where(
        (
            (df['Asset Category'].str.contains('ption') & df['Listing Exch'].isin(us_opt_exchanges)) |
            (df['Asset Category'].str.contains('uture') & df['Description'].str.contains(us_futures))
        ),
        'US',
        df['Country']
    )
    df['Country'] = np.where(df['Country'] == '', 'None', df['Country'])
    df['Country'] = df['Country'].str.replace('XS', 'BE')

    # Types
    df['Multiplier'] = df['Multiplier'].str.replace(',', '').astype(float)

    # Split data
    mask = df['Symbol'].str.contains(',') & ~df['Symbol'].str.contains('OLD')
    new_rows = df[mask].assign(Symbol=df.loc[mask, 'Symbol'].str.split(r'\s*,\s*')).explode('Symbol', ignore_index=True)
    df = pd.concat([df, new_rows], ignore_index=True, sort=False)
    duplicates = df[df.duplicated(['Symbol', 'Year'], keep=False)]
    assert len(duplicates) == 0, f"Financial instrument table has duplicates in Symbol and Year columns\n{duplicates}"

    return df


def prep_ca_df(df):
    df['Date/Time'] = time_converter(df['Date/Time'])
    df['Year'] = df['Date/Time'].dt.year
    df['Isin'] = df['Description'].str.extract(r'(?<!\w)([A-Z]{2}[A-Z0-9]{10})(?!\w)')
    df[['Quantity', 'Proceeds']] = df[['Quantity', 'Proceeds']].astype(float)
    df['exit_price'] = (df['Proceeds'] / df['Quantity']).abs()

    df['Country'] = np.where(
        df['Asset Category'].str.contains('ption'),
        'US',
        df['Isin'].str[:2]
    )
    df['Country'] = df['Country'].str.replace('XS', 'BE')

    df.columns = df.columns.str.strip().str.replace(" ", "_").str.replace("/", "_")

    return df


def prep_interest_df(df, total_fi: pd.DataFrame):
    df['Date_Time'] = pd.to_datetime(df['Date'])
    df['Amount'] = df['Amount'].astype(float)
    pattern = r'([A-Z]{1,10}(?: [\d./]+){1,2} \d{2}/\d{2}/\d{2})'
    df['Symbol'] = df['Description'].str.extract(pattern)

    mask = df['Description'].str.contains('Debit Interest|Credit Interest|SYEP|Borrow Fees|Short Stock Interest')
    df.loc[mask, 'Symbol'] = df['Description'].str.split(' for ', expand=True)[0]

    check_na = df[pd.isna(df['Symbol'])]
    assert len(check_na) == 0, f"Interest table has Nan values in the Symbol column\n{check_na}"

    df['Year'] = pd.to_datetime(df['Date']).dt.year
    df = pd.merge(
        df,
        total_fi[['Symbol', 'Security ID', 'Year', 'Country']].drop_duplicates(),
        on=['Symbol', 'Year'],
        how='left'
    ).merge(
        total_fi[['Description', 'Security ID', 'Year', 'Country']].rename(columns={'Description': 'Symbol'}),
        on=['Symbol', 'Year'],
        how='left'
    )
    df['Security ID_x'] = df['Security ID_x'].fillna(df['Security ID_y'])
    df['Country_x'] = df['Country_x'].fillna(df['Country_y'])

    df.drop(['Security ID_y', 'Date', 'Country_y'], axis=1, inplace=True)
    df.rename(columns={'Security ID_x': 'Isin', 'Country_x': 'Country'}, inplace=True)
    df['Isin'] = df['Isin'].fillna(df['Currency'].str[:2])
    df['Country'] = df['Country'].fillna(df['Isin'].str[:2])

    return df


def prep_divs_and_tax_df(df, total_fi: pd.DataFrame):
    df['Symbol'] = df['Description'].str.extract(r'^([^\(]+)\(')
    df['Isin'] = df['Description'].str.extract(r'\(([^)]+)\)')
    df['Dividend_type'] = df['Description'].str.extract(r'\(([^)]+)\)\s*$')
    df['Year'] = pd.to_datetime(df['Date']).dt.year

    no_data = df[pd.isna(df['Symbol']) & pd.isna(df['Isin']) & df['Description'].str.lower().str.contains('ithhold') &
                 df['Description'].str.lower().str.contains('credit interest')]
    if len(no_data) > 0:
        df = df.drop(no_data.index).reset_index(drop=True)

    wrong_isin_mask = ~df['Isin'].str.match(r'^[A-Z]{2}[A-Z0-9]{10}$', na=False)
    wrong_isin = pd.merge(
        df[wrong_isin_mask],
        total_fi[['Security ID', 'Symbol', 'Year']].drop_duplicates(),
        how='left',
        on=['Symbol', 'Year']
    )

    df = df.merge(wrong_isin[['Date', 'Symbol', 'Security ID']], how='left', on=['Date', 'Symbol'])
    df.loc[wrong_isin_mask, 'Isin'] = df.loc[wrong_isin_mask, 'Security ID']

    df['Date_Time'] = pd.to_datetime(df['Date'])
    df['Amount'] = df['Amount'].astype(float)
    df.drop(['Security ID', 'Date'], axis=1, inplace=True)

    wrong_isin = df[~df['Isin'].str.match(r'^[A-Z]{2}[A-Z0-9]{10}$', na=False)]
    assert len(wrong_isin) == 0, f"Some ISIN's are wrong in the dividend df\n{wrong_isin}"

    df['Country'] = df['Isin'].str[:2].str.replace('XS', 'BE')

    return df


def merge_divs_and_withhold(total_dividends, total_tax) -> pd.DataFrame:
    group_cols = ['Dividends', 'Header', 'Currency', 'Date_Time', 'Symbol', 'Isin', 'Country']
    total_dividends = total_dividends.groupby(group_cols)\
        ['Amount'].sum()\
        .reset_index()

    total_tax.drop('Dividend_type', axis=1, inplace=True)
    total_tax = total_tax.groupby(['Date_Time', 'Isin', 'Currency'])\
        ['Amount'].sum().rename('Withhold')\
        .reset_index()

    total_dividends = total_dividends.merge(total_tax, on=['Date_Time', 'Isin', 'Currency'], how='left')
    total_dividends['Withhold'] = total_dividends['Withhold'].fillna(0)

    total_dividends['TaxLoad'] = (total_dividends['Withhold'] / total_dividends['Amount']).abs()
    total_dividends['IsliquidKASE'] = 0

    return total_dividends


def prep_transfers_df(df, total_fi: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(['Symbol', 'Date'])
    df['Qty'] = df['Qty'].str.replace(',', '').astype(float)
    df['Date'] = pd.to_datetime(df['Date'])
    df['Year'] = df['Date'].dt.year

    tt_group = df.groupby(['Date', 'Symbol'])['Qty'].sum()
    tt_group = tt_group[tt_group == 0].reset_index()[['Date', 'Symbol']]

    df = pd.merge(df, tt_group, on=['Date', 'Symbol'], how='left', indicator=True)
    df = df[df['_merge'] == 'left_only'].drop(columns=['_merge'])
    df = df[~((df['Direction'] == 'Out') & (df['Qty'] > 0))]
    df = df.drop_duplicates(['Symbol', 'Date', 'Qty']).sort_values('Date')

    if df['Symbol'].str.contains('-').any():
        expand = df['Symbol'].str.split(' - ', expand=True)
        df['Symbol'] = expand[0]
        df['Description'] = expand[1]

    cur_fi = total_fi[['Symbol', 'Security ID', 'Year', 'Country']].rename(columns={'Security ID': 'Isin'})
    df = df.merge(cur_fi, how='left', on=['Symbol', 'Year'])

    return df


def prep_positions_df(df: pd.DataFrame):
    group_cols = ['Asset', 'Symbol', 'Isin', 'Currency', 'Country', 'Date_Time', 'Type', 'Price', 'Year']
    df['Date_Time'] = df['Date_Time'].dt.date
    df['Group_Qty'] = df.groupby(group_cols)['Qty'].transform('sum')
    df['group_comm'] = df['Qty'] / df['Group_Qty'] * df['Comm_per_one']
    df = df.groupby(group_cols)[['Qty', 'group_comm']]\
        .sum()\
        .reset_index()\
        .rename(columns={'group_comm': 'Comm_per_one'})

    df['RegCountry'] = df['Country']
    df = df.sort_values(['Year', 'Date_Time'])

    return df


def prepare_trades_df(account: str) -> tuple:
    global max_year

    # IB reports parser
    total_trades = {}
    total_fi = pd.DataFrame()
    total_ca = pd.DataFrame()
    total_interest = pd.DataFrame()
    total_dividends = pd.DataFrame()
    total_tax = pd.DataFrame()
    total_transfers = pd.DataFrame()

    for f in os.listdir('files'):
        if f.startswith(account + '_'):
            cur_year = int(f.split('_')[1][:4])
            with open(f'files/{f}', encoding='utf-8', newline='') as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and (row[0].strip() == 'Trades'):
                        if row[1].strip() == 'Header':
                            cur_data = []
                            cur_header = row

                        elif row[1].strip() == 'Data':
                            cur_data.append(row)

                        elif row[1].strip() == 'Total':
                            cur_asset = row[3].strip() + f"_{cur_year}"
                            cur_df = pd.DataFrame(cur_data, columns=cur_header)

                            cur_df[['Symbol', 'Yield']] = cur_df['Symbol'].str.extract(
                                r'^(.*?)(?:\s+(\d+(?:\.\d+)?%))?$'
                            )
                            cur_df['Date/Time'] = time_converter(cur_df['Date/Time'])

                            total_trades[cur_asset] = cur_df

                    elif row and (row[0].strip() == 'Financial Instrument Information'):
                        if row[1].strip() == 'Header':
                            if 'fi_header' not in locals():
                                fi_header = row
                                fi_header.append('Year')
                                list_fi = []
                            else:
                                cur_df = pd.DataFrame(columns=fi_header, data=list_fi)
                                total_fi = pd.concat([total_fi, cur_df], ignore_index=True, sort=False)

                                fi_header = row
                                fi_header.append('Year')
                                list_fi = []

                        elif row[1].strip() == 'Data':
                            row.append(cur_year)
                            list_fi.append(row)

                    elif row and (row[0].strip() == 'Corporate Actions'):
                        if row[1].strip() == 'Header':
                            if 'corp_header' not in locals():
                                corp_header = row
                                list_corp = []
                            else:
                                cur_df = pd.DataFrame(columns=corp_header, data=list_corp)
                                total_ca = pd.concat([total_ca, cur_df], ignore_index=True, sort=False)
                                corp_header = row
                                list_corp = []

                        elif (row[1].strip() == 'Data') and (row[2].strip() != 'Total'):
                            list_corp.append(row)

                    elif row and (row[0].strip() == 'Interest'):
                        if row[1].strip() == 'Header':
                            if 'interest_header' not in locals():
                                interest_header = row
                                list_interest = []
                            else:
                                cur_df = pd.DataFrame(columns=interest_header, data=list_interest)
                                total_interest = pd.concat([total_interest, cur_df], ignore_index=True, sort=False)
                                interest_header = row
                                list_interest = []

                        elif (row[1].strip() == 'Data') and ('Total' not in row[2].strip()):
                            list_interest.append(row)

                    elif row and (row[0].strip() == 'Dividends'):
                        if row[1].strip() == 'Header':
                            if 'div_header' not in locals():
                                div_header = row
                                list_divs = []
                            else:
                                cur_df = pd.DataFrame(columns=div_header, data=list_divs)
                                total_dividends = pd.concat([total_dividends, cur_df], ignore_index=True, sort=False)
                                div_header = row
                                list_divs = []

                        elif (row[1].strip() == 'Data') and ('Total' not in row[2].strip()):
                            list_divs.append(row)

                    elif row and (row[0].strip() == 'Withholding Tax'):
                        if row[1].strip() == 'Header':
                            if 'tax_header' not in locals():
                                tax_header = row
                                list_tax = []
                            else:
                                cur_df = pd.DataFrame(columns=tax_header, data=list_tax)
                                total_tax = pd.concat([total_tax, cur_df], ignore_index=True, sort=False)
                                tax_header = row
                                list_tax = []

                        elif (row[1].strip() == 'Data') and ('Total' not in row[2].strip()):
                            list_tax.append(row)

                    elif row and (row[0].strip() == 'Transfers'):
                        if row[1].strip() == 'Header':
                            if 'trans_header' not in locals():
                                trans_header = row
                                list_trans = []
                            else:
                                cur_df = pd.DataFrame(columns=trans_header, data=list_trans)
                                total_transfers = pd.concat([total_transfers, cur_df], ignore_index=True, sort=False)
                                trans_header = row
                                list_trans = []

                        elif (row[1].strip() == 'Data') and ('Total' not in row[2].strip()):
                            list_trans.append(row)

                    elif row and (row[0] == 'Statement') and (row[2] == 'Period'):
                        max_year = max(int(row[3][-4:]), max_year)
    dfs = {}

    # Financial instrument description
    cur_df = pd.DataFrame(columns=fi_header, data=list_fi)
    total_fi = pd.concat([total_fi, cur_df], ignore_index=True, sort=False)
    total_fi = prep_fi_df(total_fi)
    dfs['FinInfo'] = total_fi

    # Corporate actions
    if 'corp_header' in locals():
        cur_df = pd.DataFrame(columns=corp_header, data=list_corp)
        total_ca = pd.concat([total_ca, cur_df], ignore_index=True, sort=False)
        total_ca = prep_ca_df(total_ca)
        dfs['CorpActions'] = total_ca

    # Interest
    if 'interest_header' in locals():
        cur_df = pd.DataFrame(columns=interest_header, data=list_interest)
        total_interest = pd.concat([total_interest, cur_df], ignore_index=True, sort=False)
        total_interest = prep_interest_df(total_interest, total_fi)
        dfs['Interest'] = total_interest

    # Dividend & Withholding Tax
    if 'div_header' in locals():
        cur_df = pd.DataFrame(columns=div_header, data=list_divs)
        total_dividends = pd.concat([total_dividends, cur_df], ignore_index=True, sort=False)
        total_dividends = prep_divs_and_tax_df(total_dividends, total_fi)

        cur_df = pd.DataFrame(columns=tax_header, data=list_tax)
        total_tax = pd.concat([total_tax, cur_df], ignore_index=True, sort=False)
        total_tax = prep_divs_and_tax_df(total_tax, total_fi)

        total_dividends = merge_divs_and_withhold(total_dividends, total_tax)
        dfs['Dividend'] = total_dividends

    # Transfers
    if 'trans_header' in locals():
        cur_df = pd.DataFrame(columns=trans_header, data=list_trans)
        total_transfers = pd.concat([total_transfers, cur_df], ignore_index=True, sort=False)
        total_transfers = prep_transfers_df(total_transfers, total_fi)
        dfs['Transfers'] = total_transfers

    # Total Trades
    for k, v in total_trades.items():
        cur_df = total_trades[k]
        cur_df['Year'] = pd.to_datetime(cur_df['Date/Time']).dt.year
        cur_df['Quantity'] = cur_df['Quantity'].astype(str).str.replace(',', '')
        cur_df['T. Price'] = pd.to_numeric(cur_df['T. Price'], errors='coerce')
        float_cols = ['Quantity', 'Notional Value', 'Proceeds']
        for col in float_cols:
            if col in cur_df.columns:
                cur_df[col] = cur_df[col].astype(float)

        if ('Equity and Index Options' in k) or ('Options On Future' in k):
            if 'Equity and Index Options' in k:
                cur_df['Symbol'] = cur_df['Symbol'].str.replace('USO 15JAN21', 'USO1 15JAN21')
                cur_df['Symbol'] = cur_df['Symbol'].str.replace('VXX 18JUN21', 'VXX1 18JUN21')

            cur_df = pd.merge(
                cur_df,
                total_fi[['Description', 'Security ID', 'Country', 'Year', 'Multiplier']].drop_duplicates(),
                left_on=['Symbol', 'Year'],
                right_on=['Description', 'Year'],
                how='left'
            )
            if 'Equity and Index Options' in k:
                mult = (cur_df['Proceeds'] / cur_df['Quantity'] / cur_df['T. Price']).abs()
                if (mult < 1).any():
                    raise Exception(f'{mult} less than 1. Need to change logic')
                cur_df['Multiplier'] = cur_df['Multiplier'].fillna(round(mult, 0))
            cur_df['Security ID'] = cur_df['Symbol']
            cur_df['Quantity'] *= cur_df['Multiplier']
        else:
            cur_df = pd.merge(
                cur_df,
                total_fi[['Symbol', 'Security ID', 'Country', 'Year', 'Multiplier']].drop_duplicates(),
                on=['Symbol', 'Year'],
                how='left'
            )
            if 'Futures' in k:
                cur_df['Quantity'] *= cur_df['Multiplier']
                cur_df['Security ID'] = cur_df['Symbol']
            elif ('Bond' in k) or ('Bill' in k):
                mult = (cur_df['Proceeds'] / cur_df['Quantity'] / cur_df['T. Price']).abs()
                cur_df['T. Price'] *= mult

        cur_df.drop(columns=['Year'], inplace=True)
        total_trades[k] = cur_df

    grouped = defaultdict(list)
    for key, df in total_trades.items():
        group_name = key.split('_')[0]
        grouped[group_name].append(df)
    combined_dfs = {group: pd.concat(dfs, ignore_index=True) for group, dfs in grouped.items()}

    return combined_dfs, dfs


def transfers_in(combined_dfs, dfs: dict, account: str):
    if 'Transfers' not in dfs.keys():
        return combined_dfs

    trans_in = dfs['Transfers'][dfs['Transfers']['Direction'] == 'In']
    if len(trans_in) == 0:
        return combined_dfs

    print(f"{Fore.BLUE}"
          f"У вас имеются акции, которые поступили путём перевода между брокерскими счетами."
          f"{Style.RESET_ALL}")
    for idx, row in trans_in.iterrows():
        print(f"{row['Date']} {row['Isin']} {row['Symbol']} количество {row['Qty']}")
    print(f"{Fore.BLUE}"
          f"Отправьте excel/csv файл, с информацией о дате и цене приобретения данных ценных бумаг. "
          f"Имя колонки[пример данных] - "
          f"symbol[TSLA],isin_code[US7496552057],currency[RUB],t_date[2024-03-20T11:35:37],t_q[10],t_price[1384.40]"
          f"{Style.RESET_ALL}")

    trans_data = pd.read_excel(f'files/transfers_to_IB_{account}.xlsx', parse_dates=['t_date'])

    # Check
    trans_data_group = trans_data.groupby('symbol')['t_q'].sum()
    check = trans_in.merge(trans_data_group, left_on='Symbol', right_on='symbol', how='left')

    dont_have_isins = check[pd.isna(check['t_q']) | pd.isna(check['Qty'])]
    if len(dont_have_isins) > 0:
        print(dont_have_isins)
        raise Exception('Под данным тикерам нет данных')

    check['diff'] = check['t_q'] - check['Qty']
    wrong_number = check[check['diff'] != 0]
    if len(wrong_number) > 0:
        print(wrong_number)
        raise Exception('Под данным тикерам предоставлено неверное суммарное количество бумаг')

    # Concat with Trades
    trans_data = trans_data[['symbol', 'isin_code', 'currency', 't_date', 't_q', 't_price']]
    trans_in = trans_in.merge(
        trans_data,
        left_on=['Symbol', 'Currency'], right_on=['symbol', 'currency'],
        how='left'
    ).drop(['symbol', 'currency'], axis=1)
    trans_in = trans_in.merge(dfs['FinInfo'][['Symbol', 'Year','Multiplier']], how='left', on=['Symbol', 'Year'])\
        .merge(
        dfs['FinInfo'][['Description', 'Year','Multiplier']],
        how='left',
        left_on=['Symbol', 'Year'],
        right_on=['Description', 'Year'],
        suffixes=('', '_y')
    )
    trans_in['Multiplier'] = trans_in['Multiplier'].fillna(trans_in['Multiplier_y'])
    trans_in.drop(columns=['Multiplier_y', 'Description'], inplace=True)

    trans_in['t_q'] *= trans_in['Multiplier']
    trans_in['Proceeds'] = trans_in['t_q'] * trans_in['t_price']
    trans_in['Isin'] = trans_in['Isin'].fillna(trans_in['Symbol'])

    for cur_asset in trans_in['Asset Category'].unique():
        cur_df = trans_in[trans_in['Asset Category'] == cur_asset]
        if cur_asset not in combined_dfs.keys():
            main_df = pd.DataFrame(
                columns=['Trades', 'Header', 'DataDiscriminator', 'Asset Category', 'Currency', 'Symbol', 'Date/Time',
                         'Quantity', 'T. Price', 'C. Price', 'Proceeds', 'Comm/Fee', 'Basis', 'Realized P/L', 'MTM P/L',
                         'Code','Yield', 'Security ID'],
                data=None,
                index=range(len(cur_df))
            )
            combined_dfs[cur_asset] = pd.DataFrame()
        else:
            main_df = pd.DataFrame(columns=combined_dfs[cur_asset].columns, index=range(len(cur_df)), data=None)

        main_df['Trades'] = 'Trades'
        main_df['Header'] = 'Data'
        main_df['DataDiscriminator'] = 'Order'
        main_df['Asset Category'] = cur_asset

        main_df['Currency'] = cur_df['Currency'].values
        main_df['Symbol'] = cur_df['Symbol'].values
        main_df['Date/Time'] = pd.to_datetime(cur_df['Date'].values)
        main_df['Quantity'] = cur_df['t_q'].values
        main_df['T. Price'] = cur_df['t_price'].values
        main_df['C. Price'] = 0
        main_df['Proceeds'] = cur_df['Proceeds'].values
        main_df['Security ID'] = cur_df['Isin'].values
        main_df['Code'] = cur_df['Code'].values
        for col in ['Comm/Fee', 'Basis',  'Realized P/L', 'MTM P/L', 'Yield']:
            main_df[col] = 0
        main_df['Comment'] = 'TransferIn'

        combined_dfs[cur_asset] = pd.concat([combined_dfs[cur_asset], main_df], ignore_index=True)

    return combined_dfs


def fifo_calc(combined_dfs: dict, dfs: dict) -> dict:
    global max_year
    total_trades = pd.DataFrame()
    total_df_fifo = pd.DataFrame()
    total_positions = []

    for name, df in combined_dfs.items():


        if name in ['Equity and Index Options', 'Options On Futures']:
            print(f'Alex.Ash report skips {name}')
            continue


        if name == 'Forex':
            print(f"{Fore.MAGENTA}Forex trades have not been processed{Style.RESET_ALL}")
            continue

        df.columns = df.columns.str.strip().str.replace(" ", "_").str.replace("/", "_")
        df['Comm_Fee'] = pd.to_numeric(df['Comm_Fee'], errors='coerce').fillna(0)
        df['Comm_Per_One'] = df['Comm_Fee'] / df['Quantity'].abs()

        df = df.sort_values(['Security_ID', 'Date_Time'])
        total_trades = pd.concat([total_trades, df])

        results = []
        for isin, group in df.groupby('Security_ID'):
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
                                    'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency,
                                    'Country': country,
                                    'Date_Time': date, 'Type': 'покупка', 'Qty': buy_qty, 'Price': price,
                                    'Comm_per_one': comm_per_one
                                })
                                buy_qty = 0
                    else:
                        longs.append({
                            'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
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
                                    'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency,
                                    'Country': country,
                                    'Date_Time': date, 'Type': 'продажа', 'Qty': sell_qty, 'Price': price,
                                    'Comm_per_one': comm_per_one
                                })
                                sell_qty = 0

                    else:
                        shorts.append({
                            'Asset': asset, 'Symbol': symb, 'Isin': isin, 'Currency': currency, 'Country': country,
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
        total_df_fifo = pd.concat([total_df_fifo, df_fifo], ignore_index=True)

    if len(total_df_fifo) != 0:
        total_df_fifo['Comment'] = ''
        dfs[f'Fifo'] = total_df_fifo

    if 'Comment' not in total_trades.columns:
        total_trades['Comment'] = None
    dfs['Trades'] = total_trades

    dfs['Positions'] = prep_positions_df(pd.concat(total_positions, ignore_index=True))

    return dfs


def corporate_actions(combined_dfs, dfs) -> dict:
    if 'CorpActions' not in dfs.keys():
        return combined_dfs

    check_duplicates = dfs['CorpActions'].groupby(['Date_Time', 'Description'])[['Quantity']].sum().reset_index()
    check_duplicates = check_duplicates[check_duplicates['Quantity'] == 0].copy()
    dfs['CorpActions'] = dfs['CorpActions'].merge(
        check_duplicates,
        on=['Date_Time', 'Description'],
        how='outer',
        suffixes=('', '_y')
    )
    dfs['CorpActions'] = dfs['CorpActions'][dfs['CorpActions']['Quantity_y'] != 0].drop(columns=['Quantity_y'])

    del_indexes = []
    for main_idx, row in dfs['CorpActions'].iterrows():
        cur_asset = row['Asset_Category']
        if cur_asset == 'Stocks':
            if 'Spinoff' in row['Description']:
                pattern = r'(?<!\w)([A-Z]{2}[A-Z0-9]{10})(?!\w)'
                new_isin = re.findall(pattern, row['Description'])[1]
                mask = (dfs['FinInfo']['Security ID'] == new_isin) & (dfs['FinInfo']['Year'] == row['Year'])
                isin_info = dfs['FinInfo'][mask]
                assert len(isin_info) == 1, f"{isin_info} double isins"

                cur_df = combined_dfs[cur_asset].iloc[:1].copy()
                idx = cur_df.index[0]
                cur_df.loc[idx, 'Currency'] = row['Currency']
                cur_df.loc[idx, 'Symbol'] = isin_info['Symbol'].iloc[0]
                cur_df.loc[idx, 'Date/Time'] = row['Date_Time']
                cur_df.loc[idx, 'Quantity'] = row['Quantity']
                cur_df.loc[idx, 'Code'] = row['Code']
                cur_df.loc[idx, 'Yield'] = None
                cur_df.loc[idx, 'Security ID'] = new_isin
                cur_df.loc[idx, 'Comment'] = 'Spinoff'
                for col in ['T. Price', 'C. Price', 'Proceeds', 'Comm/Fee', 'Basis', 'Realized P/L', 'MTM P/L']:
                    cur_df.loc[idx, col] = 0

                combined_dfs[cur_asset] = pd.concat([combined_dfs[cur_asset], cur_df], ignore_index=True)

                del_indexes.append(main_idx)

            elif (('Cash and Stock Merger (Acquisition)' in row['Description']) or
                  ('Merged(Mandatory Offer Allocation)' in row['Description'])):
                pattern = r'(\d+(?:\.\d+)?)\s+for\s+(\d+(?:\.\d+)?)'
                match = re.search(pattern, row['Description'])
                from_qty = float(match.group(1))
                to_qty = float(match.group(2))
                ratio = to_qty / from_qty

                pattern = r'(?<!\w)([A-Z]{2}[A-Z0-9]{10})(?!\w)'
                isins = re.findall(pattern, row['Description'])
                if (ratio == 1) and len(isins) == 1:
                    del_indexes.append(main_idx)
                    continue

                old_isin = isins[0]
                new_isin = isins[1]
                mask = (dfs['FinInfo']['Security ID'] == new_isin) & (dfs['FinInfo']['Year'] == row['Year'])
                isin_info = dfs['FinInfo'][mask]
                assert len(isin_info) == 1, f"{isin_info} double isins"

                main_df = combined_dfs[cur_asset]
                mask = (main_df['Security ID'] == old_isin) & (main_df['Date/Time'] < row['Date_Time'])
                main_df.loc[mask, 'Security ID'] = new_isin

                mask = (main_df['Security ID'] == new_isin) & (main_df['Date/Time'] < row['Date_Time'])
                main_df.loc[mask, 'Quantity'] = main_df['Quantity'] / ratio
                main_df.loc[mask, 'T. Price'] = main_df['T. Price'] * ratio
                main_df.loc[mask, 'Symbol'] = isin_info['Symbol'].iloc[0]
                main_df.loc[mask, 'Comment'] = f'Acquisition {old_isin}|{new_isin}'

                del_indexes.append(main_idx)

            elif 'Split' in row['Description']:
                if row['Quantity'] < 0:
                    del_indexes.append(main_idx)
                    continue

                pattern = r'(?<!\w)([A-Z]{2}[A-Z0-9]{10})(?!\w)'
                new_isin = re.findall(pattern, row['Description'])[1]

                pattern = r'(\d+(?:\.\d+)?)\s+for\s+(\d+(?:\.\d+)?)'
                match = re.search(pattern, row['Description'])
                from_qty = float(match.group(1))
                to_qty = float(match.group(2))
                ratio = to_qty / from_qty

                main_df = combined_dfs[cur_asset]
                mask = (main_df['Security ID'] == row['Isin']) & (main_df['Date/Time'] < row['Date_Time'])
                main_df.loc[mask, 'Security ID'] = new_isin

                mask = (main_df['Security ID'] == new_isin) & (main_df['Date/Time'] < row['Date_Time'])
                main_df.loc[mask, 'Quantity'] = main_df['Quantity'] / ratio
                main_df.loc[mask, 'T. Price'] = main_df['T. Price'] * ratio
                main_df.loc[mask, 'Comment'] = f'Split {ratio}'

                del_indexes.append(main_idx)

            elif 'Merged(Acquisition)' in row['Description']:
                main_df = combined_dfs[cur_asset]
                cur_df = main_df[main_df['Security ID'] == row['Isin']].iloc[:1].copy()
                idx = cur_df.index[0]

                cur_df.loc[idx, 'Date/Time'] = row['Date_Time']
                cur_df.loc[idx, 'Quantity'] = row['Quantity']
                cur_df.loc[idx, 'T. Price'] = row['exit_price']
                cur_df.loc[idx, 'Comment'] = 'Acquisition'
                cur_df.loc[idx, 'Basis'] = row['exit_price'] * row['Quantity']
                for col in ['C. Price', 'Proceeds', 'Comm/Fee', 'Realized P/L', 'MTM P/L', 'Code', 'Yield']:
                    cur_df.loc[idx, col] = None

                combined_dfs[cur_asset] = pd.concat([main_df, cur_df], ignore_index=True, sort=False)
                del_indexes.append(main_idx)

        elif cur_asset in ['Bonds', 'Treasury Bills']:
            if (('Full Call' in row['Description']) or ('Bill Maturity' in row['Description']) or
                    ('Bond Maturity' in row['Description'])):
                main_df = combined_dfs[cur_asset]
                cur_df = main_df[main_df['Security ID'] == row['Isin']].iloc[:1].copy()
                idx = cur_df.index[0]

                cur_df.loc[idx, 'Date/Time'] = row['Date_Time']
                cur_df.loc[idx, 'Quantity'] = row['Quantity']
                cur_df.loc[idx, 'T. Price'] = row['exit_price']
                cur_df.loc[idx, 'C. Price'] = row['exit_price']
                cur_df.loc[idx, 'Proceeds'] = row['Proceeds']
                cur_df.loc[idx, 'Realized P/L'] = row['Realized_P_L']
                cur_df.loc[idx, 'Code'] = row['Code']
                for col in ['Comm/Fee', 'Basis', 'MTM P/L']:
                    cur_df.loc[idx, col] = 0
                cur_df.loc[idx, 'Comment'] = 'Bond Full Call'

                combined_dfs[cur_asset] = pd.concat([combined_dfs[cur_asset], cur_df], ignore_index=True)

                del_indexes.append(main_idx)

        elif cur_asset in ['Equity and Index Options']:
            if 'Split' in row['Description']:
                pattern = r'(\d+(?:\.\d+)?)\s+for\s+(\d+(?:\.\d+)?)'
                match = re.search(pattern, row['Description'])
                from_qty = float(match.group(1))
                to_qty = float(match.group(2))
                ratio = to_qty / from_qty

                new_symbol = re.search(r',\s*([^,]+?)\s*,', row['Description']).group(1)
                old_symbol = re.sub(
                    r'^(?P<ticker>\S+)\s+(?P<date>\d{2}[A-Z]{3}\d{2})\s+(?P<strike>\d+(?:\.\d+)?)\s+(?P<cp>[CP])$',
                    lambda m: f"{m['ticker']} {m['date']} {float(m['strike'])/ratio:.1f} {m['cp']}",
                    new_symbol
                )

                main_df = combined_dfs[cur_asset]
                mask = (main_df['Symbol'] == old_symbol) & (main_df['Date/Time'] < row['Date_Time'])
                main_df.loc[mask, 'Symbol'] = new_symbol
                main_df.loc[mask, 'Security ID'] = new_symbol
                main_df.loc[mask, 'Quantity'] = main_df['Quantity'] / ratio
                main_df.loc[mask, 'T. Price'] = main_df['T. Price'] * ratio
                main_df.loc[mask, 'Comment'] = f'Split {ratio} {old_symbol}'

                del_indexes.append(main_idx)

    unprocessed_corp = dfs['CorpActions'].drop(del_indexes)
    assert len(unprocessed_corp) == 0, f"Some CorpActions have not been processed.\n{unprocessed_corp}"

    return combined_dfs


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
        isin = transfer['Isin']
        qty_to_remove = -transfer['Qty']
        trans_date = transfer['Date']
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
                removed_rows.append(row)
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
    removed_df.to_excel(f'files/transfers_from_IB_{account}.xlsx', index=False)

    dfs['Positions'] = pos_df[pos_df['Qty'] != 0].sort_values(['Year', 'Asset', 'Isin', 'Date_Time'])

    return dfs


def add_currency(dfs: dict) -> dict:
    currency_df = pd.read_excel('Currency.xlsx')
    currency_df['Date'] = pd.to_datetime(currency_df['Date'], format='%d.%m.%Y')
    values = [col for col in currency_df.columns if ('quant' not in col) and ('Date' not in col)]
    currency_df = currency_df.melt(
        id_vars=['Date'],
        value_vars=values,
        var_name='Currency',
        value_name='KZT'
    )

    for k, v in dfs.items():
        if len(v) == 0:
            continue

        if k in ['Trades', 'Interest', 'Dividend', 'CorpActions']:
            v['Date'] = pd.to_datetime(v['Date_Time'].dt.date)
        elif k in ['Fifo']:
            v['Date'] = pd.to_datetime(v['Exit_Date'].dt.date)
        elif k in ['Positions']:
            v['Date'] = pd.to_datetime(v['Date_Time'])
        else:
            print(f"{Fore.BLUE}{k} - doesn't require currency data{Style.RESET_ALL}")
            continue

        v = v.merge(currency_df, on=['Date', 'Currency'], how='left')
        dfs[k] = v.drop(['Date'], axis=1)

    return dfs


def final_preparations(dfs) -> dict:
    offshores = pd.read_excel('Offshores_iso2.xlsx')

    for k, v in dfs.items():
        if len(v) == 0:
            continue

        if 'Trades' in k:
            v['Invest'] = v['Quantity'] * v['T._Price']
            v['Country'] = v['Security_ID'].str[:2]
            v.loc[v['Asset_Category'].str.contains('ption'), 'Country'] = 'US'
            v.drop(['C._Price', 'Proceeds', 'Realized_P_L', 'MTM_P_L', 'Code'], axis=1, inplace=True)
        elif k in ['Fifo']:
            v['PnL_KZT'] = v['PnL'] * v['KZT']
            v['OnlyProfit'] = np.where(v['PnL'] > 0, v['PnL'], 0)
            v['OnlyProfit'] = np.where(
                v['Country'].isin(offshores['ISO2']),
                v['Exit_Price'] * v['Quantity'],
                v['OnlyProfit']
            )
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax_KZT'] = v['OnlyProfit_KZT'] * 0.1

            if 'CorpActions' in dfs.keys():
                cur_corp = dfs['CorpActions'][['Asset_Category', 'Currency', 'Date_Time', 'Description', 'Isin',
                                               'exit_price']]
                mask = cur_corp['Description'].str.contains('Full Call')
                cur_corp.loc[mask, 'Description'] = 'Bond Full Call'
                mask = cur_corp['Description'].str.contains('Treasury Bill Maturity')
                cur_corp.loc[mask, 'Description'] = 'Bond Full Call'

                v = v.merge(
                    cur_corp,
                    how='left',
                    left_on=['Asset', 'Currency', 'Exit_Date', 'Isin', 'Exit_Price'],
                    right_on=['Asset_Category', 'Currency', 'Date_Time', 'Isin', 'exit_price']
                ).drop(['Asset_Category', 'Date_Time', 'exit_price'], axis=1)
            else:
                v['Description'] = None

        elif k in ['Interest']:
            v['Amount_KZT'] = v['Amount'] * v['KZT']
            v['OnlyProfit'] = np.where(v['Amount'] > 0, v['Amount'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax_KZT'] = v['OnlyProfit_KZT'] * 0.1
        elif k in ['Dividend']:
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

        dfs[k] = v

    years_results_dict = {}
    idx_names = ['Year', 'Country', 'Currency']
    if 'Fifo' in dfs.keys():
        mask = dfs['Fifo']['Description'] == 'Bond Full Call'
        fifo_trades = dfs['Fifo'][~mask]
        fifo_corp = dfs['Fifo'][mask]
        check = dfs['Fifo'].drop(fifo_trades.index.union(fifo_corp.index))
        if len(check) > 0:
            print(check)
            raise Exception(f"Fifo df has unprocessed actions.")

        # Fifo yearly results
        if len(fifo_trades) > 0:
            isin_fifo = fifo_trades.groupby(
                    [fifo_trades['Exit_Date'].dt.year, fifo_trades['Country'], fifo_trades['Currency']]
                )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']]\
                .sum() \
                .rename_axis(idx_names)
            years_results_dict['Yearly Trades'] = isin_fifo

        # Corp actions yearly results
        if len(fifo_corp) > 0:
            isin_corp = fifo_corp.groupby(
                    [fifo_corp['Exit_Date'].dt.year, fifo_corp['Country'], fifo_corp['Currency']]
                )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']]\
                .sum() \
                .rename_axis(idx_names)
            years_results_dict['Yearly Corp Actions'] = isin_corp

    if 'Dividend' in dfs.keys():
        isin_divs = dfs['Dividend'].groupby(
            [dfs['Dividend']['Date_Time'].dt.year, dfs['Dividend']['Country'], dfs['Dividend']['Currency']]
            )[[
                'Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'OP_KZcorrect', 'Withhold_KZT', 'Tax_KZT',
                'Tax_KZT_Withhold'
            ]].sum()\
            .rename_axis(idx_names)
        years_results_dict['Yearly Dividends'] = isin_divs

    if 'Interest' in dfs.keys():
        # Deposits yearly results
        deposits = dfs['Interest'][dfs['Interest']['Isin'].str.len() == 2]
        if len(deposits) > 0:
            isin_deposits = deposits.groupby([deposits['Date_Time'].dt.year, deposits['Country'], deposits['Currency']])\
                [['Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']]\
                .sum()\
                .rename_axis(idx_names)
            years_results_dict['Yearly Deposits'] = isin_deposits
            dfs['Deposits'] = deposits

        # Coupons yearly results
        coupons = dfs['Interest'][dfs['Interest']['Isin'].str.len() > 2]
        if len(coupons) > 0:
            isin_coupons = coupons.groupby([coupons['Date_Time'].dt.year, coupons['Country'], coupons['Currency']])\
                [['Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']]\
                .sum()\
                .rename_axis(idx_names)
            years_results_dict['Yearly Coupons'] = isin_coupons
            dfs['Coupons'] = coupons

        check_interest = dfs['Interest'].drop(deposits.index.union(coupons.index))
        if len(check_interest) > 0:
            print(check_interest)
            raise Exception(f"dfs['Interest'] has non proceeded rows")
        dfs.pop('Interest')

    dfs['Years_Results'] = years_results_dict

    return dfs


def excel_writer(dfs: dict, file_name: str) -> None:
    with pd.ExcelWriter(f'postProcessed/IB_{file_name}.xlsx', engine='openpyxl') as writer:
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
    account: str = 'U1717377'  # U5927652
    max_year = 0

    combined_dfs, dfs = prepare_trades_df(account)
    combined_dfs = transfers_in(combined_dfs, dfs, account)
    combined_dfs = corporate_actions(combined_dfs, dfs)
    dfs = fifo_calc(combined_dfs, dfs)
    dfs = transfers_out(dfs, account)
    dfs = add_currency(dfs)
    dfs = final_preparations(dfs)
    excel_writer(dfs, account)
