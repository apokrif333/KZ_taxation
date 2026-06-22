import datetime
import os
import csv
import pandas as pd
import numpy as np
import re
import urllib.request
import urllib.parse
import json
import pdfplumber

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


def prep_trades_df(df):
    df = df[~df.duplicated()].sort_values(['Дата', 'Время']).reset_index(drop=True)
    df['Количество'] = np.where(df['Операция'] == 'Продажа', -df['Количество'], df['Количество'])
    df['Сумма'] = df['Цена'] * df['Количество']
    df['Date_Time'] = pd.to_datetime(df['Дата'].astype(str) + ' ' + df['Время'].astype(str))
    df.drop(columns=['Дата', 'Время', 'Комментарий'], inplace=True)
    df.rename(
        columns={'Цена': 'T._Price', 'Количество': 'Quantity', 'Сумма': 'Invest', 'Валюта': 'Currency',
                 'ISIN': 'Security_ID'},
        inplace=True
    )
    df['Comment'] = None
    df['Asset_Category'] = 'Equity'
    df['Country'] = df['Security_ID'].str[:2].str.replace('XS', 'BE')

    return df


def prep_positions_df(df):
    group_cols = ['Asset', 'Isin', 'Currency', 'Date_Time', 'Type', 'Price', 'Year']
    df['Date_Time'] = df['Date_Time'].dt.date
    df['Group_Qty'] = df.groupby(group_cols)['Qty'].transform('sum')
    df = df.groupby(group_cols)[['Qty']].sum().reset_index()

    df = df.sort_values(['Year', 'Isin', 'Date_Time'])
    df['Country'] = df['Isin'].str[:2].str.replace('XS', 'BE')
    df['RegCountry'] = df['Country']

    return df


def load_data(account: str) -> dict:
    dfs = {'Trades': pd.DataFrame()}
    for f in os.listdir('files'):
        if account in f and f.endswith('pdf'):
            with pdfplumber.open(f"files/{f}") as pdf:

                isin = ''
                sec_type = ''
                t_rows = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if not text:
                        continue

                    lines = [line.strip() for line in text.split('\n')]

                    current_row = {}
                    for i, line in enumerate(lines):
                        match_isin = re.match('ISIN', line.replace(' ', ''))
                        if match_isin:
                            isin = line.split(' ')[1]
                        match_type = re.match('Вид ценной бумаги', line)
                        if match_type:
                            sec_type = line.split(': ')[1]

                        match = re.match(r'^B1-\d{4}-', line.replace(' ', ''))
                        if match:
                            if current_row:
                                t_rows.append(current_row)
                                current_row = {}

                            current_row['Номер сделки'] = match.group(0)

                            try:
                                parts = lines[i + 1].split()
                                current_row['Дата'] = parts[0]
                                current_row['Время'] = parts[1]
                                current_row['Операция'] = parts[2]
                                current_row['Цена'] = parts[3]
                                current_row['Количество'] = parts[4]
                                current_row['Сумма'] = parts[5]
                                current_row['Комментарий'] = parts[6] if len(parts) > 6 else ''
                            except Exception as e:

                                print(f"Ошибка обработки строки: {lines[i + 1]}")
                                raise Exception(e)

                    if current_row:
                        t_rows.append(current_row)

            df = pd.DataFrame(t_rows)
            df['Дата'] = pd.to_datetime(df['Дата'], dayfirst=True)
            df['Время'] = pd.to_datetime(df['Время'], format='%H:%M:%S').dt.time
            df['Цена'] = df['Цена'].str.replace('$', '').astype(float)
            df['Количество'] = df['Количество'].str.replace(',', '').astype(int)
            df['Валюта'] = np.where(df['Сумма'].str.contains('$'), 'USD', '')
            df['Сумма'] = df['Сумма'].str.replace('$', '').astype(float)
            df['ISIN'] = isin
            df['Тип'] = sec_type
            dfs['Trades'] = pd.concat([dfs['Trades'], df], ignore_index=True, sort=False)

    dfs['Trades'] = prep_trades_df(dfs['Trades'])

    return dfs


