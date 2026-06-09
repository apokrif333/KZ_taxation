import xml.etree.ElementTree as ET
import os
import pandas as pd
import numpy as np

from colorama import init, Fore, Style
from openpyxl.styles import Alignment, Font
from pprint import pprint

# Settings
pd.options.display.max_rows = 1_500
pd.options.display.max_columns = 500
pd.set_option('display.width', 1400)
pd.options.display.float_format = '{:.6f}'.format


def element_to_string(element):
    return ET.tostring(element, encoding='unicode')


def data_parcer(root, tag: str) -> pd.DataFrame:
    search = root.find(tag)
    if search is None:
        # print(f"{Fore.MAGENTA}{tag} don't exist in the report{Style.RESET_ALL}")
        return pd.DataFrame()

    row_list = []
    if tag not in ['money_move', 'stock_income']:
        for row in search:
            row_list.append(row.attrib)
    else:
        for row in root.findall(tag):
            row_list.append(row.attrib)
    df = pd.DataFrame(row_list)

    return df


def money_total(root) -> pd.DataFrame:
    money_total = root.find("money_total")
    if money_total is None:
        print(f"{Fore.MAGENTA}money_total don't exist in the report{Style.RESET_ALL}")
        return pd.DataFrame()

    rows = [{"name": r.attrib["name"], "value": float(r.attrib["value"])} for r in money_total.findall("row")]
    currency = money_total.attrib
    for r in rows:
        r.update(currency)

    money_total_df = pd.DataFrame(rows)

    return money_total_df


def get_orders(root) -> tuple:
    # Orders
    orders_root = root.find('orders')
    repo_rows = []
    trade_rows = []
    order_rows = []
    for order in orders_root.findall('order'):
        order_attribs = order.attrib.copy()

        for repo in order.findall('repo'):
            row = order_attribs.copy()
            row.update(repo.attrib)
            repo_rows.append(row)

        for trade in order.findall('trade'):
            row = order_attribs.copy()
            row.update(trade.attrib)
            trade_rows.append(row)

        if not order.findall('repo') and not order.findall('trade'):
            row = order_attribs.copy()
            row['raw_xml'] = element_to_string(order)
            order_rows.append(row)

    repo_df = pd.DataFrame(repo_rows)
    trade_df = pd.DataFrame(trade_rows)
    order_df = pd.DataFrame(order_rows)
    if len(order_df) > 0:
        print(order_df)
        raise Exception(f"order_df has unprocessed events")

    return repo_df, trade_df


def get_full_data() -> dict:
    global max_year
    dfs = {
        'FinInfo': pd.DataFrame(), 'Income': pd.DataFrame(), 'Money': pd.DataFrame(), 'ActiveMoves': pd.DataFrame(),
        'Commissions': pd.DataFrame(), "Nalog": pd.DataFrame(), 'Trades': pd.DataFrame(),
    }

    for f in os.listdir('files'):
        if 'Цифра' not in f:
            continue
        print(f)

        tree = ET.parse(f'files/{f}')
        root = tree.getroot()
        rep_start, rep_end = pd.to_datetime(root.attrib['period'].split('-'), dayfirst=True).date
        max_year = max(max_year, rep_end.year)

        # combined_data = {**root.find('account').attrib, **root.find('money').attrib, **root.find('positions').attrib}
        # summary_df = pd.DataFrame([combined_data])

        # money_total_df = money_total(root)
        # money_debts_df = data_parcer(root, 'money_debts')
        # repo_df, trade_df = get_orders(root)
        position_df = data_parcer(root, 'positions')
        position_df = position_df[['code', 'numGosReg', 'isin', 'issuer', 'MicexСode', 'StockType', 'price_curr']]
        dfs['FinInfo'] = pd.concat([dfs['FinInfo'], position_df], sort=False, ignore_index=True)

        income_df = data_parcer(root, 'stock_income')
        dfs['Income'] = pd.concat([dfs['Income'], income_df], sort=False, ignore_index=True)

        money_df = data_parcer(root, 'money_move')
        dfs['Money'] = pd.concat([dfs['Money'], money_df], sort=False, ignore_index=True)

        active_moves_df = data_parcer(root, 'active_moves')
        dfs['ActiveMoves'] = pd.concat([dfs['ActiveMoves'], active_moves_df], sort=False, ignore_index=True)

        commiss_moves_df = data_parcer(root, 'commiss_moves')
        commiss_moves_df['rep_start'], commiss_moves_df['rep_end'] = rep_start, rep_end
        dfs['Commissions'] = pd.concat([dfs['Commissions'], commiss_moves_df], sort=False, ignore_index=True)

        nalog_df = data_parcer(root, 'nalog_moves')
        nalog_df['rep_start'], nalog_df['rep_end'] = rep_start, rep_end
        dfs['Nalog'] = pd.concat([dfs['Nalog'], nalog_df], sort=False, ignore_index=True)

        repo_df, trade_df = get_orders(root)
        dfs['Trades'] = pd.concat([dfs['Trades'], trade_df], sort=False, ignore_index=True)

    return dfs


