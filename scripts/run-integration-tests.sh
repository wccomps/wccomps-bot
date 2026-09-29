#!/usr/bin/env bash
#
# Integration test runner script
# Handles setup, execution, and cleanup of E2E test environment
#
# Usage:
#   ./scripts/run-integration-tests.sh           # Run all integration tests
#   ./scripts/run-integration-tests.sh start     # Start services only
#   ./scripts/run-integration-tests.sh stop      # Stop services only
#   ./scripts/run-integration-tests.sh status    # Check service status
#   ./scripts/run-integration-tests.sh TEST_PATH # Run specific test(s)
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env.test"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.test.yml"
SERVER_PORT=8765
SERVER_PID_FILE="/tmp/wccomps-test-server.pid"
SERVER_LOG_FILE="/tmp/wccomps-test-server.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_env_file() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error ".env.test file not found at $ENV_FILE"
        log_info "Copy .env.test.example and fill in credentials"
        exit 1
    fi
}

cleanup_stale_processes() {
    log_info "Cleaning up stale processes..."

    # Kill any stray runserver processes on our port
    pkill -f "runserver.*$SERVER_PORT" 2>/dev/null || true

    # Also clean up any stray processes on common dev ports
    pkill -f "runserver.*8000" 2>/dev/null || true

    # Remove stale PID file
    if [[ -f "$SERVER_PID_FILE" ]]; then
        local pid
        pid=$(cat "$SERVER_PID_FILE" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$SERVER_PID_FILE"
        fi
    fi

    # Give processes time to terminate
    sleep 1
}

start_database() {
    log_info "Starting test database..."
    if ! docker compose -f "$COMPOSE_FILE" ps --status running 2>/dev/null | grep -q postgres-test; then
        docker compose -f "$COMPOSE_FILE" up -d --wait
        log_info "Database started on port 5433"
    else
        log_info "Database already running"
    fi
}

stop_database() {
    log_info "Stopping test database..."
    docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
}

wait_for_database() {
    log_info "Waiting for database to be ready..."
    local max_attempts=30
    local attempt=1

    while ! docker exec wccomps-test-db pg_isready -U test_user -d wccomps_test >/dev/null 2>&1; do
        if [[ $attempt -ge $max_attempts ]]; then
            log_error "Database failed to become ready after $max_attempts attempts"
            exit 1
        fi
        sleep 1
        ((attempt++))
    done
    log_info "Database is ready"
}

load_env_safely() {
    # Export the settings the server, migrations and tests need from .env.test. dotenv parses
    # the file and shlex.quote makes any value (special characters included) safe to eval.
    local env_file="${1:-$ENV_FILE}"
    eval "$(uv run python -c "
import shlex
from dotenv import dotenv_values

config = dotenv_values('$env_file')
keys = ['TEST_DB_HOST', 'TEST_DB_PORT', 'TEST_DB_NAME', 'TEST_DB_USER', 'TEST_DB_PASSWORD',
        'TEST_AUTHENTIK_USERNAME', 'TEST_AUTHENTIK_PASSWORD', 'TEST_BASE_URL',
        'AUTHENTIK_URL', 'AUTHENTIK_OIDC_URL', 'AUTHENTIK_CLIENT_ID', 'AUTHENTIK_SECRET',
        'TICKETING_ENABLED', 'TEST_TEAM_ID', 'TEST_TOTP_SECRET',
        'QUOTIENT_API_URL', 'QUOTIENT_USERNAME', 'QUOTIENT_PASSWORD']
for key in keys:
    if config.get(key):
        print(f'export {key}={shlex.quote(config[key])}')
# The server and migrations use the same database as the tests (web/integration_tests/conftest.py)
for name, default in [('HOST', 'localhost'), ('PORT', '5433'), ('NAME', 'wccomps_test'),
                      ('USER', 'test_user'), ('PASSWORD', 'test_password')]:
    print(f'export DB_{name}={shlex.quote(config.get(f\"TEST_DB_{name}\") or default)}')
")"
}

run_migrations() {
    log_info "Running database migrations..."
    (
        cd "$PROJECT_ROOT"
        load_env_safely "$ENV_FILE"
        export DJANGO_SETTINGS_MODULE=portal.test_settings
        export PYTHONPATH="$PROJECT_ROOT/web:$PROJECT_ROOT"
        uv run python web/manage.py migrate --run-syncdb 2>&1 | head -20
    )
    log_info "Migrations complete"
}

start_server() {
    if [[ -f "$SERVER_PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$SERVER_PID_FILE")
        if kill -0 "$old_pid" 2>/dev/null; then
            log_info "Server already running (PID: $old_pid)"
            return 0
        fi
        rm -f "$SERVER_PID_FILE"
    fi

    log_info "Starting Django development server on port $SERVER_PORT..."
    (
        cd "$PROJECT_ROOT"
        load_env_safely "$ENV_FILE"
        export DJANGO_SETTINGS_MODULE=portal.test_settings
        export PYTHONPATH="$PROJECT_ROOT/web:$PROJECT_ROOT"
        uv run python web/manage.py runserver "127.0.0.1:$SERVER_PORT" > "$SERVER_LOG_FILE" 2>&1 &
        echo $! > "$SERVER_PID_FILE"
    )

    # Wait for server to start
    local max_attempts=30
    local attempt=1
    while ! curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$SERVER_PORT/" 2>/dev/null | grep -qE "^(200|302)$"; do
        if [[ $attempt -ge $max_attempts ]]; then
            log_error "Server failed to start after $max_attempts seconds"
            log_error "Check log: $SERVER_LOG_FILE"
            cat "$SERVER_LOG_FILE"
            exit 1
        fi
        sleep 1
        ((attempt++))
    done

    log_info "Server started on http://127.0.0.1:$SERVER_PORT (PID: $(cat "$SERVER_PID_FILE"))"
}

stop_server() {
    if [[ -f "$SERVER_PID_FILE" ]]; then
        local pid
        pid=$(cat "$SERVER_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Stopping server (PID: $pid)..."
            kill "$pid" 2>/dev/null || true
            sleep 1
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$SERVER_PID_FILE"
    fi

    # Also kill any stray runserver processes on our port
    pkill -f "runserver.*$SERVER_PORT" 2>/dev/null || true

    log_info "Server stopped"
}

check_status() {
    echo "=== Integration Test Environment Status ==="
    echo ""

    # Check env file
    if [[ -f "$ENV_FILE" ]]; then
        echo -e "Environment file: ${GREEN}Found${NC} ($ENV_FILE)"
    else
        echo -e "Environment file: ${RED}Missing${NC}"
    fi

    # Check database
    if docker compose -f "$COMPOSE_FILE" ps --status running 2>/dev/null | grep -q postgres-test; then
        echo -e "Test database:    ${GREEN}Running${NC} (port 5433)"
    else
        echo -e "Test database:    ${RED}Stopped${NC}"
    fi

    # Check server
    if [[ -f "$SERVER_PID_FILE" ]]; then
        local pid
        pid=$(cat "$SERVER_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "Django server:    ${GREEN}Running${NC} (PID: $pid, port $SERVER_PORT)"
        else
            echo -e "Django server:    ${RED}Stopped${NC} (stale PID file)"
        fi
    else
        echo -e "Django server:    ${RED}Stopped${NC}"
    fi

    echo ""
}

run_tests() {
    local test_args=("$@")

    log_info "Running integration tests..."
    (
        cd "$PROJECT_ROOT"
        load_env_safely "$ENV_FILE"
        export DJANGO_SETTINGS_MODULE=portal.test_settings
        export PYTHONPATH="$PROJECT_ROOT/web:$PROJECT_ROOT"
        export TEST_BASE_URL="http://127.0.0.1:$SERVER_PORT"

        if [[ ${#test_args[@]} -eq 0 ]]; then
            # Run all integration tests
            # --reuse-db: Don't create/destroy test database (use external DB)
            # --no-migrations: Don't run migrations (already done above)
            uv run pytest web/integration_tests/ -v --tb=short --reuse-db --no-migrations
        else
            # Run specific tests
            uv run pytest "${test_args[@]}" -v --tb=short --reuse-db --no-migrations
        fi
    )
}

cleanup() {
    log_info "Cleaning up..."
    stop_server
    # Note: We don't stop the database by default to speed up repeated test runs
}

# Main entry point
main() {
    cd "$PROJECT_ROOT"

    case "${1:-run}" in
        start)
            cleanup_stale_processes
            check_env_file
            start_database
            wait_for_database
            run_migrations
            start_server
            ;;
        stop)
            stop_server
            stop_database
            ;;
        status)
            check_status
            ;;
        restart)
            cleanup_stale_processes
            stop_server
            check_env_file
            start_database
            wait_for_database
            start_server
            ;;
        run|"")
            cleanup_stale_processes
            check_env_file
            start_database
            wait_for_database
            run_migrations
            start_server
            trap cleanup EXIT
            run_tests
            ;;
        *)
            # Assume it's a test path
            cleanup_stale_processes
            check_env_file
            start_database
            wait_for_database
            run_migrations
            start_server
            trap cleanup EXIT
            run_tests "$@"
            ;;
    esac
}

main "$@"
