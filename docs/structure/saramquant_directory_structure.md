# SaramQuant Calc Server - Directory Structure

```
saramquant-calc-server/
├── run.py
├── requirements.txt
├── db_table.sql
├── Dockerfile
│
├── app/
│   ├── api/
│   │   ├── portfolio/
│   │   │   ├── analysis.py             # 리스크 점수, 리스크 분해, 분산도
│   │   │   ├── price_lookup.py         # 날짜별 종가 + 환율 조회
│   │   │   └── simulation.py           # 포트폴리오 몬테카를로 시뮬레이션
│   │   └── quant/
│   │       └── simulation.py           # 종목 몬테카를로 시뮬레이션
│   │
│   ├── collectors/
│   │   ├── clients/
│   │   │   ├── alpaca.py
│   │   │   ├── dart.py
│   │   │   ├── ecos.py
│   │   │   ├── finnhub.py
│   │   │   ├── fred.py
│   │   │   ├── nasdaq_screener.py
│   │   │   ├── pykrx.py
│   │   │   └── yfinance.py
│   │   ├── service/
│   │   │   ├── benchmark_price.py
│   │   │   ├── exchange_rate.py        # 환율 수집 (KRW/USD)
│   │   │   ├── kr_daily_price.py
│   │   │   ├── kr_financial_statement.py
│   │   │   ├── risk_free_rate.py
│   │   │   ├── sector.py               # KR/US 섹터 수집 (통합)
│   │   │   ├── stock_list.py           # KR/US 종목 목록 수집 (통합)
│   │   │   └── us_daily_price.py
│   │   └── utils/
│   │       ├── market_groups.py
│   │       ├── skip_rules.py
│   │       └── throttle.py
│   │
│   ├── db/
│   │   ├── connection.py
│   │   └── repositories/
│   │       ├── benchmark.py
│   │       ├── daily_price.py
│   │       ├── exchange_rate.py
│   │       ├── factor.py
│   │       ├── financial_statement.py
│   │       ├── fundamental.py
│   │       ├── indicator.py
│   │       ├── portfolio.py
│   │       ├── risk_badge.py
│   │       ├── risk_free_rate.py
│   │       └── stock.py
│   │
│   ├── log/
│   │   ├── model/
│   │   │   ├── audit_log.py            # AuditLogEntry dataclass
│   │   │   └── pipeline_metadata.py    # StepResult, PipelineMetadata dataclass
│   │   ├── repository/
│   │   │   └── audit_log_repository.py # psycopg2 INSERT (try-except 격리)
│   │   ├── service/
│   │   │   └── audit_log_service.py    # log_api(), log_pipeline()
│   │   └── middleware/
│   │       └── audit_middleware.py      # Flask before/after/teardown_request
│   │
│   ├── pipeline/
│   │   ├── __main__.py                 # CLI 진입점
│   │   ├── orchestrator.py             # 파이프라인 오케스트레이터 (StepResult 수집 + 감사 로깅)
│   │   ├── factor_compute.py
│   │   ├── fundamental_compute.py
│   │   ├── indicator_compute.py
│   │   ├── integrity_check.py
│   │   └── sector_aggregate_compute.py
│   │
│   ├── quant/
│   │   ├── factor_model/
│   │   │   ├── beta.py
│   │   │   ├── covariance.py
│   │   │   ├── exposure.py
│   │   │   ├── normalize.py
│   │   │   └── regression.py
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
│   │   ├── portfolio/
│   │   │   ├── diversification.py
│   │   │   ├── hypothetical_returns.py
│   │   │   ├── portfolio_metrics.py
│   │   │   ├── portfolio_risk_score.py
│   │   │   └── risk_contribution.py
│   │   ├── risk_badge/
│   │   │   ├── badge_scoring.py
│   │   │   ├── badge_types.py
│   │   │   ├── composite_badge.py
│   │   │   ├── dimension_company_health.py
│   │   │   ├── dimension_price_heat.py
│   │   │   ├── dimension_trend.py
│   │   │   ├── dimension_valuation.py
│   │   │   └── dimension_volatility.py
│   │   └── simulation/
│   │       ├── monte_carlo.py
│   │       ├── path_generator.py
│   │       └── portfolio_path_generator.py
│   │
│   ├── schema/
│   │   ├── dto/
│   │   │   ├── financial_statement.py
│   │   │   ├── price.py
│   │   │   ├── risk.py
│   │   │   └── stock.py
│   │   ├── enums/
│   │   │   ├── benchmark.py
│   │   │   ├── country.py
│   │   │   ├── data_coverage.py
│   │   │   ├── data_source.py
│   │   │   ├── market.py
│   │   │   ├── maturity.py
│   │   │   └── report_type.py
│   │   └── data_sources/
│   │       ├── alpaca.py
│   │       ├── kis.py
│   │       ├── pykrx.py
│   │       └── yfinance.py
│   │
│   ├── services/
│   │   ├── factor_model_service.py
│   │   ├── fundamental_collection_service.py
│   │   ├── fundamental_service.py
│   │   ├── historical_price_lookup.py
│   │   ├── indicator_service.py
│   │   ├── integrity_check_service.py
│   │   ├── portfolio_analysis_service.py
│   │   ├── portfolio_simulation_service.py
│   │   ├── price_collection_service.py
│   │   ├── risk_badge_service.py
│   │   ├── sector_aggregate_service.py
│   │   └── simulation_service.py
│   │
│   └── utils/
│       ├── quant/
│       │   └── market_reference_data.py
│       └── system/
│           ├── errors.py
│           ├── logging_config.py
│           └── retry.py
│
├── docs/
│   ├── structure/
│   │   ├── portfolio_system.md
│   │   ├── risk_badge_system.md
│   │   ├── saramquant_directory_structure.md
│   │   └── saramquant_general_plan.md
│   ├── problem-solution/
│   │   ├── fundamental-pipeline-story.md
│   │   ├── indicator-optimization-story.md
│   │   ├── issues-2026-02-16.md
│   │   └── multi-factor-risk-model-story.md
│   └── test-results/
│       └── *.md
│
├── logs/
│   └── pipeline.log
│
└── tests/
    └── data_source_test/
        └── test_all_sources.py
```