def prepare_trades_df(dfs: dict) -> dict:
    for k, v in dfs.items():
        if k == 'FinInfo':
            v = v.drop_duplicates(subset=['code', 'numGosReg', 'isin', 'MicexСode', 'StockType', 'price_curr']).copy()
            v.loc[v['StockType'] == 'ао', 'Asset'] = 'Stocks'
            v.loc[v['StockType'] == 'ап', 'Asset'] = 'Stocks'
            v.loc[v['StockType'] == 'п', 'Asset'] = 'Stocks'
            v.loc[v['StockType'] == 'об', 'Asset'] = 'Bonds'
            if v['Asset'].isna().any():
                print(f"{Fore.RED}{v[v['Asset'].isna()]}\nFinInfo has nan values in Asset col. Replace nan by 'Stocks'"
                      f"{Style.RESET_ALL}")
                v['Asset'] = v['Asset'].fillna('Stocks')
        elif k == 'Income':
            v['operation_date'] = pd.to_datetime(v['operation_date'])
            v['operation_sum'] = v['operation_sum'].astype(float)

            currency_map = {'руб': 'RUB'}
            v[['currency', 'amount_per_share']] = v['comment'].str.extract(
                r"Размер выплаты на 1 цб\s*\(в\s*([\w.]+)\)\s*-\s*([\d.,]+)"
            )
            v['currency'] = v['currency'].str.lower().str.replace('\.', '', regex=True)
            unexisting_curr = [cur for cur in v['currency'].unique() if cur not in currency_map.keys()]
            if len(unexisting_curr) > 0:
                raise Exception(f"{unexisting_curr} - don't exist in currency_map")
            v['currency'] = v['currency'].map(currency_map)
            v['amount_per_share'] = v['amount_per_share'].astype(float)
        elif k == 'Money':
            for col in ['date', 'date_fix']:
                v[col] = pd.to_datetime(v[col])
            for col in ['in_qty', 'out_qty', 'quantity', 'price', 'tax_rate']:
                v[col] = v[col].astype(float)
            v = v.rename(columns={'currency_code': 'currency'})
        elif k == 'ActiveMoves':
            v['date'] = pd.to_datetime(v['date'], format='mixed')
            for col in ['in_qty', 'out_qty']:
                v[col] = v[col].astype(float)
        elif k in ['Commissions', 'Nalog']:
            for col in ['rep_start', 'rep_end']:
                v[col] = pd.to_datetime(v[col])
            for col in ['in_debt', 'plan_qty', 'trans_qty', 'out_debt']:
                v[col] = v[col].astype(float)
            v = v.rename(columns={'currency_code': 'currency'})
        elif k == 'Trades':
            date_cols = ['date', 't_date', 't_date_oplata', 't_date_postavka', 't_date_oplata_fact',
                         't_date_postavka_fact']
            for col in date_cols:
                v[col] = pd.to_datetime(v[col])
            for col in ['q', 'sum', 'price', 'comis', 'comis_nds', 'stock_comm', 't_q', 't_price', 't_sum']:
                v[col] = v[col].astype(float)

            v = v.merge(dfs['FinInfo'][['isin', 'code']], left_on='isin_code', right_on='isin', how='left') \
                .drop(['security_name', 'isin'], axis=1) \
                .rename(columns={'code_y': 'security_name', 'code_x': 'code'})
            v['Comment'] = None
        else:
            raise Exception(f'Unknown key from dfs dictionary - {k}')

        dfs[k] = v

    return dfs


