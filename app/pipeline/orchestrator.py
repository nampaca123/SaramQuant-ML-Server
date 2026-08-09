import logging
import time
from typing import Callable, Any
from concurrent.futures import ThreadPoolExecutor

from app.db import get_connection, DailyPriceRepository, lake_writer
from app.db.repositories.stock import StockRepository
from app.db.repositories.indicator import COLUMNS as _IND_COLUMNS
from app.db.repositories.lake_rows import resolve_run_id
from app.schema import Market
from app.services import PriceCollectionService
from app.services.price_collection_service import REGION_CONFIG
from app.services.fundamental_collection_service import FundamentalCollectionService
from app.pipeline.indicator_compute import IndicatorComputeEngine
from app.pipeline.fundamental_compute import FundamentalComputeEngine
from app.pipeline.factor_compute import FactorComputeEngine
from app.pipeline.sector_aggregate_compute import SectorAggregateComputeEngine
from app.pipeline.integrity_check import IntegrityCheckEngine
from app.collectors.service.exchange_rate import ExchangeRateCollector
from app.schema import StepResult, PipelineMetadata
from app.log.service.audit_log_service import log_pipeline

logger = logging.getLogger(__name__)

PriceMaps = dict[Market, dict[int, list[tuple]]]
_SAFETY_THRESHOLD = 0.10

FS_TABLES = ["stocks", "financial_statements", "stock_fundamentals"]
COMPUTE_TABLES = FS_TABLES + [
    "daily_prices", "benchmark_daily_prices", "risk_free_rates", "exchange_rates",
    "stock_indicators", "factor_exposures", "sector_aggregates", "risk_badges",
]


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _indicator_rows_to_dicts(
    rows: list[tuple], price_maps: PriceMaps, stock_market_map: dict[int, str],
) -> dict[str, dict[int, dict]]:
    close_by_id: dict[int, float] = {}
    for pm in price_maps.values():
        for sid, prices in pm.items():
            if prices:
                close_by_id[sid] = float(prices[-1][4])

    by_market: dict[str, dict[int, dict]] = {}
    for row in rows:
        d = dict(zip(_IND_COLUMNS, row))
        d["close"] = close_by_id.get(d["stock_id"])
        mkt = stock_market_map[d["stock_id"]]
        by_market.setdefault(mkt, {})[d["stock_id"]] = d
    return by_market


