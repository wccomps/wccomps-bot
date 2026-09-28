#!/bin/bash
set -e

echo "Applying migrations and seeding teams (waits for any other container doing the same)..."
uv run --no-sync python manage.py prepare_database

# The image already contains collected static files. Compose mounts the code over the image and keeps
# static files in a writable volume, so collect there; skip where the filesystem is read-only.
if [ -w /app/web/staticfiles ]; then
    echo "Collecting static files..."
    uv run --no-sync python manage.py collectstatic --noinput
fi

echo "Starting application..."
exec "$@"