def prep_positions_df(df, dfs):
    df = df.merge(dfs['FinInfo'][['isin', 'code']], on='isin', how='left')\
        .drop('symbol', axis=1)\
        .rename(columns={'code': 'symbol'})

    group_cols = ['symbol', 'isin', 'currency', 'date_Time', 'price', 'Year']
    df['date_Time'] = pd.to_datetime(df['date_Time']).dt.date
    df['group_qty'] = df.groupby(group_cols)['qty'].transform('sum')
    df['group_comm'] = df['qty'] / df['group_qty'] * df['comm_per_one']
    df = df.groupby(group_cols)[['qty', 'group_comm']] \
        .sum() \
        .reset_index() \
        .rename(columns={'group_comm': 'comm_per_one'})

    df = df.sort_values(['Year', 'isin', 'date_Time'])
    df['Comment'] = ''

    return df


def check_transfers(dfs: dict, trans_file_name: str) -> dict:
    if len(dfs['ActiveMoves']) == 0:
        return dfs

    # Load csv/xlsx
    trans_df = dfs['ActiveMoves'][
        (dfs['ActiveMoves']['oper_name'] == 'Перевод ЦБ') &
        dfs['ActiveMoves']['description'].str.contains('Депозитарный договор')
    ].copy()
    trans_df['in_qty'] = trans_df['in_qty'].astype(int)
    trans_df = trans_df.groupby(['active_name', 'ISIN', 'date'])['in_qty'].sum().reset_index()

    print(f"{Fore.BLUE}"
          f"У вас имеются акции, которые поступили путём перевода между брокерскими счетами."
          f"{Style.RESET_ALL}")
    for idx, row in trans_df.iterrows():
        print(f"{row['ISIN']} {row['active_name']} количество {row['in_qty']}")
    print(f"{Fore.BLUE}"
          f"Отправьте excel/csv файл, с информацией о дате и цене приобретения данных ценных бумаг. "
          f"Имя колонки[пример данных] - "
          f"isin_code[US7496552057],currency[RUB],t_date[2024-03-20T11:35:37],t_q[10],t_price[1384.40]"
          f"{Style.RESET_ALL}")

    trans_data = pd.read_excel(f'files/{trans_file_name}.xlsx', parse_dates=['t_date'])

    # Check
    trans_data_group = trans_data.groupby('isin_code')['t_q'].sum()
    check = trans_df.merge(trans_data_group, left_on='ISIN', right_on='isin_code', how='left')

    dont_have_isins = check[pd.isna(check['t_q']) | pd.isna(check['in_qty'])]
    if len(dont_have_isins) > 0:
        print(dont_have_isins)
        raise Exception('Под данным тикерам нет данных')

    check['diff'] = check['t_q'] - check['in_qty']
    wrong_number = check[check['diff'] != 0]
    if len(wrong_number) > 0:
        print(wrong_number)
        raise Exception('Под данным тикерам предоставлено неверное суммарное количество бумаг')

    trans_data['kind'] = 'покупка'
    trans_data = trans_data.merge(
        dfs['FinInfo'][['isin', 'code']],
        left_on='isin_code', right_on='isin',
        how='left'
    ).drop(['isin'], axis=1)
    trans_data = trans_data.merge(
            trans_df[['ISIN', 'date']],
            left_on='isin_code', right_on='ISIN',
            how='inner'
        ).drop(['ISIN', 't_date'], axis=1)\
        .rename(columns={'date': 't_date'})

    trans_data = trans_data[[col for col in trans_data.columns if col in dfs['Trades'].columns]]
    trans_data['Comment'] = 'TransferIn'

    dfs['Trades'] = pd.concat([dfs['Trades'], trans_data], sort=False, ignore_index=True).sort_values('t_date')
    dfs['Trades']['t_sum'] = dfs['Trades']['t_sum'].fillna(dfs['Trades']['t_q'] * dfs['Trades']['t_price'])
    dfs['Trades']['security_name'] = dfs['Trades']['security_name'].fillna(dfs['Trades']['code'])

    return dfs


