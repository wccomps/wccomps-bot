# CLAUDE.md

## Project Overview

WCComps Portal is a competition management platform for WRCCDC. Three components:
- **Discord Bot** (`main.py` + `bot/`) — Team ticketing, role sync, competition commands
- **Django Web** (`web/`) — Scoring portal, inject grading, packet distribution, ops dashboard
- **Authentik Integration** — SSO, team provisioning, permission sync via `core/authentik_manager.py`

## Quick Commands

```bash
# Run tests (requires test DB: docker compose -f docker-compose.test.yml up -d --wait)
cd web && DB_HOST=localhost DB_PORT=5433 DB_USER=test_user DB_PASSWORD=test_password DB_NAME=wccomps_test uv run pytest

# Lint and type checks (CI runs these plus the tests on every PR)
uv run ruff check . && uv run djlint web/templates --lint && DJANGO_SETTINGS_MODULE=portal.settings uv run mypy
```

## Production

Runs on the deoxys Kubernetes cluster, deployed by Argo CD from `wccomps/wccomps-argocd`
(`manifests/wccomps-portal/`; its README covers operations).
- CI builds one image for web and bot on every merge to main: `ghcr.io/wccomps/wccomps-portal:sha-<short>`.
- Deploy: PR to wccomps-argocd bumping `newTag` in `manifests/wccomps-portal/kustomization.yaml`.
- After changing its `configmap.yaml` or `secrets.yaml`, restart the web and bot Deployments.
- `docker-compose.yml` is for anyone self-hosting the public repo (Postgres + web + bot); it isn't
  how production runs.

## Python 3.14

This project targets Python 3.14+ (`requires-python = ">=3.14"`). PEP 758 re-enables
`except A, B:` syntax without parentheses. Do NOT change these to `except (A, B):` —
the unparenthesized form is used intentionally throughout.

## Key Conventions

### Permissions
- All authorization uses Authentik groups via `core.permission_constants.PERMISSION_MAP`
- Use `@require_permission("role_name")` decorator for page views
- Use manual `has_permission()` check for JSON API endpoints (return `JsonResponse` 403)
- `WCComps_Discord_Admin` grants access to everything

### Discord Task Queue
- Views create `DiscordTask` records; the bot's `DiscordQueueProcessor` consumes them
- Valid task types and payload schemas documented in `core/models.py` DiscordTask docstring
- Always set `status="pending"` when creating tasks

### UI Components
- All templates extend `admin/base_site.html` directly
- Use django-cotton components from `templates/cotton/`

### Streaming Progress Pattern
- Use `StreamingHttpResponse` + NDJSON + Alpine.js for long operations
- Use `core.utils.ndjson_progress()` helper for progress lines

### Team Model
- `MAX_TEAMS = 50` defined in `team/models.py`
- Team accounts are shared (multiple Discord users per Authentik account)
