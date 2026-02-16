# SaramQuant ML Server - Directory Structure

```
saramquant-ml-server/
├── run.py
├── requirements.txt
├── db_table.sql
│
├── app/
│   ├── api/
│   │   ├── __init__.py
│   │   └── quant/
│   │       └── simulation.py
│   │
│   ├── collectors/
│   │   ├── __init__.py                  # re-export (service/* → 외부 참조 유지)
│   │   ├── clients/
│   │   │   ├── alpaca.py
│   │   │   ├── dart.py
│   │   │   ├── ecos.py
│   │   │   ├── fred.py
│   │   │   ├── nasdaq_screener.py       # NASDAQ Screener API (US 벌크 섹터)
│   │   │   ├── pykrx.py
│   │   │   └── yfinance.py
│   │   ├── service/
│   │   │   ├── benchmark_price.py
│   │   │   ├── kr_daily_price.py
│   │   │   ├── kr_financial_statement.py
│   │   │   ├── risk_free_rate.py
│   │   │   ├── sector.py
│   │   │   ├── stock_list.py
│   │   │   └── us_daily_price.py
│   │   └── utils/
│   │       ├── market_groups.py         # KR_MARKETS, US_MARKETS, MARKET_TO_PYKRX
│   │       ├── skip_rules.py            # SKIP_INDICES, 종목 스킵 판별 함수
│   │       └── throttle.py              # Throttle 공유 유틸리티
│   │
│   ├── db/
│   │   ├── connection.py
│   │   └── repositories/
│   │       ├── benchmark.py
│   │       ├── daily_price.py
│   │       ├── financial_statement.py
│   │       ├── fundamental.py
│   │       ├── indicator.py
│   │       ├── risk_free_rate.py
│   │       └── stock.py
│   │
│   ├── pipeline/
│   │   ├── __main__.py
│   │   ├── fundamental_compute.py
│   │   ├── indicator_compute.py
│   │   └── orchestrator.py
│   │
│   ├── services/
│   │   ├── fundamental_collection_service.py
│   │   ├── fundamental_service.py
│   │   ├── indicator_service.py
│   │   ├── price_collection_service.py
│   │   └── simulation_service.py
│   │
│   ├── quant/
│   │   ├── fundamentals/
│   │   │   ├── profitability.py
│   │   │   ├── stability.py
│   │   │   └── valuation.py
│   │   ├── indicators/
│   │   │   ├── momentum.py
│   │   │   ├── moving_average.py
│   │   │   ├── risk.py
│   │   │   ├── trend.py
│   │   │   ├── volatility.py
│   │   │   └── volume.py
│   │   └── simulation/
│   │       ├── monte_carlo.py
│   │       └── path_generator.py
│   │
│   ├── schema/
│   │   ├── data_sources/
│   │   │   ├── alpaca.py
│   │   │   ├── kis.py
│   │   │   ├── pykrx.py
│   │   │   └── yfinance.py
│   │   ├── dto/
│   │   │   ├── financial_statement.py
│   │   │   ├── price.py
│   │   │   ├── risk.py
│   │   │   └── stock.py
│   │   └── enums/
│   │       ├── benchmark.py
│   │       ├── country.py
│   │       ├── data_source.py
│   │       ├── market.py
│   │       ├── maturity.py
│   │       └── report_type.py
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