def fifo_calc(dfs) -> dict:
    df = dfs['Trades'].sort_values(['isin_code', 't_date'])
    df['comm_per_one'] = (df['comis'] / df['t_q'].abs()).fillna(0)

    global max_year
    buy_after_trades = []
    total_positions = []
    results = []
    for isin, group in df.groupby('isin_code'):
        group = group.sort_values('t_date')
        buys = []

        snapshots = {}
        current_year = None
        for _, row in group.iterrows():

            row_year = row['t_date'].year
            if (current_year is not None) and (row['t_date'] > pd.to_datetime(f"12/31/{current_year} 23:59:59")):
                while current_year < row_year:
                    snapshot_positions = buys
                    snapshots[current_year] = [p.copy() for p in snapshot_positions]
                    current_year += 1
            current_year = row_year

            symb = row['security_name']
            currency = row['currency']
            qty = row['t_q']
            date = row['t_date']
            price = row['t_price']
            comm_per_one = row['comm_per_one']

            if qty > 0:
                buys.append({
                    'symbol': symb, 'isin': isin, 'currency': currency, 'date_Time': date, 'qty': qty, 'price': price,
                    'comm_per_one': comm_per_one
                })
            else:
                sell_qty = -qty
                while sell_qty > 0 and buys:
                    buy = buys[0]
                    matched_qty = min(sell_qty, buy['qty'])
                    total_comm = matched_qty * buy['comm_per_one'] + matched_qty * comm_per_one

                    result = {
                        'symbol': symb,
                        'isin': isin,
                        'currency': currency,
                        'buy_Date': buy['date_Time'],
                        'buy_Price': buy['price'],
                        'sell_Date': date,
                        'sell_Price': price,
                        'quantity': matched_qty,
                        'commission': total_comm,
                        'PnL': (price - buy['price']) * matched_qty
                    }
                    results.append(result)

                    buy['qty'] -= matched_qty
                    sell_qty -= matched_qty

                    if buy['qty'] == 0:
                        buys.pop(0)

        if current_year and buys:
            while current_year <= max_year:
                snapshots[current_year] = [p.copy() for p in buys]
                current_year += 1

        rows = []
        for year, positions in snapshots.items():
            for pos in positions:
                pos_with_year = pos.copy()
                pos_with_year['Year'] = year
                rows.append(pos_with_year)
        total_positions.append(pd.DataFrame(rows))

    df_fifo = pd.DataFrame(results)
    df_fifo = df_fifo.merge(dfs['FinInfo'][['isin', 'code']], on='isin', how='left') \
        .drop('symbol', axis=1) \
        .rename(columns={'code': 'symbol'})
    df_fifo['Comment'] = ''
    dfs['Fifo'] = df_fifo

    positions_df = pd.concat(total_positions, ignore_index=True)
    dfs['Positions'] = prep_positions_df(positions_df, dfs)

    return dfs


