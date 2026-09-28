"""Gunicorn settings, loaded automatically from the working directory (/app/web)."""

import os

bind = "0.0.0.0:8000"
# Default 4; stay at or under 10 to keep within the database's connection limit
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
timeout = 90
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Default format minus the query string and referer: /auth/link?token=... and
# /auth/callback/?code=... would otherwise put link tokens and OAuth codes in journald.
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(a)s"'
