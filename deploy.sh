#!/bin/bash
set -e

REMOTE_HOST="root@10.0.0.10"
REMOTE_PATH="/opt/stacks/wccomps-portal/"
SECONDS=0

fail() {
    echo ""
    echo "  ✗ $1"
    [ -n "${2:-}" ] && echo "" && echo "$2"
    echo ""
    exit 1
}
step() { echo "  ✓ $*"; }
cleanup_test_db() { docker compose -f docker-compose.test.yml down -v || true; }
remote() { ssh "$REMOTE_HOST" "cd $REMOTE_PATH && $1"; }
test_db_healthy() {
    docker compose -f docker-compose.test.yml ps --format json 2>/dev/null \
        | grep -q '"healthy"' 2>/dev/null
}

# Suppress stderr noise (progress bars, docker lifecycle) — errors go via stdout
exec 3>&2 2>/dev/null

echo "deploy ── $(git branch --show-current) @ $(git rev-parse --short HEAD)"
echo ""

# Require clean working tree so deployed code matches a commit
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    fail "uncommitted changes — commit before deploying"
fi

echo "Checks"

uv lock --upgrade --quiet
uv sync --quiet
step "deps"

# Auto-fix formatting (ruff=Python, djlint=HTML — no overlap, safe to parallel)
uv run djlint web/templates --reformat --quiet &
PID_DJFMT=$!
uv run ruff format . --quiet
uv run ruff check --fix --quiet . || true
# djlint --reformat exits 1 when it changed files; that's expected here (auto-committed below)
wait $PID_DJFMT || true

# Auto-commit any formatting changes so deployed code matches a commit
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git add -u && git commit -m "autoformat" --quiet
fi

# Start test DB in background during lint phase (overlaps ~7s of DB startup with ~10s of lint)
PID_TESTDB=0
if command -v docker &>/dev/null; then
    {
        if ! test_db_healthy; then
            docker compose -f docker-compose.test.yml up -d --wait || exit 1
        fi
    } &
    PID_TESTDB=$!
fi

# Run all lint checks in parallel
RUFF_RC=0 DJLINT_RC=0 MYPY_RC=0

uv run ruff check . --quiet > /tmp/deploy_ruff 2>&1 || RUFF_RC=$?
{ uv run djlint web/templates --lint --quiet > /tmp/deploy_djlint 2>&1; } &
PID_DJLINT=$!
{ DJANGO_SETTINGS_MODULE=portal.settings uv run mypy > /tmp/deploy_mypy 2>&1; } &
PID_MYPY=$!

# wait returns the subshell's exit status (|| prevents set -e from killing the script)
wait $PID_DJLINT || DJLINT_RC=$?
wait $PID_MYPY || MYPY_RC=$?

[ $RUFF_RC -ne 0 ] && fail "ruff" "$(cat /tmp/deploy_ruff)"
step "ruff"
[ $DJLINT_RC -ne 0 ] && { exec 2>&3; fail "djlint" "$(uv run djlint web/templates --lint)"; }
step "djlint"
[ $MYPY_RC -ne 0 ] && fail "mypy" "$(cat /tmp/deploy_mypy)"
step "mypy"

if [ "$PID_TESTDB" -ne 0 ]; then
    wait $PID_TESTDB || fail "test db"

    if [ -f .env.test ]; then
        set -a; source .env.test; set +a
    fi

    export DB_HOST="${TEST_DB_HOST:-localhost}"
    export DB_PORT="${TEST_DB_PORT:-5433}"
    export DB_NAME="${TEST_DB_NAME:-wccomps_test}"
    export DB_USER="${TEST_DB_USER:-test_user}"
    export DB_PASSWORD="${TEST_DB_PASSWORD:-test_password}"

    # Run migrate, makemigrations check, and collectstatic in parallel
    # (makemigrations --check doesn't need DB; collectstatic doesn't need DB)
    MIGRATE_RC=0 MAKEMIG_RC=0 STATIC_RC=0

    { cd web && DJANGO_SETTINGS_MODULE=portal.settings \
        uv run python manage.py migrate --noinput --verbosity 0 \
        > /tmp/deploy_migrate 2>&1; } &
    PID_MIGRATE=$!

    { cd web && DJANGO_SETTINGS_MODULE=portal.settings \
        uv run python manage.py makemigrations --check --dry-run --verbosity 0 \
        > /tmp/deploy_makemig 2>&1; } &
    PID_MAKEMIG=$!

    { cd web && DJANGO_SETTINGS_MODULE=portal.settings \
        uv run python manage.py collectstatic --noinput --verbosity 0 \
        > /tmp/deploy_static 2>&1; } &
    PID_STATIC=$!

    wait $PID_MIGRATE || MIGRATE_RC=$?
    wait $PID_MAKEMIG || MAKEMIG_RC=$?
    wait $PID_STATIC || STATIC_RC=$?

    [ $MIGRATE_RC -ne 0 ] && { cleanup_test_db; fail "migrate" "$(cat /tmp/deploy_migrate)"; }
    [ $MAKEMIG_RC -ne 0 ] && { cleanup_test_db; fail "migrate — unapplied model changes detected"; }
    [ $STATIC_RC -ne 0 ] && { cleanup_test_db; fail "collectstatic" "$(cat /tmp/deploy_static)"; }
    step "migrate"

    # Check for playwright browser availability before starting tests
    HAS_BROWSER=false
    if uv run python -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
try:
    b = p.chromium.launch(headless=True)