def add_currency(dfs: dict) -> dict:
    currency_df = pd.read_excel('Currency.xlsx')
    currency_df['Date'] = pd.to_datetime(currency_df['Date'], format='%d.%m.%Y')
    values = [col for col in currency_df.columns if ('quant' not in col) and ('Date' not in col)]
    currency_df = currency_df.melt(
        id_vars=['Date'],
        value_vars=values,
        var_name='currency',
        value_name='KZT'
    )

    for k, v in dfs.items():
        if k == 'Trades':
            v['Date'] = pd.to_datetime(v['t_date'].dt.date)
        elif k == 'Income':
            v['Date'] = pd.to_datetime(v['operation_date'].dt.date)
        elif k == 'Money':
            v['Date'] = pd.to_datetime(v['date'].dt.date)
        elif k in ['Commissions', 'Nalog']:
            v['Date'] = pd.to_datetime(v['rep_end'].dt.date)
        elif k == 'Fifo':
            v['Date'] = pd.to_datetime(v['sell_Date'].dt.date)
        elif k == 'Positions':
            v['Date'] = pd.to_datetime(v['date_Time'])
        elif k in ['FinInfo', 'ActiveMoves']:
            continue
        else:
            raise Exception(f'{k} - no processing procedure is specified.')

        v = v.merge(currency_df, on=['Date', 'currency'], how='left')
        dfs[k] = v.drop(['Date'], axis=1)

    return dfs


def income_tax_comm(dfs: dict) -> dict:
    del_idx = []

    # Divs with tax
    div_df = dfs['Money'][dfs['Money']['oper_name'].str.contains('ивиденд') & (dfs['Money']['in_qty'] > 0)].copy()
    div_df.drop(['out_qty', 'oper_name'], axis=1, inplace=True)
    del_idx.append(div_df.index)

    div_tax_df = dfs['Money'][dfs['Money']['oper_name'].str.contains('ивиденд') & (dfs['Money']['out_qty'] > 0)].copy()
    div_tax_df = div_tax_df[['date', 'out_qty', 'isin']].rename(columns={'out_qty': 'ru_tax'})
    div_df = pd.merge(div_df, div_tax_df, on=['date', 'isin'], how='left')
    div_df['tax_rate'] = div_df['ru_tax'] / div_df['in_qty']
    del_idx.append(div_tax_df.index.values)

    div_df = div_df.merge(dfs['FinInfo'][['isin', 'code']], on='isin', how='left')\
        .drop('ticker', axis=1)\
        .rename(columns={'code': 'ticker'})
    div_df['IsliquidKASE'] = 0
    dfs['Dividends'] = div_df

    # Coupons
    coupons_df = dfs['Money'][dfs['Money']['oper_name'].str.contains('выплаты по облигациям')].copy()
    coupons_df['ru_30%_tax'] = coupons_df['in_qty'] * 0.3
    coupons_df['tax_rate'] = coupons_df['tax_rate'].fillna(0)
    del_idx.append(coupons_df.index)
    dfs['Coupons'] = coupons_df

    # Money transfers
    money_trans_df = dfs['Money'][dfs['Money']['oper_name'].str.contains('клиента')]
    del_idx.append(money_trans_df.index)
    dfs['MoneyTrans'] = money_trans_df

    # Commissions
    commissions_df = dfs['Money'][dfs['Money']['oper_name'].str.contains('омисси')]
    del_idx.append(commissions_df.index)

    # Additional taxations
    other_tax_df = dfs['Money'][dfs['Money']['oper_name'].str.contains('алоговое удержание') &
                                ~dfs['Money']['oper_name'].str.contains('ивиденд')]
    del_idx.append(other_tax_df.index)
    dfs['Tax'] = other_tax_df

    # Check
    check_df = dfs['Money'].drop(np.hstack(del_idx))
    if len(check_df) > 0:
        print(check_df)
        raise Exception(f"unprocessed rows in dfs['Money']")

    return dfs


