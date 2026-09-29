"""Gunicorn settings, loaded automatically from the working directory (/app/web)."""

import os

bind = "0.0.0.0:8000"
# Default 4. Each keeps one database connection open (CONN_MAX_AGE); mind Postgres max_connections
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
# Sync workers are killed after this long on one request. Streamed operations (competition
# start/stop: ~60 Authentik calls; packet and scorecard emailing) must finish inside it.
timeout = 300
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Default format minus the query string and referer: /auth/link?token=... and
# /auth/callback/?code=... would otherwise put link tokens and OAuth codes in the logs.
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(a)s"'
