"""Gunicorn settings, loaded automatically from the working directory (/app/web)."""

# Default format minus the query string and referer: /auth/link?token=... and
# /auth/callback/?code=... would otherwise put link tokens and OAuth codes in journald.
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(a)s"'