def final_preparations(dfs: dict) -> dict:
    del_data = ['Income', 'Money', 'Nalog']
    for del_name in del_data:
        dfs.pop(del_name)

    for k, v in dfs.items():
        if k in ['ActiveMoves']:
            v = v.merge(dfs['FinInfo'][['isin', 'code']], left_on='ISIN', right_on='isin', how='left') \
                .drop('ISIN', axis=1) \
                .rename(columns={'code': 'ticker'}) \
                .sort_values('date')
        elif k in ['Fifo']:
            v = v.sort_values('sell_Date')
            v['PnL_KZT'] = v['PnL'] * v['KZT']
            v['only_profit'] = np.where(v['PnL'] > 0, v['PnL'], 0)
            v['only_profit_KZT'] = v['only_profit'] * v['KZT']
            v['tax_KZT'] = v['only_profit_KZT'] * 0.1
        elif k in ['Dividends']:
            v['country'] = v['isin'].str[:2]
            v['amount_KZT'] = v['in_qty'] * v['KZT']
            v['OnlyProfit'] = np.where(v['in_qty'] > 0, v['in_qty'], 0)
            v['OnlyProfit_KZT'] = v['OnlyProfit'] * v['KZT']
            v['OP_KZcorrect'] = np.where(v['IsliquidKASE'] == 1, v['OnlyProfit_KZT'], 0)
            v['ru_tax_KZT'] = v['ru_tax'] * v['KZT']
            v['tax_KZT'] = v['amount_KZT'] * 0.1
            v['tax_KZT_withhold'] = np.where(
                v['ru_tax_KZT'].abs() > v['tax_KZT'],
                0,
                v['tax_KZT'] - v['ru_tax_KZT'].abs()
            )
            for col in ['OnlyProfit', 'OnlyProfit_KZT', 'tax_KZT', 'tax_KZT_withhold']:
                mask = (v['country'] == 'KZ')
                v.loc[mask, col] = 0
        elif k in ['Trades']:
            v['Invest'] = v['t_q'] * v['t_price']
        elif k in ['Coupons']:
            v = v.sort_values('date')
            v['amount_KZT'] = v['in_qty'] * v['KZT']
            v['only_profit'] = np.where(v['in_qty'] > 0, v['in_qty'], 0)
            v['only_profit_KZT'] = v['only_profit'] * v['KZT']
            v['tax_KZT'] = v['amount_KZT'] * 0.1
            v['ru_30%_tax_KZT'] = v['ru_30%_tax'] * v['KZT']
            v['tax_KZT_withhold'] = np.where(
                v['ru_30%_tax_KZT'].abs() > v['tax_KZT'],
                0,
                v['tax_KZT'] - v['ru_30%_tax_KZT'].abs()
            )
        elif k in ['Tax']:
            v['in_qty_KZT'] = v['in_qty'] * v['KZT']
            v['out_qty_KZT'] = v['out_qty'] * v['KZT']
            v['country'] = 'RU'

        dfs[k] = v

    years_results_dict = {}
    idx_names = ['Year', 'Country', 'Currency']
    if 'Fifo' in dfs.keys():
        dfs['Fifo']['country'] = dfs['Fifo']['isin'].str[:2]
        isin_fifo = dfs['Fifo'].groupby(
                [dfs['Fifo']['sell_Date'].dt.year, dfs['Fifo']['country'], dfs['Fifo']['currency']]
            )[['PnL', 'PnL_KZT', 'only_profit', 'only_profit_KZT', 'tax_KZT']]\
            .sum() \
            .rename_axis(idx_names)
        isin_fifo.columns = ['PnL', 'PnL_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Tax_KZT']
        years_results_dict['Yearly Trades'] = isin_fifo

    if 'Dividends' in dfs.keys():
        isin_divs = dfs['Dividends'].groupby(
                [dfs['Dividends']['date'].dt.year, dfs['Dividends']['country'], dfs['Dividends']['currency']]
            )[[
                'in_qty', 'amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'OP_KZcorrect', 'ru_tax_KZT', 'tax_KZT',
                'tax_KZT_withhold'
            ]].sum() \
            .rename_axis(idx_names)
        isin_divs.columns = [
            'Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'OP_KZcorrect', 'Withhold_KZT', 'Tax_KZT',
            'Tax_KZT_Withhold'
        ]
        years_results_dict['Yearly Dividends'] = isin_divs

    if 'Coupons' in dfs.keys():
        dfs['Coupons']['country'] = dfs['Coupons']['isin'].str[:2]
        isin_coupons = dfs['Coupons'].groupby(
                [dfs['Coupons']['date'].dt.year, dfs['Coupons']['country'], dfs['Coupons']['currency']]
            )[[
                'in_qty', 'amount_KZT', 'only_profit', 'only_profit_KZT', 'ru_30%_tax', 'ru_30%_tax_KZT', 'tax_KZT',
                'tax_KZT_withhold'
            ]].sum() \
            .rename_axis(idx_names)
        isin_coupons.columns = ['Amount', 'Amount_KZT', 'OnlyProfit', 'OnlyProfit_KZT', 'Withhold', 'Withhold_KZT',
                                'Tax_KZT', 'Tax_KZT_Withhold']
        years_results_dict['Yearly Coupons'] = isin_coupons

    if 'Tax' in dfs.keys():
        rus_tax = dfs['Tax'].groupby(
                [dfs['Tax']['date'].dt.year, dfs['Tax']['country'], dfs['Tax']['currency']]
            )[['in_qty', 'out_qty', 'in_qty_KZT', 'out_qty_KZT']] \
            .sum() \
            .rename_axis(idx_names)
        rus_tax['ru_tax'] = rus_tax['out_qty'] - rus_tax['in_qty']
        rus_tax['ru_tax_KZT'] = rus_tax['out_qty_KZT'] - rus_tax['in_qty_KZT']
        rus_tax.drop(['in_qty', 'out_qty', 'in_qty_KZT', 'out_qty_KZT'], axis=1, inplace=True)

        if 'isin_coupons' in locals():
            rus_tax = rus_tax.join(isin_coupons[['Withhold', 'Withhold_KZT']], how="left")
            rus_tax['ru_tax_for_trades'] = rus_tax['ru_tax'] - rus_tax['Withhold']
            rus_tax['ru_tax_KZT_for_trades'] = rus_tax['ru_tax_KZT'] - rus_tax['Withhold_KZT']
            rus_tax.drop(['Withhold', 'Withhold_KZT'], axis=1, inplace=True)

        rus_tax.columns = ['Withhold', 'Withhold_KZT', 'Withhold_forTrades', 'Withhold_forTrades_KZT']
        years_results_dict['Yearly Taxes'] = rus_tax

    dfs['Years_Results'] = years_results_dict

    return dfs


