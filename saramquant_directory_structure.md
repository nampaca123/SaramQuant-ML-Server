# SaramQuant ML Server - Directory Structure

```
saramquant-ml-server/
├── run.py
├── requirements.txt
├── db_table.sql
│
├── app/
│   ├── api/
│   │   └── quant/
│   │       ├── indicators.py
│   │       ├── prices.py
│   │       ├── risk.py
│   │       └── stocks.py
│   │
│   ├── collectors/
│   │   ├── clients/
│   │   │   ├── ecos.py
│   │   │   └── fred.py
│   │   ├── benchmark_price.py
│   │   ├── daily_price.py
│   │   ├── risk_free_rate.py
│   │   └── stock_list.py
│   │
│   ├── db/
│   │   ├── connection.py
│   │   └── repository.py
│   │
│   ├── quant/
│   │   └── indicators/
│   │       ├── momentum.py      # RSI, MACD, Stochastic
│   │       ├── moving_average.py # SMA, EMA, WMA
│   │       ├── risk.py          # Beta, Alpha(Jensen's), Sharpe Ratio
│   │       ├── trend.py         # Parabolic SAR
│   │       ├── volatility.py    # Bollinger Bands, ATR, ADX
│   │       └── volume.py        # OBV, VMA
│   │
│   ├── schema/
│   │   ├── data_sources/
│   │   │   ├── fdr.py
│   │   │   └── kis.py
│   │   ├── dto/
│   │   │   ├── price.py
│   │   │   ├── risk.py
│   │   │   └── stock.py
│   │   └── enums/
│   │       ├── benchmark.py
│   │       ├── country.py
│   │       ├── data_source.py
│   │       ├── market.py
│   │       └── maturity.py
│   │
│   ├── services/
│   │   ├── indicator_service.py
│   │   ├── price_service.py
│   │   └── risk_service.py
│   │
│   └── utils/
│       ├── parser/
│       │   └── request.py
│       └── system/
│           ├── errors.py
│           ├── logging_config.py
│           └── retry.py
│
└── tests/
    ├── collectors/
    │   ├── cli.py
    │   ├── run_cli.py
    │   ├── run_daily_price.py
    │   └── run_stock_list.py
    └── data_source_test/
        └── test_all_sources.py
```
