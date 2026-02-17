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
│   │   └── quant/
│   │       └── simulation.py
│   │
│   ├── collectors/
│   │   ├── __init__.py                  # re-export (service/* → 외부 참조 유지)
│   │   ├── clients/
│   │   │   ├── alpaca.py
│   │   │   ├── dart.py
│   │   │   ├── ecos.py
│   │   │   ├── finnhub.py               # Finnhub API (US 섹터 fallback)
│   │   │   ├── fred.py
│   │   │   ├── nasdaq_screener.py       # NASDAQ Screener API (US 벌크 섹터)
│   │   │   ├── pykrx.py
│   │   │   └── yfinance.py
│   │   ├── service/
│   │   │   ├── benchmark_price.py
│   │   │   ├── kr_daily_price.py
│   │   │   ├── kr_financial_statement.py
│   │   │   ├── risk_free_rate.py
│   │   │   ├── sector.py                # NASDAQ Screener + Finnhub fallback
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
│   │       ├── factor.py               # factor_exposures, factor_returns, factor_covariance, sector_aggregates
│   │       ├── financial_statement.py
│   │       ├── fundamental.py
│   │       ├── indicator.py
│   │       ├── risk_free_rate.py
│   │       └── stock.py                # + get_eligible_for_factors(), get_integrity_stats()
│   │
│   ├── pipeline/
│   │   ├── __main__.py                 # CLI 진입점 (kr|us|all|kr-fs|us-fs|full|sectors)
│   │   ├── factor_compute.py           # → FactorModelService.run()
│   │   ├── fundamental_compute.py      # → FundamentalService.compute()
│   │   ├── indicator_compute.py        # → FactorModelService.get_betas() + IndicatorService.compute()
│   │   ├── integrity_check.py          # → IntegrityCheckService.check()
│   │   ├── orchestrator.py             # 전체 파이프라인 순서 조율
│   │   └── sector_aggregate_compute.py # → SectorAggregateService.compute()
│   │
│   ├── services/
│   │   ├── factor_model_service.py          # 멀티팩터 리스크 모델 (노출도, 회귀, 공분산, 베타)
│   │   ├── fundamental_collection_service.py
│   │   ├── fundamental_service.py           # 펀더멘털 지표 계산 (PER, PBR, ROE 등)
│   │   ├── indicator_service.py             # 기술적 지표 23개 계산
│   │   ├── integrity_check_service.py       # 데이터 무결성 보고 (읽기 전용)
│   │   ├── price_collection_service.py
│   │   ├── sector_aggregate_service.py      # 섹터별 중위수 집계
│   │   └── simulation_service.py
│   │
│   ├── quant/
│   │   ├── factor_model/                # Barra 멀티팩터 리스크 모델
│   │   │   ├── beta.py                  # factor_beta(), ols_beta(), build_exposure_vector()
│   │   │   ├── covariance.py            # ewm_factor_covariance(), ewm_specific_variance()
│   │   │   ├── exposure.py              # compute_exposures(), build_design_matrix()
│   │   │   ├── normalize.py             # winsorize(), z_score()
│   │   │   └── regression.py            # constrained_wls() (라그랑주 산업 제약)
│   │   ├── fundamentals/
│   │   │   ├── profitability.py
│   │   │   ├── stability.py
│   │   │   └── valuation.py
│   │   ├── indicators/
│   │   │   ├── momentum.py
│   │   │   ├── moving_average.py
│   │   │   ├── risk.py                  # alpha(), sharpe() (베타는 factor_model/beta.py로 이전)
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
│   │       ├── data_coverage.py
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
├── docs/
│   ├── fundamental-pipeline-story.md
│   ├── indicator-optimization-story.md
│   ├── multi-factor-risk-model-story.md
│   ├── sector_classification.md
│   ├── saramquant_directory_structure.md
│   ├── saramquant_general_plan.md
│   ├── issues-2026-02-16.md
│   ├── test-kr-workflow-initial-2026-02-16.md
│   └── test-us-workflow-initial-2026-02-16.md
│
└── tests/
    └── data_source_test/
        └── test_all_sources.py
```