def prep_for_270form(dfs: dict) -> dict:
    # Positions
    dfs['Positions'] = dfs['Positions'].merge(dfs['FinInfo'][['isin', 'StockType', 'Asset']], on='isin', how='left')
    dfs['Positions'].columns = [col.capitalize() for col in dfs['Positions'].columns]
    dfs['Positions']['Country'] = dfs['Positions']['Isin'].str[:2]
    dfs['Positions']['RegCountry'] = dfs['Positions']['Country']

    # Trades
    dfs['Trades'] = dfs['Trades'].merge(
        dfs['FinInfo'][['isin', 'StockType', 'Asset']],
        left_on='isin_code', right_on='isin',
        how='left'
    ).drop(['isin'], axis=1)
    dfs['Trades']['Country'] = dfs['Trades']['isin_code'].str[:2]
    dfs['Trades'].rename(
        columns={'t_date': 'Date_Time', 't_q': 'Quantity', 'Asset': 'Asset_Category', 'isin_code': 'Security_ID',
                 'currency': 'Currency', 't_price': 'T._Price'},
        inplace=True
    )

    return dfs


def excel_writer(dfs: dict, acc_number: str) -> None:
    with pd.ExcelWriter(f'postProcessed/Tsifra_{acc_number}.xlsx', engine='openpyxl') as writer:
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
    acc_number: str = '1432280'
    trans_file_name: str = 'transfers_from_IB_U5157275'
    max_year = 0

    dfs = get_full_data()
    dfs = prepare_trades_df(dfs)
    dfs = check_transfers(dfs, trans_file_name)
    dfs = fifo_calc(dfs)
    dfs = add_currency(dfs)
    dfs = income_tax_comm(dfs)
    dfs = final_preparations(dfs)
    dfs = prep_for_270form(dfs)
    excel_writer(dfs, acc_number)
