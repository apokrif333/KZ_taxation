from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers import ib as ib_module
from kztax270.brokers.ib import InteractiveBrokersParser
from kztax270.canonical.schema import CanonicalDataset
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.reconciliation.models import ReconciliationMetric, ReconciliationSeverity
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.reference.securities import AixInstrumentProvider, OffshoreJurisdictionProvider
from kztax270.transfers import TransferInFifoLot, TransferInRequest


MINIMAL_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,U1
Account Information,Data,Base Currency,USD
Change in NAV,Header,Field Name,Field Value
Change in NAV,Data,Dividends,10
Change in NAV,Data,Withholding Tax,-1.5
Change in NAV,Data,Commissions,-2
Change in NAV,Data,Interest,3
Change in NAV,Data,Ending Value,108
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,AAPL,APPLE INC,1,US0378331005,AAPL,NASDAQ,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,AAPL,"2024-01-10, 10:00:00",10,100,100,-1000,-1,1001,0,0,O
Trades,Data,Order,Stocks,USD,AAPL,"2024-02-10, 10:00:00",-10,110,110,1100,-1,-1001,98,0,C
Trades,Total,,Stocks,USD,,,,,,100,-2,0,98,0,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2024-03-01,AAPL(US0378331005) Cash Dividend USD 1 per Share (Ordinary Dividend),10
Dividends,Data,Total,,,10
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2024-03-01,AAPL(US0378331005) Cash Dividend USD 1 per Share - US Tax,-1.5,
Withholding Tax,Data,Total,,,-1.5,
Interest,Header,Currency,Date,Description,Amount
Interest,Data,USD,2024-04-01,USD Credit Interest for Mar-2024,3
Interest,Data,Total,,,3
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,108,108,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code
Realized & Unrealized Performance Summary,Data,Total (All Assets),,0,98,0,0,0,98,0,0,0,0,0,98,
"""


CUSIP_DIVIDEND_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UCUSIP
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,SPTL,SPDR PORT LNG TRM TRSRY,45540689,US78464A6644,SPTL,ARCA,1,ETF,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2025-03-06,SPTL(78464A664) Cash Dividend USD 0.083059 per Share (Ordinary Dividend),97.84
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2025-03-06,SPTL(78464A664) Cash Dividend USD 0.083059 per Share - US Tax,-14.68,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


BARE_SYMBOL_DIVIDEND_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2018 - December 31, 2018"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UBARE
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,IEF,ISHARES 7-10 YEAR TREASURY BOND ETF,1,464287440,IEF,NASDAQ,1,ETF,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2018-03-07,IEF Cash Dividend USD 0.070 per Share (Ordinary Dividend),14.98
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


OPTION_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UOPT
Account Information,Data,Base Currency,USD
Change in NAV,Header,Field Name,Field Value
Change in NAV,Data,Commissions,-2
Change in NAV,Data,Ending Value,40
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Equity and Index Options,SPY   250825P00643000,SPY 25AUG25 643 P,2,,SPY,CBOE,100,PUT,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Equity and Index Options,USD,SPY 25AUG25 643 P,"2025-08-25, 09:36:08",4,0.66,0.66,-264,-1,265,0,0,O
Trades,Data,Order,Equity and Index Options,USD,SPY 25AUG25 643 P,"2025-08-25, 15:51:28",-4,0.76,0.76,304,-1,-265,38,0,C
Trades,Total,,Equity and Index Options,USD,,,,,,40,-2,0,38,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,40,40,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code
Realized & Unrealized Performance Summary,Data,Total (All Assets),,0,38,0,0,0,38,0,0,0,0,0,38,
"""


MISSING_OPENING_LOT_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2018 - December 31, 2018"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UMISS
Account Information,Data,Base Currency,USD
Change in NAV,Header,Field Name,Field Value
Change in NAV,Data,Commissions,-0.35700712
Change in NAV,Data,Ending Value,677.7
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,SVXY,PROSHARES SHORT VIX ST FUTUR,3,74347W627,SVXY,ARCA,1,ETF,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,SVXY,"2018-01-03, 09:39:34",-5,135.54,135.51,677.7,-0.35700712,-652.483513,24.85948,0.15,C;P
Trades,Total,,Stocks,USD,,,,,,677.7,-0.35700712,0,24.85948,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,677.7,677.7,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code
Realized & Unrealized Performance Summary,Data,Total (All Assets),,0,24.85948,0,0,0,24.85948,0,0,0,0,0,24.85948,
"""


SPLIT_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2021 - December 31, 2021"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,USPLIT
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,NVDA,NVIDIA CORP,4,US67066G1040,NVDA,NASDAQ,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,NVDA,"2021-05-10, 16:00:04",2,570.63,570.63,-1141.26,-0.35,1141.61,0,0,O
Trades,Data,Order,Stocks,USD,NVDA,"2021-07-06, 16:00:04",-1,827.94,827.94,827.94,-0.35,-570.805,256.785,0,C
Trades,Data,Order,Stocks,USD,NVDA,"2021-09-13, 16:00:02",-2,221.52,221.52,443.04,-0.35,-285.315,157.375,0,C
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2021-07-20,"2021-07-19, 20:25:00","NVDA(US67066G1040) Split 4 for 1 (NVDA, NVIDIA CORP, US67066G1040)",6,0,0,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code
Realized & Unrealized Performance Summary,Data,Total (All Assets),,0,414.16,0,0,0,414.16,0,0,0,0,0,414.16,
"""


WITHHOLDING_REVERT_2021_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2021 - December 31, 2021"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UREV
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,TLT,ISHARES 20+ YEAR TREASURY BOND ETF,5,US4642874329,TLT,NASDAQ,1,ETF,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2021-06-01,TLT(US4642874329) Cash Dividend USD 1 per Share (Ordinary Dividend),10
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2021-06-01,TLT(US4642874329) Cash Dividend USD 1 per Share - US Tax,-2.79,
Withholding Tax,Data,USD,2021-12-01,TLT(US4642874329) Reversal of Withholding Tax,2.29,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


WITHHOLDING_REVERT_2022_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2022 - December 31, 2022"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UREV
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,TLT,ISHARES 20+ YEAR TREASURY BOND ETF,5,US4642874329,TLT,NASDAQ,1,ETF,
Financial Instrument Information,Data,Stocks,XIU,ISHARES S&P/TSX 60 INDEX ETF,6,CA0000000000,XIU,TSX,1,ETF,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2022-06-01,XIU(CA0000000000) Cash Dividend USD 1 per Share (Ordinary Dividend),5
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2022-01-15,TLT(US4642874329) Reversal of Withholding Tax,2.37,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


FX_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UFX
Account Information,Data,Base Currency,USD
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,,Proceeds,Comm in USD,,,MTM in USD,Code
Trades,Data,Order,Forex,USD,EUR.USD,"2024-01-10, 10:00:00",100,1.1,,-110,-1,,,5,P
Trades,Data,Order,Forex,USD,EUR.USD,"2024-02-10, 10:00:00",-50,1.2,,60,-1,,,7,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Mark-to-Market Performance Summary,Header,Asset Category,Symbol,Prior Quantity,Current Quantity,Prior Price,Current Price,Mark-to-Market P/L Position,Mark-to-Market P/L Transaction,Mark-to-Market P/L Commissions,Mark-to-Market P/L Other,Mark-to-Market P/L Total,Code
Mark-to-Market Performance Summary,Data,Forex,USD,0,-393.401880703,1.0000,1.0000,0,0,0,0,0,
Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code
Realized & Unrealized Performance Summary,Data,Forex,EUR.USD,0,10,0,0,0,10,0,0,0,0,0,10,
"""


TRANSFER_OUT_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UTRANSFER
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,AAPL,APPLE INC,1,US0378331005,AAPL,NASDAQ,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,AAPL,"2024-01-10, 10:00:00",10,100,100,-1000,-10,1010,0,0,O
Trades,Data,Order,Stocks,USD,AAPL,"2024-02-10, 10:00:00",10,200,200,-2000,-20,2020,0,0,O
Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,Cash Amount,Code
Transfers,Data,Stocks,USD,AAPL,2024-08-19,Internal,Out,--,U2,-15,--,-3000,0.00,0.00,
Transfers,Data,Total,,,,,,,,,,-3000,0,0,
Transfers,Data,Total in USD,,,,,,,,,,-3000,0,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


TRANSFER_IN_2023_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2023 - December 31, 2023"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UTRANSFERIN
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,MAGN,MAGNITOGORSK IRON & STEEL WO,360308780,RU0009084396,,MOEX,1,COMMON,
Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,Cash Amount,Code
Transfers,Data,Stocks,RUB,MAGN,2023-05-01,Internal,In,--,U5157275,"10,000",--,"395,800.00",0.00,0.00,
Transfers,Data,Total,,,,,,,,,,395800,0,0,
Transfers,Data,Total in USD,,,,,,,,,,5000,0,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,RUB,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,RUB,MAGN,10000,1,48.048,480480,52.15,521500,41020,
"""


