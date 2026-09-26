# WCComps Portal

Competition management platform for WRCCDC: Discord bot, Django web app, and Authentik SSO integration.

## Components

- **Discord Bot** - Team ticketing, role sync, competition commands
- **Django Web** - Scoring portal, inject grading, packet distribution, ops dashboard
- **Authentik Integration** - SSO, team provisioning, permission sync

## Setup

```bash
cp .env.example .env  # Configure credentials
docker-compose up -d  # Migrations run automatically
```

## Development

```bash
uv sync
uv run pytest
./deploy.sh  # Tests, builds, deploys
```

## Troubleshooting

- **Bot not responding:** `docker-compose logs bot`
- **OAuth errors:** `BASE_URL` must match Authentik redirect URI
- **Permissions stale:** User must re-login after Authentik group changes