def fifo_calc(dfs):
    df = dfs['Trades'].sort_values(['Security_ID', 'Date_Time'])
    max_year = max(df['Date_Time'].dt.year)

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
            currency = row['Currency']
            qty = row['Quantity']
            date = row['Date_Time']
            price = row['T._Price']

            if qty > 0:
                if shorts:
                    buy_qty = qty
                    while buy_qty > 0 and shorts:
                        short = shorts[0]
                        matched_qty = min(buy_qty, short['Qty'])
                        result = {
                            'Asset': asset, 'Isin': isin, 'Currency': currency,
                            'Position_Type': 'short',
                            'Enter_Date': short['Date_Time'], 'Enter_Price': short['Price'],
                            'Exit_Date': date, 'Exit_Price': price,
                            'Quantity': matched_qty,
                            'PnL': (short['Price'] - price) * matched_qty
                        }
                        results.append(result)

                        short['Qty'] -= matched_qty
                        buy_qty -= matched_qty
                        if short['Qty'] == 0:
                            shorts.pop(0)
                else:
                    longs.append({
                        'Asset': asset, 'Isin': isin, 'Currency': currency,
                        'Date_Time': date, 'Type': 'покупка', 'Qty': qty, 'Price': price,
                    })

            else:
                sell_qty = -qty
                if longs:
                    while sell_qty > 0 and longs:
                        buy = longs[0]
                        matched_qty = min(sell_qty, buy['Qty'])

                        result = {
                            'Asset': asset, 'Isin': isin, 'Currency': currency,
                            'Position_Type': 'long',
                            'Enter_Date': buy['Date_Time'], 'Enter_Price': buy['Price'],
                            'Exit_Date': date, 'Exit_Price': price,
                            'Quantity': matched_qty,
                            'PnL': (price - buy['Price']) * matched_qty
                        }
                        results.append(result)

                        buy['Qty'] -= matched_qty
                        sell_qty -= matched_qty

                        if buy['Qty'] == 0:
                            longs.pop(0)

                else:
                    shorts.append({
                        'Asset': asset, 'Isin': isin, 'Currency': currency,
                        'Date_Time': date, 'Type': 'продажа', 'Qty': sell_qty, 'Price': price,
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
        df_fifo['Country'] = df_fifo['Isin'].str[:2].str.replace('XS', 'BE')
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

        if k in ['Trades']:
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
        dfs[k]['KZT'] = np.where(dfs[k]['Currency'] == 'KZT', 1, dfs[k]['KZT'])

    return dfs


def final_preparations(dfs):
    for k, v in dfs.items():
        if len(v) == 0:
            continue

        if 'Trades' in k:
            v['Invest'] = v['Quantity'] * v['T._Price']
        elif k in ['Fifo']:
            v['PnL_KZT'] = v['PnL'] * v['KZT']
            v['OnlyProfit'] = np.where(v['PnL'] > 0, v['PnL'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['Tax_KZT'] = v['OnlyProfit_KZT'] * 0.1

        dfs[k] = v

    years_results_dict = {}
    idx_names = ['Year', 'Country', 'Currency']
    if 'Fifo' in dfs.keys():
        cur_df = dfs['Fifo']
        isin_trades = cur_df.groupby(
                [cur_df['Exit_Date'].dt.year, cur_df['Country'], cur_df['Currency']]
            )[['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']] \
            .sum() \
            .rename_axis(idx_names)
        years_results_dict['Yearly Trades'] = isin_trades

    dfs['Years_Results'] = years_results_dict

    return dfs


def excel_writer(dfs: dict, file_name: str) -> None:
    with pd.ExcelWriter(f'postProcessed/FBank_{file_name}.xlsx', engine='openpyxl') as writer:
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


if __name__ == '__main__':
    account = '870806301595'
    dfs = load_data(account)
    dfs = fifo_calc(dfs)
    dfs = add_currency(dfs)
    dfs = final_preparations(dfs)
    dfs = excel_writer(dfs, account)
