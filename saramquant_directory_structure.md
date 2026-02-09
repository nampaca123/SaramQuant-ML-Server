# SaramQuant ML Server - Directory Structure

```
saramquant-ml-server/
├── run.py
├── requirements.txt
├── db_table.sql
│
├── app/
│   ├── api/
│   │   └── __init__.py
│   │
│   ├── collectors/
│   │   ├── clients/
│   │   │   ├── alpaca.py
│   │   │   ├── ecos.py
│   │   │   ├── fred.py
│   │   │   ├── pykrx.py
│   │   │   └── yfinance.py
│   │   ├── benchmark_price.py
│   │   ├── kr_daily_price.py
│   │   ├── risk_free_rate.py
│   │   ├── stock_list.py
│   │   └── us_daily_price.py
│   │
│   ├── db/
│   │   ├── connection.py
│   │   └── repositories/
│   │       ├── benchmark.py
│   │       ├── daily_price.py
│   │       ├── indicator.py
│   │       ├── risk_free_rate.py
│   │       └── stock.py
│   │
│   ├── pipeline/
│   │   ├── __main__.py
│   │   ├── compute.py
│   │   └── orchestrator.py
│   │
│   ├── services/
│   │   ├── indicator_service.py
│   │   └── price_collection_service.py
│   │
│   ├── quant/
│   │   └── indicators/
│   │       ├── momentum.py
│   │       ├── moving_average.py
│   │       ├── risk.py
│   │       ├── trend.py
│   │       ├── volatility.py
│   │       └── volume.py
│   │
│   ├── schema/
│   │   ├── data_sources/
│   │   │   ├── alpaca.py
│   │   │   ├── kis.py
│   │   │   ├── pykrx.py
│   │   │   └── yfinance.py
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
│   └── utils/
│       ├── quant/
│       │   └── market_reference_data.py
│       └── system/
│           ├── errors.py
│           ├── logging_config.py
│           └── retry.py
│
└── tests/
    └── data_source_test/
        └── test_all_sources.py
```
