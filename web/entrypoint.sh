#!/bin/bash
set -e

echo "Applying migrations and seeding teams (waits for any other container doing the same)..."
uv run --no-sync python manage.py prepare_database

echo "Starting application..."
exec "$@"
