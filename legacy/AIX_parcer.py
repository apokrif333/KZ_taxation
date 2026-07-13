import requests
import datetime
import pandas as pd

total_df = pd.DataFrame()
cur_year = datetime.datetime.now().year+1
for start_year in range(2023, cur_year):
    url = (
        'https://market-backend.aixkz.com/api/table/mw-main-records?'
        f'search=&instrument=&listing_between_start={start_year}-01-01&listing_between_end={start_year}-12-31'
        '&is_etf_etn=true'
    )
    answ = requests.get(url)
    df = pd.DataFrame.from_dict(answ.json())
    df['year'] = start_year

    total_df = pd.concat([total_df, df], ignore_index=True)

print(total_df)