except Exception:
    b = p.firefox.launch(headless=True)
b.close()
p.stop()
" 2>/dev/null; then
        HAS_BROWSER=true
    fi

    # Run unit and browser tests in parallel
    TEST_RC=0 BROWSER_RC=0
    { PYTHONPATH="$(pwd)/web:$(pwd)" uv run pytest -q -m "not browser" > /tmp/deploy_tests 2>&1; } &
    PID_TESTS=$!

    if $HAS_BROWSER; then
        { PYTHONPATH="$(pwd)/web:$(pwd)" uv run pytest -q -m browser -n0 > /tmp/deploy_browser 2>&1; } &
        PID_BROWSER=$!
    fi

    wait $PID_TESTS || TEST_RC=$?
    [ $TEST_RC -ne 0 ] && { cleanup_test_db; fail "tests" "$(cat /tmp/deploy_tests)"; }
    SUMMARY=$(grep . /tmp/deploy_tests | tail -1)
    step "tests      $SUMMARY"

    if $HAS_BROWSER; then
        wait $PID_BROWSER || BROWSER_RC=$?
        [ $BROWSER_RC -ne 0 ] && { cleanup_test_db; fail "browser tests" "$(cat /tmp/deploy_browser)"; }
        BSUMMARY=$(grep . /tmp/deploy_browser | tail -1)
        step "browser    $BSUMMARY"
    else
        echo "  · browser    skipped (no playwright browser)"
    fi

    # Leave test DB running — next deploy reuses it via test_db_healthy() check.
    # To reclaim resources: docker compose -f docker-compose.test.yml down -v
else
    echo "  · tests      skipped (no docker)"
fi

# --- Deploy ---

echo ""
echo "Deploy"

# Dry-run rsync to see what files changed — determines deploy strategy.
# --checksum + dropping '.'-prefixed (attribute-only) lines: `uv lock` rewrites uv.lock every deploy,
# and a new mtime alone must not force a full rebuild.
CHANGES=$({ rsync -az --checksum --delete --exclude-from=.rsyncignore -e ssh . "$REMOTE_HOST:$REMOTE_PATH" \
    --dry-run --itemize-changes || echo "first-deploy"; } | grep -v '^\.' || true)

# Actual sync
rsync -az --delete --exclude-from=.rsyncignore -e ssh . "$REMOTE_HOST:$REMOTE_PATH" --quiet \
    || fail "rsync"
step "synced"

# Classify what changed
INFRA_CHANGED=false
BOT_CHANGED=false
STATIC_CHANGED=false
echo "$CHANGES" | grep -qE 'pyproject\.toml|uv\.lock|Dockerfile|docker-compose\.yml|entrypoint\.sh' && INFRA_CHANGED=true
echo "$CHANGES" | grep -q 'bot/' && BOT_CHANGED=true
echo "$CHANGES" | grep -q 'web/static/' && STATIC_CHANGED=true

# Only take the fast path if web is running AND healthy
# grep -c prints 0 AND exits 1 when web isn't running; `|| echo 0` would make this "0\n0"
WEB_HEALTHY=$(remote "docker compose ps web --format json | grep -c '\"healthy\"'" || true)
WEB_HEALTHY=${WEB_HEALTHY:-0}

if $INFRA_CHANGED || [ "$WEB_HEALTHY" = "0" ]; then
    # Full rebuild: infra changed, container not running, or container unhealthy
    REASON="infra changed"
    $INFRA_CHANGED || REASON="web not healthy"

    remote "docker compose build --quiet web bot" \
        || fail "build"
    step "built"

    # Pre-run migrations on new image (skip entrypoint) so container startup is fast
    remote "docker compose run --rm --no-deps --entrypoint '' web uv run --no-sync python manage.py migrate --noinput --verbosity 0" \
        || fail "migrate"

    remote "docker compose up -d --quiet-pull web bot" \
        || fail "start"

    for i in $(seq 1 30); do
        if remote "docker compose ps web --format json | grep -q '\"Health\":\"healthy\"'"; then
            step "healthy"
            echo ""
            echo "Done in ${SECONDS}s (rebuild — $REASON)"
            exit 0
        fi
        sleep 2
    done

    echo ""
    remote "docker compose logs --tail=30 web"
    fail "health check timed out after 60s"
else
    # Code-only — zero downtime via gunicorn graceful reload.
    # Bind mount (./web:/app/web) means rsync already updated the running code.
    # Migrate + collectstatic in the running container, then SIGHUP gunicorn.
    # If new code fails to import, old workers keep serving and Docker health checks
    # (10s interval) will catch it — Traefik stops routing to unhealthy containers.

    remote "docker compose exec -T web uv run --no-sync python manage.py migrate --noinput --verbosity 0" \
        || fail "migrate"
    remote "docker compose exec -T web uv run --no-sync python manage.py collectstatic --clear --noinput --verbosity 0" \
        || fail "collectstatic"
    step "migrate"

    # Clear bytecode cache and restart so new workers load fresh source
    remote "docker compose exec -T web find /app/web -type d -name __pycache__ -exec rm -rf {} + || true"
    remote "docker compose restart web" \
        || fail "restart"
    step "restarted"

    # Only restart bot if bot code actually changed
    if $BOT_CHANGED; then
        remote "docker compose up -d --force-recreate bot" \
            || echo "  · bot restart failed"
        step "bot"
    fi

    echo ""
    echo "Done in ${SECONDS}s (reload)"
fi
