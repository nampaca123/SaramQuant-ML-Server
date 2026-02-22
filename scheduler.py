import subprocess
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("scheduler")

# ── health check (Railway keeps the service alive via this endpoint) ──


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_a):
        pass


def _start_health_server(port: int = 8080):
    HTTPServer(("", port), _Health).serve_forever()


threading.Thread(target=_start_health_server, daemon=True).start()

# ── pipeline runner ──


def run_pipeline(command: str):
    logger.info(f"Starting: python -m app.pipeline {command}")
    result = subprocess.run(
        ["python", "-m", "app.pipeline", command],
        capture_output=True,
        text=True,
    )
    level = logging.INFO if result.returncode == 0 else logging.ERROR
    logger.log(level, f"[{command}] exit={result.returncode}")
    if result.stdout:
        logger.info(result.stdout[-2000:])
    if result.stderr:
        logger.error(result.stderr[-2000:])


# ── schedule ──

scheduler = BlockingScheduler(timezone="Asia/Seoul")

# Daily
scheduler.add_job(
    run_pipeline,
    CronTrigger(hour=18, minute=0, day_of_week="mon-fri", timezone="Asia/Seoul"),
    args=["kr"],
    id="daily_kr",
    misfire_grace_time=3600,
)
scheduler.add_job(
    run_pipeline,
    CronTrigger(hour=9, minute=0, day_of_week="tue-sat", timezone="Asia/Seoul"),
    args=["us"],
    id="daily_us",
    misfire_grace_time=3600,
)

# Quarterly FS — individual jobs per (month, day) to avoid cartesian product
FS_SCHEDULE = [
    ("4", "7"),   # Q4/Annual  (KR 3/31 deadline, US ~3/1)
    ("5", "22"),  # Q1         (KR 5/15 deadline, US ~5/10)
    ("8", "21"),  # Q2/Semi    (KR 8/14 deadline, US ~8/9)
    ("11", "21"), # Q3         (KR 11/14 deadline, US ~11/9)
]
for month, day in FS_SCHEDULE:
    for cmd in ("kr-fs", "us-fs"):
        scheduler.add_job(
            run_pipeline,
            CronTrigger(month=month, day=day, hour=3, minute=0, timezone="Asia/Seoul"),
            args=[cmd],
            id=f"fs_{cmd}_m{month}",
            misfire_grace_time=86400,
        )

logger.info("Scheduler started — %d jobs registered", len(scheduler.get_jobs()))
scheduler.start()