TRANSFER_IN_2024_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UTRANSFERIN
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,MAGN,MAGNITOGORSK IRON & STEEL WO,360308780,RU0009084396,MAGN,MOEX,1,COMMON,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,RUB,0,0,0,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,RUB,2025-03-12,MAGN(RU0009084396) Cash Dividend RUB 2.752 per Share (Ordinary Dividend),27520
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,RUB,2025-03-12,MAGN(RU0009084396) Cash Dividend RUB 2.752 per Share - RU Tax,-4128,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,RUB,MAGN,10000,1,48.048,480480,54.42,544200,63720,
"""


TRANSFER_IN_2025_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2025 - June 12, 2025"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UTRANSFERIN
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,MAGN,MAGNITOGORSK IRON & STEEL WO,360308780,RU0009084396,MAGN,MOEX,1,COMMON,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,RUB,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,RUB,MAGN,10000,1,48.048,480480,54.42,544200,63720,
"""


REVERSAL_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2020 - December 31, 2020"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UREVERSAL
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,CCR,CONSOL COAL RESOURCES LP,9,20855T100,CCR,NYSE,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,CCR,"2020-11-05, 09:30:00","-1,275",2.78,3.09,3544.5,-6.60505845,-3537.894942,0,-395.25,O;P
Trades,Data,Order,Stocks,USD,CCR,"2020-11-05, 16:00:01","1,275",3.09,3.09,-3939.75,-6.375,3743.450783,-202.674217,0,C;O;P
Trades,Data,Order,Stocks,USD,CCR,"2020-11-05, 16:00:01","1,275",3.09,3.09,-3939.75,-6.375,2613.989158,-205.555842,0,C;O;P
Trades,Data,Order,Stocks,USD,CCR,"2020-11-06, 09:30:00","-1,275",2.93,2.91,3735.75,-6.609285075,-3946.125,-216.984285,25.5,C;P
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


PRIOR_LOT_RESIDUAL_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2020 - December 31, 2020"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UPRIOR
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,ITA,ISHARES U.S. AEROSPACE & DEF,10,US4642887602,ITA,BATS,1,ETF,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,ITA,"2020-01-02, 16:00:00",17,227.43,227.38,-3866.31,-0.37,3866.68,0,0,O
Trades,Data,Order,Stocks,USD,ITA,"2020-02-28, 16:00:00",-87,200.89,200.89,17477.43,-0.87,-18636.68,-1160.10,0,C
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


PRIOR_INVENTORY_BEFORE_VISIBLE_BUY_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2020 - December 31, 2020"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UPRIORINV
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,FBT,FIRST TRUST NYSE ARCA BIOTECH ETF,11,US33733E2037,FBT,ARCA,1,ETF,
Mark-to-Market Performance Summary,Header,Asset Category,Symbol,Prior Quantity,Current Quantity,Prior Price,Current Price,Mark-to-Market P/L Position,Mark-to-Market P/L Transaction,Mark-to-Market P/L Commissions,Mark-to-Market P/L Other,Mark-to-Market P/L Total,Code
Mark-to-Market Performance Summary,Data,Stocks,FBT,5,2,100,120,0,0,0,0,0,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,FBT,"2020-06-01, 09:30:00",3,110,110,-330,-0.30,330.30,0,0,O
Trades,Data,Order,Stocks,USD,FBT,"2020-08-03, 11:38:23",-6,120,120,720,-0.60,-660.30,60,0,C
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,390,390,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,USD,FBT,2,1,110,220,120,240,20,
"""


CASH_MERGER_2021_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2021 - December 31, 2021"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UMERGER
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,TWTR,TWITTER INC,137780444,US90184L1026,,NYSE,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,TWTR,"2021-11-02, 11:17:53",20,54.32,53.99,-1086.4,-1,1087.4,0,-6.6,O
Trades,Data,Order,Stocks,USD,TWTR,"2021-11-02, 11:23:00",20,54.23,53.99,-1084.6,-1,1085.6,0,-4.8,O
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,USD,TWTR,40,1,54.325,2173,43.22,1728.8,-444.2,
"""


CASH_MERGER_2022_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2022 - December 31, 2022"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UMERGER
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,TWTR,TWITTER INC,137780444,US90184L1026,,VALUE,1,COMMON,
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2022-10-31,"2022-10-28, 20:25:00","TWTR(US90184L1026) Merged(Acquisition) for USD 54.20 per Share (TWTR, TWITTER INC, US90184L1026)",-40,2168,-2168,-5,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,2168,2168,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code
Realized & Unrealized Performance Summary,Data,Stocks,TWTR,0,0,-5,0,0,-5,0,0,0,0,0,-5,
Realized & Unrealized Performance Summary,Data,Total (All Assets),,0,0,-5,0,0,-5,0,0,0,0,0,-5,
"""


STOCK_CA_2021_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2021 - December 31, 2021"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,USTOCKCA
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,ERUS,ISHARES MSCI RUSSIA ETF,253190518,US46434G7988,,ARCA,1,ETF,
Financial Instrument Information,Data,Stocks,T,AT&T INC,37018770,US00206R1023,,NYSE,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,ERUS,"2021-11-23, 10:15:44",30,45.43,45.43,-1362.9,-1,1363.9,0,0,O
Trades,Data,Order,Stocks,USD,T,"2021-12-03, 09:36:15",100,22.975,22.975,-2297.5,-1,2298.5,0,0,O
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


STOCK_CA_2022_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2022 - December 31, 2022"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,USTOCKCA
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,ERUS,ISHARES MSCI RUSSIA ETF,253190518,US46434G7988,,ARCA,1,ETF,
Financial Instrument Information,Data,Stocks,ERUS.ESC,ESC ETF ISHARES MSCI RUSSIA BE<,579809677,US464ESC0112,,VALUE,1,COMMON,
Financial Instrument Information,Data,Stocks,T,AT&T INC,37018770,US00206R1023,,NYSE,1,COMMON,
Financial Instrument Information,Data,Stocks,WBD,WARNER BROS DISCOVERY INC,554208351,US9344231041,,NASDAQ,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,ERUS,"2022-02-25, 09:31:01",25,28,28,-700,-1,701,0,0,O
Trades,Data,Order,Stocks,USD,ERUS.ESC,"2022-08-16, 20:25:00",-0.6097,0,0,0,0,0,0,0,C
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2022-04-11,"2022-04-08, 20:25:00","T(US00206R1023) Spinoff  241917 for 1000000 (WBD, WARNER BROS DISCOVERY INC, US9344231041)",24.1917,0,0,0,
Corporate Actions,Data,Stocks,USD,2022-08-17,"2022-08-16, 20:25:00","ERUS(US46434G7988) Cash and Stock Merger (Acquisition) 464ESC011 1 for 1 and USD 0.032727 (ERUS, ISHARES MSCI RUSSIA ETF, US46434G7988)",-55.6097,1.82,-54,0,
Corporate Actions,Data,Stocks,USD,2022-08-17,"2022-08-16, 20:25:00","ERUS(US46434G7988) Cash and Stock Merger (Acquisition) 464ESC011 1 for 1 and USD 0.032727 (ERUS.ESC, ESC ETF ISHARES MSCI RUSSIA BE<, US464ESC0112)",55.6097,0,2.07,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


TRANSFER_ADJUSTMENT_2023_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2023 - December 31, 2023"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UTRANSADJ
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,AAPL,APPLE INC,1,US0378331005,AAPL,NASDAQ,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,AAPL,"2023-01-02, 10:00:00",10,100,100,-1000,-1,1001,0,0,O
Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,Cash Amount,Code
Transfers,Data,Stocks,USD,AAPL,2023-12-21,FOP,Out,--,ACC,-10,--,-1000,0,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


TRANSFER_ADJUSTMENT_2024_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UTRANSADJ
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,AAPL,APPLE INC,1,US0378331005,AAPL,NASDAQ,1,COMMON,
Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,Cash Amount,Code
Transfers,Data,Stocks,USD,AAPL,2023-12-21,FOP,Out,--,ACC,10,--,1000,0,0,Ca
Transfers,Data,Stocks,USD,AAPL,2024-01-17,FOP,Out,--,ACC,-10,--,-1000,0,0,
Transfers,Data,Stocks,USD,AAPL,2024-01-17,FOP,Out,--,ACC,10,--,1000,0,0,Ca
Transfers,Data,Stocks,USD,AAPL,2024-01-29,FOP,Out,--,ACC,-10,--,-1000,0,0,
Transfers,Data,Stocks,USD,AAPL,2024-01-29,FOP,Out,--,ACC,10,--,1000,0,0,Ca
Transfers,Data,Stocks,USD,AAPL,2024-01-29,FOP,Out,--,ACC,-10,--,-1000,0,0,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


SYMBOL_CHANGE_2024_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,URENAME
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,SQ,BLOCK INC,212671971,US8522341036,SQ,NYSE,1,COMMON,
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,SQ,"2024-12-01, 10:00:00",13,207.07,207.07,-2691.91,-1,2692.91,0,0,O
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,USD,SQ,13,1,207.146923077,2692.91,84.99,1104.87,-1588.04,
"""


