import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "4"))
preload_app = False
max_requests = 1000
max_requests_jitter = 50


def on_starting(server):
    from app.scheduler import init_scheduler
    init_scheduler()


def worker_exit(server, worker):
    from app.db.connection import close_pool
    close_pool()
