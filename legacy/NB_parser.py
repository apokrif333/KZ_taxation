import io
from pprint import pprint
from zipfile import BadZipFile

import datetime
import pandas as pd
import pdfplumber
import requests
import json
from bs4 import BeautifulSoup as bs


def parse_pages(full_data: pd.DataFrame, need_year: int) -> pd.DataFrame:
    main_url = "https://nationalbank.kz"
    url = f"{main_url}/ru/news/oficialnye-kursy?page=page_n"

    for page in range(1, 100):
        print(f"Processing page {page}")

        cur_url = url.replace('page_n', str(page))
        answ = requests.get(cur_url)
        soup = bs(answ.content, 'lxml')
        links = soup.find_all('div', attrs={'class': 'posts-files__title'})

        if not links:
            return full_data

        for link in links:
            cur_link = link.a['href']
            cur_year = link.text.strip()[:4]
            if cur_year == '2014':
                return full_data

            file_url = f"{main_url}{cur_link}"
            try:
                table = pd.read_excel(file_url, engine="openpyxl", skiprows=2, header=0).iloc[:-4, 1:]
                table = table.rename(columns={'Unnamed: 1': 'Des_Currency', 'Unnamed: 2': 'Currency'})
                if len(table.columns) != 19:
                    print(f"len(table.columns) != 19: {file_url}. This year doesn't have full data")
                    continue

            except BadZipFile:
                table = requests.get(file_url)

                tables = []
                with pdfplumber.open(io.BytesIO(table.content)) as pdf:
                    for page in pdf.pages:
                        page_tables = page.extract_tables()
                        for table in page_tables:
                            df = pd.DataFrame(table[1:], columns=table[0])
                            tables.append(df)

                table = pd.concat(tables, ignore_index=True)
                cols_names = list(table.columns)
                cols_names[0] = 'Des_Currency'
                cols_names[1] = 'Currency'
                table.columns = cols_names
                table[list(table.columns)[2:]] = table[list(table.columns)[2:]].replace(',', '.', regex=True)
                table[list(table.columns)[2:]] =table[list(table.columns)[2:]].astype(float)

            table['Year'] = int(table.columns[-1][:4])
            table = table.rename(columns={table.columns[-2]: 'Annual'})
            need_cols = ['Des_Currency', 'Currency', 'Annual', 'Year']
            full_data = pd.concat([full_data, table[need_cols]], ignore_index=True)

            if need_year in table['Year'].unique():
                full_data = full_data.sort_values(by=['Year', 'Currency'], ascending=[False, True])
                return full_data


if __name__ == "__main__":
    last_year = datetime.datetime.now().year-1
    nb_rates = pd.read_excel("data/nb_rates.xlsx")
    if last_year not in nb_rates['Year'].unique():
        full_data = parse_pages(nb_rates, last_year)
        full_data.to_excel("nb_rates.xlsx", index=False)