SYMBOL_CHANGE_2025_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2025 - July 24, 2025"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,URENAME
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,XYZ,BLOCK INC,212671971,US8522341036,XYZ,NYSE,1,COMMON,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,USD,XYZ,13,1,207.146923077,2692.91,79.77,1037.01,-1655.9,
"""


AGGREGATE_DIVIDEND_CREDIT_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2019 - December 31, 2019"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UDIV
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,AAA,AAA INC,7,US0000000001,AAA,NASDAQ,1,COMMON,
Financial Instrument Information,Data,Stocks,BBB,BBB INC,8,US0000000002,BBB,NASDAQ,1,COMMON,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2019-01-10,AAA(US0000000001) Cash Dividend USD 1 per Share (Ordinary Dividend),100
Dividends,Data,USD,2019-02-10,BBB(US0000000002) Cash Dividend USD 1 per Share (Ordinary Dividend),100
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2019-01-10,AAA(US0000000001) Cash Dividend USD 1 per Share - US Tax,-15,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


OVERWITHHELD_DIVIDEND_CREDIT_IB_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2023 - December 31, 2023"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,UOVER
Account Information,Data,Base Currency,USD
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,AAA,AAA INC,7,US0000000001,AAA,NASDAQ,1,COMMON,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,USD,2023-01-10,AAA(US0000000001) Cash Dividend USD 1 per Share (Ordinary Dividend),100
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2023-01-10,AAA(US0000000001) Cash Dividend USD 1 per Share - US Tax,-15,
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Ending Cash,USD,0,0,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
"""