class PipelineOrchestrator:
    def __init__(self):
        self._collector = PriceCollectionService()
        self._fund_collector = FundamentalCollectionService()
        self._exchange_rate_collector = ExchangeRateCollector()
        self._run_id = resolve_run_id(None)

    # ── public entry points ──

    def run_daily_kr(self) -> None:
        self._run_command("kr", lambda steps: self._daily(steps, "kr", True), COMPUTE_TABLES)

    def run_daily_us(self) -> None:
        self._run_command("us", lambda steps: self._daily(steps, "us", False), COMPUTE_TABLES)

    def run_initial_kr(self) -> None:
        self._run_command("kr-initial", lambda steps: self._initial(steps, "kr"), COMPUTE_TABLES)

    def run_initial_us(self) -> None:
        self._run_command("us-initial", lambda steps: self._initial(steps, "us"), COMPUTE_TABLES)

    def run_collect_fs_kr(self) -> None:
        self._run_command("kr-fs", lambda steps: self._collect_fs(steps, "kr"), FS_TABLES)

    def run_collect_fs_us(self) -> None:
        self._run_command("us-fs", lambda steps: self._collect_fs(steps, "us"), FS_TABLES)

    # ── run wrapper (run record is written exactly once, even on failure) ──

    def _run_command(
        self, command: str, body: Callable[[list[StepResult]], bool], tables: list[str],
    ) -> None:
        steps: list[StepResult] = []
        start = time.monotonic()
        aborted = True
        try:
            aborted = not body(steps)
        except Exception as e:
            steps.append(StepResult("pipeline", False, _ms(start), str(e)))
            raise
        finally:
            if any(step.success for step in steps):
                lake_writer.optimize_and_vacuum(tables)
            self._log_pipeline_audit(command, steps, start, aborted)

    def _log_pipeline_audit(
        self, command: str, steps: list[StepResult], start: float, aborted: bool,
    ) -> None:
        meta = PipelineMetadata(
            command=command,
            steps=steps,
            total_duration_ms=_ms(start),
            aborted=aborted,
            run_id=self._run_id,
        )
        try:
            log_pipeline(meta)
        except Exception:
            logger.exception("Failed to log pipeline audit")

    # ── flows ──

    def _daily(self, steps: list[StepResult], region: str, with_exchange: bool) -> bool:
        logger.info(f"[Pipeline] Starting {region.upper()} daily pipeline")
        collect = self._collect_daily(region, with_exchange)
        steps.append(collect)
        if not collect.success:
            logger.error("[Pipeline] Collection failed — skipping compute")
            return False
        return self._compute(steps, region)

    def _initial(self, steps: list[StepResult], region: str) -> bool:
        logger.info(f"[Pipeline] Starting {region.upper()} initial pipeline")
        collect = self._safe_step("collection", self._collect_prices, region)
        steps.append(collect)
        if not collect.success:
            logger.error("[Pipeline] Collection failed — skipping compute")
            return False
        steps.append(self._safe_step("fs_collection", self._collect_fs_rows, region))
        return self._compute(steps, region)

    def _collect_fs(self, steps: list[StepResult], region: str) -> bool:
        logger.info(f"[Pipeline] Collecting {region.upper()} financial statements")
        collect = self._safe_step("fs_collection", self._collect_fs_rows, region)
        steps.append(collect)
        if not collect.success:
            logger.info("[Pipeline] skipping fundamentals recompute: fs_collection failed")
            return False
        fundamentals = self._safe_step("fundamentals", self._compute_fundamentals, region)
        steps.append(fundamentals)
        return fundamentals.success

    def _collect_daily(self, region: str, with_exchange: bool) -> StepResult:
        """Run collection and report it as a pipeline step.

        A raised exception or zero collected price rows (surfaced by the
        collectors as an exception) is recorded as a failed `collection` step
        so it is visible in the run record instead of being silently swallowed.
        """
        start = time.monotonic()
        try:
            if with_exchange:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    collect_future = pool.submit(self._collector.collect_all, region)
                    exchange_future = pool.submit(self._collect_exchange_rates)
                    results = collect_future.result()
                    exchange_future.result()
            else:
                results = self._collector.collect_all(region)
        except Exception as e:
            logger.error(f"[Pipeline] {region.upper()} collection failed: {e}", exc_info=True)
            return StepResult("collection", False, _ms(start), str(e))

        collected = sum(results.values())
        logger.info(f"[Pipeline] {region.upper()} collection done: {collected} rows")
        return StepResult("collection", True, _ms(start), output_count=collected)

    # ── compute pipeline ──

    def _compute(self, steps: list[StepResult], region: str) -> bool:
        markets = REGION_CONFIG[region]["markets"]

        deactivate = self._progressive_deactivate(region)
        steps.append(deactivate)
        if not deactivate.success:
            return False

        load_start = time.monotonic()
        price_maps = self._load_prices(markets)
        steps.append(StepResult(
            "load_prices", True, _ms(load_start),
            output_count=sum(len(pm) for pm in price_maps.values()),
        ))

        fund = self._safe_step("fundamentals", self._compute_fundamentals, region, price_maps)
        steps.append(fund)
        if not fund.success:
            steps.append(StepResult("factors", False, 0, "skipped"))
            logger.error("[Pipeline] Fundamentals failed — skipping factors/indicators/risk_badges")
            self._run_integrity_check(region)
            return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            factor_future = pool.submit(
                self._safe_step, "factors", self._compute_factors, region, price_maps)
            sector_agg_future = pool.submit(
                self._safe_step, "sector_agg", self._compute_sector_aggregates, region)
            factor = factor_future.result()
            sector_agg = sector_agg_future.result()
        steps.extend([factor, sector_agg])

        if factor.success:
            self._run_indicators_and_badges(region, markets, price_maps, steps)

        self._run_integrity_check(region)
        return True

    # ── progressive deactivation (single MERGE) ──

    def _progressive_deactivate(self, region: str) -> StepResult:
        start = time.monotonic()
        try:
            changes, summary = StockRepository().compute_deactivation(
                region, self._collector.active_symbols,
            )
            if not self._safety_check(region, summary):
                return StepResult(
                    "progressive_deactivate", False, _ms(start), "safety_check_failed",
                    input_count=summary["total"], output_count=0,
                )
            merged = lake_writer.merge("stocks", changes, self._run_id)
            logger.info(
                f"[Pipeline] Deactivation applied to {region}: "
                f"{summary['deactivated']} deactivated, {summary['reactivated']} reactivated, "
                f"{merged} rows merged"
            )
            return StepResult(
                "progressive_deactivate", True, _ms(start),
                input_count=summary["total"], output_count=merged,
            )
        except Exception as e:
            logger.error(f"[Pipeline] progressive_deactivate failed: {e}", exc_info=True)
            return StepResult("progressive_deactivate", False, _ms(start), str(e))

    @staticmethod
    def _safety_check(region: str, summary: dict[str, int]) -> bool:
        total, active = summary["total"], summary["active_after"]
        ratio = active / total if total > 0 else 0.0
        if ratio < _SAFETY_THRESHOLD:
            logger.error(
                f"[Pipeline] Safety check FAILED for {region}: {active}/{total} would stay "
                f"active ({ratio:.1%}). Skipping the stocks merge and aborting compute."
            )
            return False
        logger.info(f"[Pipeline] Safety check OK: {active}/{total} active ({ratio:.1%})")
        return True

    # ── helpers ──

    def _load_prices(self, markets: list[Market]) -> PriceMaps:
        def _load_market(market: Market) -> tuple[Market, dict[int, list[tuple]]]:
            with get_connection() as conn:
                return market, DailyPriceRepository(conn).get_prices_by_market(market, limit_per_stock=300)

        price_maps: PriceMaps = {}
        with ThreadPoolExecutor(max_workers=len(markets)) as pool:
            for market, data in pool.map(_load_market, markets):
                price_maps[market] = data
        return price_maps

    def _safe_step(self, name: str, fn: Callable[..., Any], *args: Any) -> StepResult:
        start = time.monotonic()
        try:
            result = fn(*args)
        except Exception as e:
            logger.error(f"[Pipeline] Step '{name}' failed: {e}", exc_info=True)
            return StepResult(name, False, _ms(start), str(e))
        return StepResult(
            name, True, _ms(start),
            output_count=result if isinstance(result, int) else None,
        )

    # ── indicators + risk_badges (in-memory handoff) ──

    def _run_indicators_and_badges(
        self, region: str, markets: list[Market],
        price_maps: PriceMaps, steps: list[StepResult],
    ) -> None:
        ind_rows, stock_market_map = None, None
        ind_start = time.monotonic()
        try:
            with get_connection() as conn:
                engine = IndicatorComputeEngine(conn)
                ind_rows, stock_market_map = engine.compute(markets, price_maps)
            steps.append(StepResult(
                "indicators", True, _ms(ind_start),
                input_count=len(stock_market_map), output_count=len(ind_rows),
            ))
        except Exception as e:
            steps.append(StepResult("indicators", False, _ms(ind_start), str(e)))
            logger.error(f"[Pipeline] indicators failed: {e}", exc_info=True)
            return

        ind_dicts = _indicator_rows_to_dicts(ind_rows, price_maps, stock_market_map)

        with ThreadPoolExecutor(max_workers=2) as pool:
            persist_start = time.monotonic()
            persist_future = pool.submit(self._persist_indicators, ind_rows, region)
            badge_step = self._safe_step("risk_badges", self._compute_risk_badges, region, ind_dicts)
            steps.append(badge_step)
            try:
                persisted = persist_future.result()
                steps.append(StepResult(
                    "indicators_persist", True, _ms(persist_start),
                    input_count=len(ind_rows), output_count=persisted,
                ))
            except Exception as e:
                steps.append(StepResult(
                    "indicators_persist", False, _ms(persist_start), str(e),
                ))
                logger.exception("[Pipeline] Failed to persist indicators")

    def _persist_indicators(self, rows: list[tuple], region: str) -> int:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = IndicatorComputeEngine(conn)
            count = engine.persist(rows, markets)
            logger.info(f"[Pipeline] Persisted {count} indicator rows")
            return count

    # ── individual compute steps ──

    def _collect_prices(self, region: str) -> int:
        return sum(self._collector.collect_all(region).values())

    def _collect_fs_rows(self, region: str) -> int | None:
        return self._fund_collector.collect_all(region).get("success")

    def _compute_fundamentals(self, region: str, price_maps: PriceMaps | None = None) -> int:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = FundamentalComputeEngine(conn)
            count = engine.run(markets, price_maps)
            logger.info(f"[Pipeline] Computed {count} fundamental rows")
            return count

    def _compute_factors(self, region: str, price_maps: PriceMaps | None = None) -> int:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = FactorComputeEngine(conn)
            count = engine.run(markets, price_maps)
            logger.info(f"[Pipeline] Computed {count} factor exposure rows")
            return count

    def _compute_sector_aggregates(self, region: str) -> int:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = SectorAggregateComputeEngine(conn)
            count = engine.run(markets)
            logger.info(f"[Pipeline] Computed {count} sector aggregate rows")
            return count

    def _compute_risk_badges(
        self, region: str, ind_dicts: dict[str, dict[int, dict]] | None = None,
    ) -> int:
        markets = REGION_CONFIG[region]["markets"]
        total = 0
        with get_connection() as conn:
            from app.services.risk_badge_service import RiskBadgeService
            from app.db.repositories.risk_badge import RiskBadgeRepository
            service = RiskBadgeService(conn)
            for market in markets:
                indicators = ind_dicts.get(market.value) if ind_dicts else None
                badges = service.compute_batch(market, indicators=indicators)
                total += RiskBadgeRepository(conn).upsert_batch(badges, self._run_id)
        return total

    def _run_integrity_check(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = IntegrityCheckEngine(conn)
            engine.run(markets)

    def _collect_exchange_rates(self) -> None:
        count = self._exchange_rate_collector.collect()
        logger.info(f"[Pipeline] Collected {count} exchange rate rows")
