# One image for both processes: they share the Django models and must run the same version.
#   web (default): entrypoint.sh applies migrations under a lock, then gunicorn (web/gunicorn.conf.py)
#   bot: entrypoint cleared, run from /app: `/app/.venv/bin/python main.py` (Compose: `uv run --no-sync main.py`)
FROM python:3.14-slim

WORKDIR /app

# Install system dependencies (Pango, Cairo and GDK-Pixbuf are WeasyPrint's, for PDFs)
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY uv.lock* .

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy entrypoint script first
COPY web/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Copy application code
COPY main.py ./main.py
COPY bot/ ./bot/
COPY web/ ./web/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/web
ENV DJANGO_SETTINGS_MODULE=portal.settings
# Writable cache home (WeasyPrint/fontconfig) when running as a non-root user on a read-only root
ENV HOME=/tmp

WORKDIR /app/web

# Bake static files (web/static plus package assets like the admin's, hashed and compressed) into
# the image. Needs no database.
RUN uv run --no-sync python manage.py collectstatic --noinput

ENTRYPOINT ["/entrypoint.sh"]
# Bind address, workers (GUNICORN_WORKERS), timeout and logging live in web/gunicorn.conf.py
CMD ["uv", "run", "--no-sync", "gunicorn", "portal.wsgi:application"]