class InteractiveBrokersParserTests(unittest.TestCase):
    def test_parse_minimal_ib_report_and_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            report_path = ib_root / "U1_2024_2024.csv"
            report_path.write_text(MINIMAL_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2024, "USD"): Decimal("470")}))
            reports = parser.discover_reports(raw_root, "U1")
            result = parser.parse_reports(reports, "U1")

        dataset = result.dataset
        self.assertEqual(len(dataset.tables["Trades"]), 2)
        self.assertNotIn("side", dataset.tables["Trades"][0])
        self.assertEqual([row["trade_type"] for row in dataset.tables["Trades"]], ["trade", "trade"])
        self.assertEqual(len(dataset.tables["Fifo"]), 1)
        fifo = dataset.tables["Fifo"][0]
        self.assertNotIn("opening_lot_status", fifo)
        self.assertEqual(fifo["acquisition_cost_with_commission"], "1001.0")
        self.assertEqual(fifo["pnl_before_commission"], "100")
        self.assertEqual(fifo["pnl"], "99.0")
        self.assertEqual(fifo["pnl_after_all_commissions"], "98.0")
        self.assertEqual(dataset.tables["Unprocessed"], [])
        self.assertEqual(dataset.tables["Dividends"][0]["gross_amount_kzt"], "4700")

        items = ReconciliationEngine().reconcile_dataset(dataset)
        non_info = [item for item in items if item.severity != ReconciliationSeverity.INFO]
        self.assertEqual(non_info, [])
        by_instrument_turnover = [
            item for item in items if item.metric == ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT
        ]
        by_instrument_pnl = [
            item for item in items if item.metric == ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT
        ]
        self.assertEqual(len(by_instrument_turnover), 1)
        self.assertEqual(by_instrument_turnover[0].broker_value, Decimal("2100"))
        self.assertEqual(by_instrument_turnover[0].canonical_value, Decimal("2100"))
        self.assertEqual(len(by_instrument_pnl), 1)
        self.assertEqual(by_instrument_pnl[0].broker_value, Decimal("98"))
        self.assertEqual(by_instrument_pnl[0].canonical_value, Decimal("98.0"))

    def test_dividend_cusip_description_resolves_to_isin_from_instruments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UCUSIP_2025_2025.csv").write_text(CUSIP_DIVIDEND_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2025, "USD"): Decimal("521.59")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UCUSIP"), "UCUSIP")

        dividends = result.dataset.tables["Dividends"]
        self.assertEqual(len(dividends), 1)
        self.assertEqual(dividends[0]["symbol"], "SPTL")
        self.assertEqual(dividends[0]["isin"], "US78464A6644")
        self.assertEqual(dividends[0]["country"], "US")
        self.assertEqual(dividends[0]["withholding_tax"], "-14.68")
        self.assertEqual(dividends[0]["gross_amount_kzt"], "51032.3656")
        self.assertNotEqual(dividends[0]["country"], "78")

    def test_dividend_bare_symbol_description_resolves_to_isin_from_instruments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UBARE_2018_2018.csv").write_text(BARE_SYMBOL_DIVIDEND_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2018, "USD"): Decimal("344.71")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UBARE"), "UBARE")

        dividends = result.dataset.tables["Dividends"]
        self.assertEqual(len(dividends), 1)
        self.assertEqual(dividends[0]["symbol"], "IEF")
        self.assertEqual(dividends[0]["isin"], "US4642874402")
        self.assertEqual(dividends[0]["country"], "US")
        self.assertEqual(dividends[0]["gross_amount"], "14.98")
        self.assertEqual(dividends[0]["gross_amount_kzt"], "5163.7558")

    def test_options_keep_broker_quantity_price_and_use_multiplier_for_amounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            report_path = ib_root / "UOPT_2025_2025.csv"
            report_path.write_text(OPTION_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2025, "USD"): Decimal("500")}))
            reports = parser.discover_reports(raw_root, "UOPT")
            result = parser.parse_reports(reports, "UOPT")

        trades = result.dataset.tables["Trades"]
        self.assertEqual(trades[0]["quantity"], "4")
        self.assertEqual(trades[0]["price"], "0.66")
        self.assertEqual(trades[0]["multiplier"], "100")
        self.assertEqual(trades[0]["amount"], "264")
        self.assertEqual(trades[0]["trade_type"], "trade")
        self.assertNotIn("gross_amount", trades[0])
        self.assertNotIn("broker_multiplier", trades[0])
        self.assertNotIn("side", trades[0])
        self.assertNotIn("data_discriminator", trades[0])
        self.assertNotIn("_broker_realized_pl", trades[0])
        self.assertNotIn("_calculation_multiplier", trades[0])
        self.assertNotIn("calculation_quantity", trades[0])
        self.assertNotIn("calculation_price", trades[0])

        fifo = result.dataset.tables["Fifo"][0]
        self.assertEqual(fifo["enter_quantity"], "4")
        self.assertEqual(fifo["exit_quantity"], "4")
        self.assertEqual(fifo["enter_price"], "0.66")
        self.assertEqual(fifo["exit_price"], "0.76")
        self.assertEqual(fifo["enter_multiplier"], "100")
        self.assertEqual(fifo["exit_multiplier"], "100")
        self.assertEqual(fifo["enter_amount"], "264")
        self.assertEqual(fifo["exit_amount"], "304.00")
        self.assertEqual(fifo["acquisition_cost_with_commission"], "264.00")
        self.assertEqual(fifo["pnl"], "40.00")
        self.assertEqual(fifo["pnl_after_all_commissions"], "38.00")
        derivative_rows = [row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Derivatives"]
        self.assertEqual(len(derivative_rows), 1)
        self.assertEqual(derivative_rows[0]["flag"], "non-preferential")
        self.assertEqual(derivative_rows[0]["exchange"], "outofKZ")
        self.assertEqual(derivative_rows[0]["pnl"], "40.00")
        self.assertEqual(derivative_rows[0]["pnl_kzt"], "20000.00")
        self.assertEqual(derivative_rows[0]["only_profit"], "40.00")
        self.assertEqual(derivative_rows[0]["only_profit_kzt"], "20000.00")
        self.assertEqual(derivative_rows[0]["tax_kzt"], "2000.00")

    def test_missing_opening_lot_is_visible_in_unprocessed_and_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            report_path = ib_root / "UMISS_2018_2018.csv"
            report_path.write_text(MISSING_OPENING_LOT_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2018, "USD"): Decimal("330")}))
            reports = parser.discover_reports(raw_root, "UMISS")
            result = parser.parse_reports(reports, "UMISS")

        dataset = result.dataset
        trade = dataset.tables["Trades"][0]
        self.assertEqual(trade["symbol"], "SVXY")
        self.assertEqual(trade["quantity"], "-5")
        self.assertEqual(trade["price"], "135.54")
        self.assertEqual(trade["amount"], "677.7")
        self.assertNotIn("data_discriminator", trade)
        self.assertNotIn("raw_c_price", trade)
        self.assertNotIn("basis", trade)
        self.assertNotIn("mtm_pl", trade)
        self.assertNotIn("code", trade)
        self.assertNotIn("side", trade)
        self.assertNotIn("_broker_realized_pl", trade)
        self.assertNotIn("_calculation_multiplier", trade)
        self.assertNotIn("calculation_quantity", trade)
        self.assertNotIn("calculation_price", trade)

        fifo_row = dataset.tables["Fifo"][0]
        self.assertIsNone(fifo_row["enter_date"])
        self.assertEqual(fifo_row["enter_price"], "130.4967026")
        self.assertEqual(fifo_row["acquisition_cost_with_commission"], "652.483513")
        self.assertEqual(fifo_row["pnl_after_all_commissions"], "24.85948")
        self.assertEqual(fifo_row["pnl"], "25.216487120")

        self.assertEqual(len(dataset.tables["Unprocessed"]), 1)
        unprocessed = dataset.tables["Unprocessed"][0]
        self.assertEqual(unprocessed["severity"], "error")
        self.assertEqual(unprocessed["reason"], "missing_opening_lot")
        self.assertEqual(unprocessed["quantity"], "-5")
        self.assertEqual(unprocessed["price"], "135.54")
        self.assertEqual(unprocessed["amount"], "677.7")
        self.assertNotIn("raw_quantity", unprocessed)

        items = ReconciliationEngine().reconcile_dataset(dataset)
        unprocessed_items = [item for item in items if item.metric == ReconciliationMetric.UNPROCESSED_ROWS]
        self.assertEqual(len(unprocessed_items), 1)
        self.assertEqual(unprocessed_items[0].severity, ReconciliationSeverity.ERROR)
        pnl_items = [item for item in items if item.metric == ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT]
        self.assertEqual(len(pnl_items), 1)
        self.assertEqual(pnl_items[0].broker_value, Decimal("24.85948"))
        self.assertEqual(pnl_items[0].canonical_value, Decimal("24.85948"))

    def test_split_adjusts_fifo_price_and_quantity_but_keeps_trades_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            report_path = ib_root / "USPLIT_2021_2021.csv"
            report_path.write_text(SPLIT_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2021, "USD"): Decimal("426.03")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "USPLIT"), "USPLIT")

        fifo_rows = result.dataset.tables["Fifo"]
        trades = result.dataset.tables["Trades"]
        self.assertEqual(trades[0]["quantity"], "2")
        self.assertEqual(trades[0]["price"], "570.63")
        self.assertEqual(trades[1]["quantity"], "-1")
        self.assertEqual(trades[1]["price"], "827.94")
        self.assertTrue(all(trade["trade_type"] == "trade" for trade in trades))
        self.assertTrue(all(not str(trade.get("source_report") or "").startswith("corporate_action:") for trade in trades))
        self.assertEqual(len(fifo_rows), 2)
        pre_split, post_split = fifo_rows
        self.assertEqual(pre_split["enter_quantity"], "4")
        self.assertEqual(pre_split["enter_price"], "142.6575")
        self.assertEqual(pre_split["exit_quantity"], "4")
        self.assertEqual(pre_split["exit_price"], "206.985")
        self.assertEqual(pre_split["enter_amount"], "570.63")
        self.assertEqual(post_split["enter_quantity"], "2")
        self.assertEqual(post_split["enter_price"], "142.6575")
        self.assertEqual(post_split["exit_quantity"], "2")
        self.assertEqual(post_split["exit_price"], "221.52")
        self.assertNotIn("enter_calculation_quantity", pre_split)
        self.assertNotIn("enter_calculation_price", pre_split)
        self.assertNotIn("exit_calculation_quantity", pre_split)
        self.assertNotIn("exit_calculation_price", pre_split)
        self.assertNotIn(post_split["enter_quantity"], {"0.25", "0.5"})

    def test_dividend_withholding_revert_offsets_same_year_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UREV_2021_2021.csv").write_text(WITHHOLDING_REVERT_2021_IB_CSV, encoding="utf-8")
            (ib_root / "UREV_2022_2022.csv").write_text(WITHHOLDING_REVERT_2022_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(
                AnnualFxRateProvider({(2021, "USD"): Decimal("426.03"), (2022, "USD"): Decimal("460")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "UREV"), "UREV")

        dividends = result.dataset.tables["Dividends"]
        self.assertTrue(all("ex_date" not in row for row in dividends))

        yearly_dividends = {
            row["year"]: row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends"
        }
        self.assertEqual(yearly_dividends[2021]["amount"], "10.00")
        self.assertEqual(yearly_dividends[2021]["country"], "US")
        self.assertEqual(yearly_dividends[2021]["withhold_kzt"], "-213.02")
        self.assertEqual(yearly_dividends[2021]["tax_kzt"], "426.03")
        self.assertEqual(yearly_dividends[2021]["tax_kzt_withhold"], "213.02")
        self.assertNotIn("only_profit", yearly_dividends[2021])
        self.assertNotIn("only_profit_kzt", yearly_dividends[2021])

        self.assertEqual(yearly_dividends[2022]["amount"], "5.00")
        self.assertEqual(yearly_dividends[2022]["country"], "CA")
        self.assertEqual(yearly_dividends[2022]["amount_kzt"], "2300.00")
        self.assertEqual(yearly_dividends[2022]["withhold_kzt"], "0.00")
        self.assertEqual(yearly_dividends[2022]["tax_kzt"], "230.00")
        self.assertEqual(yearly_dividends[2022]["tax_kzt_withhold"], "230.00")

    def test_dividend_foreign_tax_credit_is_capped_by_year_not_instrument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UDIV_2019_2019.csv").write_text(AGGREGATE_DIVIDEND_CREDIT_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2019, "USD"): Decimal("382.75")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UDIV"), "UDIV")

        yearly_dividends = [
            row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends"
        ]
        self.assertEqual(len(yearly_dividends), 1)
        self.assertEqual(yearly_dividends[0]["amount"], "200.00")
        self.assertEqual(yearly_dividends[0]["amount_kzt"], "76550.00")
        self.assertEqual(yearly_dividends[0]["withhold_kzt"], "-5741.25")
        self.assertEqual(yearly_dividends[0]["tax_kzt"], "7655.00")
        self.assertEqual(yearly_dividends[0]["tax_kzt_withhold"], "1913.75")

    def test_dividend_withhold_kzt_shows_full_withholding_when_credit_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UOVER_2023_2023.csv").write_text(OVERWITHHELD_DIVIDEND_CREDIT_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2023, "USD"): Decimal("100")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UOVER"), "UOVER")

        yearly_dividends = [
            row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends"
        ]
        self.assertEqual(len(yearly_dividends), 1)
        self.assertEqual(yearly_dividends[0]["amount"], "100.00")
        self.assertEqual(yearly_dividends[0]["withhold_kzt"], "-1500.00")
        self.assertEqual(yearly_dividends[0]["tax_kzt"], "1000.00")
        self.assertEqual(yearly_dividends[0]["tax_kzt_withhold"], "0.00")

    def test_yearly_interest_and_fx_are_non_preferential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UFX_2024_2024.csv").write_text(FX_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2024, "USD"): Decimal("470")}))
            fx_result = parser.parse_reports(parser.discover_reports(raw_root, "UFX"), "UFX")

        fx_rows = [row for row in fx_result.dataset.tables["Years_Results"] if row["table"] == "Yearly FX Trades"]
        self.assertEqual(fx_rows[0]["flag"], "non-preferential")
        self.assertEqual(fx_rows[0]["pnl"], "10.00")
        self.assertEqual(fx_rows[0]["pnl_kzt"], "4700.00")
        self.assertEqual(fx_rows[0]["tax_kzt"], "0.00")
        self.assertFalse(any(row["table"] == "Yearly Derivatives" for row in fx_result.dataset.tables["Years_Results"]))
        self.assertNotIn("FxTrades", fx_result.dataset.tables)
        fx_trade_rows = [row for row in fx_result.dataset.tables["Trades"] if row["asset_type"] == "Forex"]
        self.assertEqual(len(fx_trade_rows), 2)
        self.assertTrue(all(row["country"] == "USA" for row in fx_trade_rows))
        fx_fifo_rows = [row for row in fx_result.dataset.tables["Fifo"] if row["asset_type"] == "Forex"]
        self.assertEqual(len(fx_fifo_rows), 2)
        self.assertTrue(all(row["country"] == "USA" for row in fx_fifo_rows))
        self.assertEqual(fx_fifo_rows[0]["source_trade_id"], fx_trade_rows[0]["trade_id"])
        self.assertEqual(fx_fifo_rows[0]["pnl_before_commission"], "5")
        self.assertEqual(fx_fifo_rows[0]["pnl_after_all_commissions"], "4")
        self.assertEqual(fx_fifo_rows[0]["pnl"], "4")
        self.assertEqual(fx_fifo_rows[1]["pnl_before_commission"], "7")
        self.assertEqual(fx_fifo_rows[1]["pnl_after_all_commissions"], "6")
        self.assertEqual(fx_fifo_rows[1]["pnl"], "6")
        reconciliation = ReconciliationEngine().reconcile_dataset(fx_result.dataset)
        position_errors = [
            item
            for item in reconciliation
            if item.metric == ReconciliationMetric.ENDING_POSITION_QUANTITY
            and item.severity == ReconciliationSeverity.ERROR
        ]
        self.assertEqual(position_errors, [])

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "U1_2024_2024.csv").write_text(MINIMAL_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2024, "USD"): Decimal("470")}))
            interest_result = parser.parse_reports(parser.discover_reports(raw_root, "U1"), "U1")

        interest_rows = [row for row in interest_result.dataset.tables["Years_Results"] if row["table"] == "Yearly Interest"]
        self.assertEqual(interest_rows[0]["flag"], "non-preferential")

    def test_security_transfer_out_uses_fifo_cost_basis_and_skips_total_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UTRANSFER_2024_2024.csv").write_text(TRANSFER_OUT_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2024, "USD"): Decimal("470")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UTRANSFER"), "UTRANSFER")

        transfers = result.dataset.tables["Transfers"]
        self.assertEqual(len(transfers), 2)
        self.assertTrue(all(row["symbol"] == "AAPL" for row in transfers))
        self.assertTrue(all(row["direction"] == "out" for row in transfers))
        self.assertEqual([row["quantity"] for row in transfers], ["10", "5"])
        self.assertEqual([row["price"] for row in transfers], ["101", "202"])
        self.assertEqual([row["enter_date"] for row in transfers], ["2024-01-10 10:00:00", "2024-02-10 10:00:00"])
        self.assertNotIn("Total", {row["asset_type"] for row in transfers})

        positions = result.dataset.tables["Positions"]
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["quantity"], "5")
        self.assertEqual(positions[0]["price"], "200")

    def test_security_transfer_in_creates_quantity_only_fifo_position_until_cost_basis_file_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UTRANSFERIN_2023_2023.csv").write_text(TRANSFER_IN_2023_IB_CSV, encoding="utf-8")
            (ib_root / "UTRANSFERIN_2024_2024.csv").write_text(TRANSFER_IN_2024_IB_CSV, encoding="utf-8")
            (ib_root / "UTRANSFERIN_20250101_20250612.csv").write_text(TRANSFER_IN_2025_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(
                AnnualFxRateProvider({(2023, "RUB"): Decimal("5"), (2024, "RUB"): Decimal("5"), (2025, "RUB"): Decimal("5")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "UTRANSFERIN"), "UTRANSFERIN")

        transfers = [row for row in result.dataset.tables["Transfers"] if row["symbol"] == "MAGN"]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["direction"], "in")
        self.assertEqual(transfers[0]["quantity"], "10000")
        self.assertIsNone(transfers[0]["price"])
        self.assertIsNone(transfers[0]["amount"])

        positions_by_year = {
            row["year"]: row for row in result.dataset.tables["Positions"] if row["symbol"] == "MAGN"
        }
        self.assertEqual(set(positions_by_year), {2023, 2024, 2025})
        self.assertEqual(positions_by_year[2025]["quantity"], "10000")
        self.assertIsNone(positions_by_year[2025]["price"])
        self.assertIsNone(positions_by_year[2025]["amount"])
        self.assertIsNone(positions_by_year[2025]["acquisition_cost_with_commission"])
        self.assertEqual(positions_by_year[2025]["valuation_basis"], "pending_transfer_out_fifo_cost_basis")
        self.assertEqual(positions_by_year[2025]["entry_trade_id"], "UTRANSFERIN_2023_2023.csv:transfer:1")

        dividend = result.dataset.tables["Dividends"][0]
        self.assertEqual(dividend["symbol"], "MAGN")
        self.assertEqual(dividend["country"], "RU")
        self.assertEqual(dividend["gross_amount"], "27520")
        self.assertEqual(dividend["withholding_tax"], "-4128")
        self.assertEqual(dividend["tax"], "2752.00")
        self.assertNotIn("tax_kzt_usd", dividend)

        yearly_dividend = next(row for row in result.dataset.tables["Years_Results"] if row["table"] == "Yearly Dividends")
        self.assertEqual(yearly_dividend["country"], "RU")
        self.assertEqual(yearly_dividend["amount"], "27520.00")
        self.assertEqual(yearly_dividend["withhold_kzt"], "-20640.00")
        self.assertEqual(yearly_dividend["tax_kzt"], "13760.00")
        self.assertEqual(yearly_dividend["tax_kzt_withhold"], "0.00")

        reconciliation = ReconciliationEngine().reconcile_dataset(result.dataset)
        position_errors = [
            item
            for item in reconciliation
            if item.metric == ReconciliationMetric.ENDING_POSITION_QUANTITY
            and item.severity == ReconciliationSeverity.ERROR
        ]
        self.assertEqual(position_errors, [])

    def test_security_transfer_in_uses_resolved_fifo_lots_from_source_workbook(self) -> None:
        seen_requests: list[TransferInRequest] = []

        def resolver(request: TransferInRequest) -> list[TransferInFifoLot]:
            seen_requests.append(request)
            return [
                TransferInFifoLot(
                    quantity=Decimal("4000"),
                    price=Decimal("10"),
                    enter_date=datetime(2021, 6, 1, 10, 0, 0),
                    source_broker="ib",
                    source_file="source.xlsx",
                    source_row=2,
                ),
                TransferInFifoLot(
                    quantity=Decimal("6000"),
                    price=Decimal("20"),
                    enter_date=datetime(2022, 7, 1, 10, 0, 0),
                    source_broker="ib",
                    source_file="source.xlsx",
                    source_row=3,
                ),
            ]

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UTRANSFERIN_2023_2023.csv").write_text(TRANSFER_IN_2023_IB_CSV, encoding="utf-8")
            (ib_root / "UTRANSFERIN_2024_2024.csv").write_text(TRANSFER_IN_2024_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(
                AnnualFxRateProvider({(2023, "RUB"): Decimal("5"), (2024, "RUB"): Decimal("5")}),
                transfer_in_resolver=resolver,
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "UTRANSFERIN"), "UTRANSFERIN")

        self.assertEqual(len(seen_requests), 1)
        self.assertIn("2023-05-01 MAGN RU0009084396 10000", seen_requests[0].prompt())

        transfers = [row for row in result.dataset.tables["Transfers"] if row["symbol"] == "MAGN"]
        self.assertEqual([row["quantity"] for row in transfers], ["4000", "6000"])
        self.assertEqual([row["price"] for row in transfers], ["10", "20"])
        self.assertEqual([row["enter_date"] for row in transfers], ["2021-06-01 10:00:00", "2022-07-01 10:00:00"])
        self.assertTrue(all("fifo_source:source.xlsx" in row["source_report"] for row in transfers))

        positions_2024 = [row for row in result.dataset.tables["Positions"] if row["symbol"] == "MAGN" and row["year"] == 2024]
        self.assertEqual([row["quantity"] for row in positions_2024], ["4000", "6000"])
        self.assertEqual([row["price"] for row in positions_2024], ["10", "20"])
        self.assertEqual([row["enter_date"] for row in positions_2024], ["2021-06-01 10:00:00", "2022-07-01 10:00:00"])
        self.assertEqual({row["valuation_basis"] for row in positions_2024}, {"fifo_lot_cost"})

    def test_security_transfer_out_cancellations_are_net_before_fifo_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UTRANSADJ_2023_2023.csv").write_text(TRANSFER_ADJUSTMENT_2023_IB_CSV, encoding="utf-8")
            (ib_root / "UTRANSADJ_2024_2024.csv").write_text(TRANSFER_ADJUSTMENT_2024_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(
                AnnualFxRateProvider({(2023, "USD"): Decimal("456"), (2024, "USD"): Decimal("469")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "UTRANSADJ"), "UTRANSADJ")

        transfers = [row for row in result.dataset.tables["Transfers"] if row["symbol"] == "AAPL"]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["date"], "2024-01-29")
        self.assertEqual(transfers[0]["direction"], "out")
        self.assertEqual(transfers[0]["quantity"], "10")
        self.assertEqual(transfers[0]["price"], "100.1")
        self.assertEqual(transfers[0]["enter_date"], "2023-01-02 10:00:00")
        self.assertNotIn("Outgoing transfer has no sufficient FIFO opening lots", "\n".join(result.dataset.warnings))

    def test_reversal_trade_code_opens_new_lot_after_short_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UREVERSAL_2020_2020.csv").write_text(REVERSAL_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2020, "USD"): Decimal("413")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UREVERSAL"), "UREVERSAL")

        fifo_rows = [row for row in result.dataset.tables["Fifo"] if row["symbol"] == "CCR"]
        self.assertEqual(len(fifo_rows), 2)
        self.assertEqual(fifo_rows[0]["position_type"], "short")
        self.assertEqual(fifo_rows[0]["enter_date"], "2020-11-05 09:30:00")
        self.assertEqual(fifo_rows[0]["exit_date"], "2020-11-05 16:00:01")
        self.assertEqual(fifo_rows[1]["position_type"], "long")
        self.assertEqual(fifo_rows[1]["enter_date"], "2020-11-05 16:00:01")
        self.assertEqual(fifo_rows[1]["exit_date"], "2020-11-06 09:30:00")
        self.assertEqual([row["quantity"] for row in result.dataset.tables["Positions"] if row["symbol"] == "CCR"], [])
        self.assertEqual([row for row in result.dataset.tables["Unprocessed"] if row["symbol"] == "CCR"], [])

    def test_closing_residual_uses_prior_unknown_lot_instead_of_opening_short(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UPRIOR_2020_2020.csv").write_text(PRIOR_LOT_RESIDUAL_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2020, "USD"): Decimal("413")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UPRIOR"), "UPRIOR")

        fifo_rows = [row for row in result.dataset.tables["Fifo"] if row["symbol"] == "ITA"]
        self.assertEqual(len(fifo_rows), 2)
        self.assertEqual(fifo_rows[0]["_opening_lot_status"], "matched")
        self.assertEqual(fifo_rows[0]["enter_quantity"], "17")
        self.assertEqual(fifo_rows[0]["exit_quantity"], "17")

        prior_row = fifo_rows[1]
        self.assertEqual(prior_row["_opening_lot_status"], "missing_opening_lot")
        self.assertEqual(prior_row["position_type"], "long")
        self.assertIsNone(prior_row["enter_date"])
        self.assertEqual(prior_row["enter_quantity"], "70")
        self.assertIsNotNone(prior_row["enter_price"])
        self.assertEqual(prior_row["exit_date"], "2020-02-28 16:00:00")
        self.assertEqual([row for row in result.dataset.tables["Positions"] if row["symbol"] == "ITA"], [])

    def test_prior_inventory_is_consumed_before_visible_buys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UPRIORINV_2020_2020.csv").write_text(
                PRIOR_INVENTORY_BEFORE_VISIBLE_BUY_IB_CSV,
                encoding="utf-8",
            )

            parser = InteractiveBrokersParser(AnnualFxRateProvider({(2020, "USD"): Decimal("413")}))
            result = parser.parse_reports(parser.discover_reports(raw_root, "UPRIORINV"), "UPRIORINV")

        fifo_rows = [row for row in result.dataset.tables["Fifo"] if row["symbol"] == "FBT"]
        self.assertEqual(len(fifo_rows), 2)
        self.assertEqual([row["_opening_lot_status"] for row in fifo_rows], ["missing_opening_lot", "matched"])
        self.assertEqual(fifo_rows[0]["enter_date"], "2020-01-01 00:00:00")
        self.assertEqual(fifo_rows[0]["enter_quantity"], "5")
        self.assertEqual(fifo_rows[0]["exit_quantity"], "5")
        self.assertEqual(fifo_rows[1]["enter_date"], "2020-06-01 09:30:00")
        self.assertEqual(fifo_rows[1]["enter_quantity"], "1")
        self.assertEqual(fifo_rows[1]["exit_quantity"], "1")

    def test_cash_merger_corporate_action_creates_fifo_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "UMERGER_2021_2021.csv").write_text(CASH_MERGER_2021_IB_CSV, encoding="utf-8")
            (ib_root / "UMERGER_2022_2022.csv").write_text(CASH_MERGER_2022_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(
                AnnualFxRateProvider({(2021, "USD"): Decimal("426.03"), (2022, "USD"): Decimal("460.48")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "UMERGER"), "UMERGER")

        fifo_rows = [row for row in result.dataset.tables["Fifo"] if row["symbol"] == "TWTR"]
        twtr_trades = [row for row in result.dataset.tables["Trades"] if row["symbol"] == "TWTR"]
        self.assertEqual(len(twtr_trades), 3)
        self.assertEqual(twtr_trades[-1]["date_time"], "2022-10-28 20:25:00")
        self.assertEqual(twtr_trades[-1]["quantity"], "-40")
        self.assertEqual(twtr_trades[-1]["price"], "54.2")
        self.assertEqual(twtr_trades[-1]["amount"], "2168")
        self.assertEqual([row["trade_type"] for row in twtr_trades], ["trade", "trade", "corporate_action:merged"])
        self.assertTrue(str(twtr_trades[-1]["source_report"]).startswith("corporate_action:"))
        self.assertEqual(len(fifo_rows), 2)
        self.assertTrue(all(str(row["source_trade_id"]).startswith("CA:") for row in fifo_rows))
        self.assertEqual([row["exit_date"] for row in fifo_rows], ["2022-10-28 20:25:00", "2022-10-28 20:25:00"])
        self.assertEqual([row["exit_quantity"] for row in fifo_rows], ["20", "20"])
        self.assertEqual([row["exit_price"] for row in fifo_rows], ["54.2", "54.2"])
        self.assertEqual(sum(Decimal(row["pnl_after_all_commissions"]) for row in fifo_rows), Decimal("-5.00"))
        self.assertEqual([row for row in result.dataset.tables["Positions"] if row["symbol"] == "TWTR" and row["year"] >= 2022], [])

        yearly_twtr = [
            row
            for row in result.dataset.tables["Years_Results"]
            if row["table"] == "Yearly Trades" and row["year"] == 2022 and row["currency"] == "USD"
        ]
        self.assertEqual(yearly_twtr[0]["pnl"], "-5.00")

    def test_stock_spinoff_and_cash_stock_merger_rewrite_history_before_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "USTOCKCA_2021_2021.csv").write_text(STOCK_CA_2021_IB_CSV, encoding="utf-8")
            (ib_root / "USTOCKCA_2022_2022.csv").write_text(STOCK_CA_2022_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(
                AnnualFxRateProvider({(2021, "USD"): Decimal("426.03"), (2022, "USD"): Decimal("460.48")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "USTOCKCA"), "USTOCKCA")

        trades = result.dataset.tables["Trades"]
        self.assertEqual([row for row in trades if row["symbol"] == "ERUS"], [])
        erus_esc_trades = [row for row in trades if row["symbol"] == "ERUS.ESC"]
        self.assertEqual([row["isin"] for row in erus_esc_trades], ["US464ESC0112", "US464ESC0112", "US464ESC0112"])
        self.assertEqual([row["quantity"] for row in erus_esc_trades], ["30", "25", "-0.6097"])
        self.assertEqual([row for row in trades if row["trade_type"] == "corporate_action:merger"], [])

        wbd_trades = [row for row in trades if row["symbol"] == "WBD"]
        self.assertEqual(len(wbd_trades), 1)
        self.assertEqual(wbd_trades[0]["trade_type"], "corporate_action:spinoff")
        self.assertEqual(wbd_trades[0]["isin"], "US9344231041")
        self.assertEqual(wbd_trades[0]["quantity"], "24.1917")
        self.assertEqual(wbd_trades[0]["price"], "0")

        fifo_rows = [row for row in result.dataset.tables["Fifo"] if row["symbol"] == "ERUS.ESC"]
        self.assertEqual(len(fifo_rows), 1)
        self.assertEqual(fifo_rows[0]["enter_date"], "2021-11-23 10:15:44")
        self.assertEqual(fifo_rows[0]["exit_quantity"], "0.6097")
        self.assertEqual([row for row in result.dataset.tables["Unprocessed"] if row["symbol"] == "ERUS.ESC"], [])

        positions = result.dataset.tables["Positions"]
        wbd_positions = [row for row in positions if row["symbol"] == "WBD"]
        self.assertEqual([row["year"] for row in wbd_positions], [2022])
        self.assertEqual(wbd_positions[0]["quantity"], "24.1917")
        self.assertEqual(wbd_positions[0]["price"], "0")

        erus_esc_2022 = [row for row in positions if row["symbol"] == "ERUS.ESC" and row["year"] == 2022]
        self.assertEqual(sum(Decimal(row["quantity"]) for row in erus_esc_2022), Decimal("54.3903"))
        self.assertTrue(all(row["enter_date"] for row in erus_esc_2022))

    def test_symbol_change_is_inferred_without_synthetic_trade_and_positions_use_current_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            ib_root = raw_root / "ib"
            ib_root.mkdir(parents=True)
            (ib_root / "URENAME_2024_2024.csv").write_text(SYMBOL_CHANGE_2024_IB_CSV, encoding="utf-8")
            (ib_root / "URENAME_20250101_20250724.csv").write_text(SYMBOL_CHANGE_2025_IB_CSV, encoding="utf-8")

            parser = InteractiveBrokersParser(
                AnnualFxRateProvider({(2024, "USD"): Decimal("469.44"), (2025, "USD"): Decimal("521.59")})
            )
            result = parser.parse_reports(parser.discover_reports(raw_root, "URENAME"), "URENAME")

        symbol_changes = [
            row for row in result.dataset.tables["CorporateActions"] if row["action_type"] == "symbol_change"
        ]
        self.assertEqual(len(symbol_changes), 1)
        self.assertEqual(symbol_changes[0]["symbol"], "XYZ")
        self.assertEqual(symbol_changes[0]["isin"], "US8522341036")
        self.assertIn("SQ -> XYZ", symbol_changes[0]["description"])
        self.assertTrue(str(symbol_changes[0]["source_report"]).startswith("inferred:financial_instrument_information:"))

        trades = result.dataset.tables["Trades"]
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "SQ")
        self.assertEqual(trades[0]["trade_type"], "trade")
        self.assertFalse(any(str(row["trade_id"]).startswith("CA:") for row in trades))

        positions_by_year = {
            row["year"]: row for row in result.dataset.tables["Positions"] if row["isin"] == "US8522341036"
        }
        self.assertEqual(positions_by_year[2024]["symbol"], "SQ")
        self.assertEqual(positions_by_year[2025]["symbol"], "XYZ")
        self.assertEqual(positions_by_year[2025]["entry_trade_id"], "URENAME_2024_2024.csv:1")

    def test_yearly_interest_taxes_only_positive_interest_not_margin_expense_net(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UINT")
        dataset.tables["Interest"] = [
            {
                "date": "2024-01-10",
                "currency": "USD",
                "gross_amount": "-100",
                "gross_amount_kzt": "-47000",
                "withholding_tax_kzt": "0",
            },
            {
                "date": "2024-02-10",
                "currency": "USD",
                "gross_amount": "30",
                "gross_amount_kzt": "14100",
                "withholding_tax_kzt": "0",
            },
        ]
        yearly = ib_module._build_years_results(dataset)
        interest_rows = [row for row in yearly if row["table"] == "Yearly Interest"]
        self.assertEqual(len(interest_rows), 1)
        self.assertEqual(interest_rows[0]["amount"], "-70.00")
        self.assertEqual(interest_rows[0]["amount_kzt"], "-32900.00")
        self.assertEqual(interest_rows[0]["only_profit"], "30.00")
        self.assertEqual(interest_rows[0]["only_profit_kzt"], "14100.00")
        self.assertEqual(interest_rows[0]["tax_kzt"], "1410.00")
        self.assertNotIn("withhold_kzt", interest_rows[0])
        self.assertNotIn("tax_kzt_withhold", interest_rows[0])

    def test_yearly_trades_do_not_group_by_hidden_country(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UKEY")
        dataset.tables["Fifo"] = [
            {
                "exit_date": "2024-01-01 10:00:00",
                "country": "US",
                "currency": "USD",
                "pnl": "10",
                "pnl_kzt": "4700",
            },
            {
                "exit_date": "2024-01-02 10:00:00",
                "country": "CA",
                "currency": "USD",
                "pnl": "20",
                "pnl_kzt": "9400",
            },
        ]
        yearly = ib_module._build_years_results(dataset)
        trade_rows = [row for row in yearly if row["table"] == "Yearly Trades"]
        self.assertEqual(len(trade_rows), 1)
        self.assertEqual(trade_rows[0]["pnl"], "30.00")
        self.assertEqual(trade_rows[0]["pnl_kzt"], "14100.00")
        self.assertIsNone(trade_rows[0]["country"])
        self.assertEqual(trade_rows[0]["flag"], "non-preferential")
        self.assertEqual(trade_rows[0]["exchange"], "outofKZ")

    def test_aix_security_is_preferential_and_offshore_tax_uses_proceeds(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UCLASS")
        dataset.tables["Fifo"] = [
            {
                "exit_date": "2024-01-01 10:00:00",
                "asset_type": "Stocks",
                "symbol": "AIXTEST",
                "isin": "US0000000001",
                "country": "US",
                "currency": "USD",
                "pnl": "100",
                "pnl_kzt": "47000",
                "exit_amount_kzt": "470000",
            },
            {
                "exit_date": "2024-02-01 10:00:00",
                "asset_type": "Stocks",
                "symbol": "OFFSHORE",
                "isin": "BS0000000001",
                "country": "BS",
                "currency": "USD",
                "pnl": "-10",
                "pnl_kzt": "-4700",
                "exit_amount": "1000",
                "kzt_rate": "470",
            },
        ]
        yearly = ib_module._build_years_results(
            dataset,
            aix_provider=AixInstrumentProvider({2024: frozenset({"US0000000001"})}),
            offshore_provider=OffshoreJurisdictionProvider(frozenset({"BS"})),
        )
        trade_rows = {(row["flag"], row["exchange"]): row for row in yearly if row["table"] == "Yearly Trades"}
        self.assertEqual(trade_rows[("preferential", "AIX")]["tax_kzt"], "0.00")
        self.assertEqual(trade_rows[("offshore", "outofKZ")]["pnl"], "-10.00")
        self.assertEqual(trade_rows[("offshore", "outofKZ")]["pnl_kzt"], "470000.00")
        self.assertEqual(trade_rows[("offshore", "outofKZ")]["tax_kzt"], "47000.00")

    def test_yearly_derivatives_tax_only_profitable_fifo_rows(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UDER")
        dataset.tables["Fifo"] = [
            {
                "exit_date": "2024-01-01 10:00:00",
                "asset_type": "Equity and Index Options",
                "symbol": "SPY 19JAN24 100 C",
                "currency": "USD",
                "pnl": "1",
                "pnl_kzt": "100",
            },
            {
                "exit_date": "2024-01-02 10:00:00",
                "asset_type": "Equity and Index Options",
                "symbol": "SPY 19JAN24 100 P",
                "currency": "USD",
                "pnl": "-0.8",
                "pnl_kzt": "-80",
            },
        ]
        yearly = ib_module._build_years_results(
            dataset,
            aix_provider=AixInstrumentProvider({}),
            offshore_provider=OffshoreJurisdictionProvider(frozenset()),
        )
        derivative_rows = [row for row in yearly if row["table"] == "Yearly Derivatives"]
        self.assertEqual(len(derivative_rows), 1)
        self.assertEqual(derivative_rows[0]["flag"], "non-preferential")
        self.assertEqual(derivative_rows[0]["exchange"], "outofKZ")
        self.assertEqual(derivative_rows[0]["pnl"], "0.20")
        self.assertEqual(derivative_rows[0]["pnl_kzt"], "20.00")
        self.assertEqual(derivative_rows[0]["only_profit"], "1.00")
        self.assertEqual(derivative_rows[0]["only_profit_kzt"], "100.00")
        self.assertEqual(derivative_rows[0]["tax_kzt"], "10.00")

    def test_yearly_bonds_redemption_is_not_taxable(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UCORP")
        dataset.tables["Fifo"] = [
            {
                "exit_date": "2023-01-16 20:25:00",
                "country": "US",
                "currency": "USD",
                "pnl": "100",
                "pnl_kzt": "45000",
                "source_trade_id": "CA:bond-maturity",
                "corporate_action_type": "maturity",
            }
        ]
        yearly = ib_module._build_years_results(dataset)
        corp_rows = [row for row in yearly if row["table"] == "Yearly Bonds Redemption"]
        self.assertEqual(len(corp_rows), 1)
        self.assertEqual(corp_rows[0]["pnl"], "100.00")
        self.assertEqual(corp_rows[0]["tax_kzt"], "0.00")

    def test_yearly_coupons_are_not_taxable(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UCOUPON")
        dataset.tables["Coupons"] = [
            {
                "date": "2023-06-30",
                "country": "US",
                "currency": "USD",
                "gross_amount": "100",
                "gross_amount_kzt": "45000",
                "withholding_tax_kzt": "0",
            }
        ]
        yearly = ib_module._build_years_results(dataset)
        coupon_rows = [row for row in yearly if row["table"] == "Yearly Coupons"]
        self.assertEqual(len(coupon_rows), 1)
        self.assertEqual(coupon_rows[0]["amount"], "100.00")
        self.assertEqual(coupon_rows[0]["tax_kzt"], "0.00")
        self.assertEqual(coupon_rows[0]["tax_kzt_withhold"], "0.00")

    def test_kz_yearly_coupons_keep_amount_but_zero_kzt_columns(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UKZCOUPON")
        dataset.tables["Instruments"] = [
            {
                "symbol": "KZBOND",
                "isin": "KZ0000000001",
                "issuer_country": "KZ",
                "issuer_outside_kz_flag": False,
            }
        ]
        dataset.tables["Coupons"] = [
            {
                "date": "2024-06-30",
                "symbol": "KZBOND",
                "isin": "KZ0000000001",
                "country": "KZ",
                "currency": "USD",
                "gross_amount": "100",
                "gross_amount_kzt": "45000",
                "withholding_tax_kzt": "0",
            }
        ]
        yearly = ib_module._build_years_results(dataset)
        coupon_rows = [row for row in yearly if row["table"] == "Yearly Coupons"]
        self.assertEqual(len(coupon_rows), 1)
        self.assertEqual(coupon_rows[0]["flag"], "preferential")
        self.assertEqual(coupon_rows[0]["amount"], "100.00")
        self.assertEqual(coupon_rows[0]["amount_kzt"], "0.00")
        self.assertEqual(coupon_rows[0]["withhold_kzt"], "0.00")
        self.assertEqual(coupon_rows[0]["tax_kzt"], "0.00")
        self.assertEqual(coupon_rows[0]["tax_kzt_withhold"], "0.00")

    def test_kz_yearly_dividends_keep_amount_but_zero_reporting_columns(self) -> None:
        dataset = CanonicalDataset.empty("ib", "UKZDIV")
        dataset.tables["Instruments"] = [
            {
                "symbol": "KZDIV",
                "isin": "KZ0000000001",
                "issuer_country": "KZ",
                "issuer_outside_kz_flag": False,
            }
        ]
        dataset.tables["Dividends"] = [
            {
                "date": "2024-06-30",
                "pay_date": "2024-06-30",
                "symbol": "KZDIV",
                "isin": "KZ0000000001",
                "country": "KZ",
                "currency": "USD",
                "gross_amount": "100",
                "withholding_tax": "0",
                "gross_amount_kzt": "45000",
                "withholding_tax_kzt": "0",
                "tax": "4500",
                "tax_kzt": "4500",
            }
        ]
        yearly = ib_module._build_years_results(dataset)
        dividend_rows = [row for row in yearly if row["table"] == "Yearly Dividends"]
        self.assertEqual(len(dividend_rows), 1)
        self.assertEqual(dividend_rows[0]["flag"], "preferential")
        self.assertEqual(dividend_rows[0]["amount"], "100.00")
        self.assertEqual(dividend_rows[0]["amount_kzt"], "0.00")
        self.assertEqual(dividend_rows[0]["withhold_kzt"], "0.00")
        self.assertEqual(dividend_rows[0]["tax_kzt"], "0.00")
        self.assertEqual(dividend_rows[0]["tax_kzt_withhold"], "0.00")


if __name__ == "__main__":
    unittest.main()
