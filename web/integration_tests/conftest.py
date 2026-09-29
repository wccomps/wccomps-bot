"""
Integration test configuration and fixtures.

These tests use real PostgreSQL, real Authentik API, and real Discord API
to catch integration issues before deployment.
"""

import os
from pathlib import Path

import django
import pytest
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

# IMPORTANT: Set environment variables BEFORE calling django.setup()
# Load test environment variables first
# Use override=True to handle special chars that bash sourcing mangles (like QUOTIENT_PASSWORD)
env_test_path = Path(__file__).parent.parent.parent / ".env.test"
if env_test_path.exists():
    load_dotenv(env_test_path, override=True)

# Allow sync database operations in async context detection.
# Required for pytest-asyncio + pytest-django + Playwright live_server compatibility.
# See: https://github.com/microsoft/playwright-pytest/issues/29
# This is safe in tests since we're not running concurrent database operations.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Set Django settings module before django.setup()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.test_settings")
os.environ["DB_HOST"] = os.getenv("TEST_DB_HOST", "localhost")
os.environ["DB_PORT"] = os.getenv("TEST_DB_PORT", "5433")
os.environ["DB_NAME"] = os.getenv("TEST_DB_NAME", "wccomps_test")
os.environ["DB_USER"] = os.getenv("TEST_DB_USER", "test_user")
os.environ["DB_PASSWORD"] = os.getenv("TEST_DB_PASSWORD", "test_password")

# Check for .env.test
if not env_test_path.exists():
    pytest.skip(
        "No .env.test file found. Copy .env.test.example and fill in credentials.",
        allow_module_level=True,
    )

# Configure Django
django.setup()


# ============================================================================
# Database Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def django_db_setup():
    """
    Use existing database without creating a test database.

    For E2E tests against a running server, we need to use the same database
    as the server. This fixture disables pytest-django's test database creation.
    See: https://pytest-django.readthedocs.io/en/latest/database.html
    """
    from django.conf import settings

    # Verify we're pointing to the right database
    db_settings = settings.DATABASES["default"]
    db_name = db_settings.get("NAME")
    db_host = db_settings.get("HOST")
    db_port = db_settings.get("PORT")
    print(f"\n[Integration Tests] Using database: {db_name} on {db_host}:{db_port}")


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """Skip test database creation - use the configured database directly."""


@pytest.fixture(scope="session", autouse=True)
def _enable_db_access_for_all_tests(django_db_blocker):
    """Enable database access for all integration tests at session scope."""
    django_db_blocker.unblock()
    yield
    django_db_blocker.restore()


@pytest.fixture
def cleanup_test_data():
    """
    Manual cleanup fixture for tests that use transactional_db.

    Tests using db fixture get automatic rollback, but concurrent tests
    that need transactional_db must manually clean up.

    Usage:
        def test_something(transactional_db, cleanup_test_data):
            # Test code that commits to database
            pass
            # Cleanup runs after test
    """
    # Run test
    yield

    # Clean up test data after test
    from django.contrib.auth import get_user_model

    from ticketing.models import Ticket

    user_model = get_user_model()

    try:
        # Delete in correct order (respecting foreign keys)
        # 1. Delete tickets first (they reference teams/discordlinks)
        Ticket.objects.filter(title__startswith="[INTEGRATION TEST]").delete()

        # 2. Get test users and delete them
        test_users = user_model.objects.filter(username__startswith="test_")
        test_users.delete()

    except Exception as e:
        # Log cleanup errors but don't fail tests
        import sys

        print(f"Warning: Cleanup error: {e}", file=sys.stderr)


# ============================================================================
# Authentication Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def authentik_credentials():
    """Get Authentik test user credentials from environment."""
    return {
        "username": os.getenv("TEST_AUTHENTIK_USERNAME"),
        "password": os.getenv("TEST_AUTHENTIK_PASSWORD"),
        "api_token": os.getenv("TEST_AUTHENTIK_API_TOKEN"),
    }


@pytest.fixture(scope="session")
def discord_credentials():
    """Get Discord test user credentials from environment."""
    return {
        "user_token": os.getenv("TEST_DISCORD_USER_TOKEN"),
        "username": os.getenv("TEST_DISCORD_USERNAME"),
    }


@pytest.fixture
def test_team_id(db):
    """Get test team ID (team 50) and ensure it exists."""
    from team.models import Team

    team_number = int(os.getenv("TEST_TEAM_ID", "50"))

    # Create team 50 if it doesn't exist
    _team, _created = Team.objects.get_or_create(
        team_number=team_number,
        defaults={
            "team_name": "Test Team 50",
            "authentik_group": "WCComps_BlueTeam50",
            "is_active": True,
        },
    )

    return team_number


@pytest.fixture
def authentik_client(authentik_credentials):
    """
    Create Authentik API client for real API calls.
    Uses real Authentik instance, not mocked.
    """
    try:
        from authentik_client import ApiClient, Configuration

        api_token = authentik_credentials["api_token"]
        authentik_url = os.getenv("AUTHENTIK_URL", "https://auth.wccomps.org")

        config = Configuration(host=f"{authentik_url}/api/v3")
        config.api_key["Authorization"] = api_token
        config.api_key_prefix["Authorization"] = "Bearer"

        return ApiClient(config)
    except ImportError:
        pytest.skip("authentik_client package not available or incompatible")


# ============================================================================
# Browser (Playwright) Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def live_server_url():
    """
    Get base URL for browser tests.

    For OAuth tests, use TEST_BASE_URL (a running server with OAuth callback registered).
    This is required because Django's live_server uses dynamic ports that can't be
    pre-registered with Authentik.
    """
    base_url = os.getenv("TEST_BASE_URL", "http://localhost:8000")
    return base_url.rstrip("/")


@pytest.fixture(scope="session")
def browser_context_args():
    """Configure browser context (headless, viewport, etc.)."""
    return {
        "viewport": {"width": 1920, "height": 1080},
        "ignore_https_errors": True,  # Allow self-signed certs in test env
    }


@pytest.fixture(scope="session")
def playwright_instance():
    """Create Playwright instance for the test session."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance):
    """Launch browser instance (headless Chromium)."""
    browser = playwright_instance.chromium.launch(headless=True)
    yield browser
    browser.close()


@pytest.fixture
def browser_context(browser, browser_context_args):
    """Create a new browser context for each test."""
    context = browser.new_context(**browser_context_args)
    yield context
    context.close()


@pytest.fixture
def page(browser_context) -> Page:
    """Create a new page for each test."""
    page = browser_context.new_page()
    yield page
    page.close()


def _perform_authentik_login(page, username: str, password: str, live_server_url: str):
    """Helper to perform Authentik OAuth login.

    Authentik uses a multi-step login flow:
    1. Username entry (uidField)
    2. Password entry (may be separate step or combined)
    3. Optional MFA/TOTP
    4. Optional consent screen
    """
    page.goto(f"{live_server_url}/auth/login/")

    # Wait for redirect to Authentik
    page.wait_for_timeout(2000)

    # Check if we already landed back at the app (SSO session reuse)
    if page.url.startswith(live_server_url):
        return page

    # Handle Authentik login flow
    try:
        # Step 1: Username entry
        uid_field = page.locator('input[name="uidField"]')
        if uid_field.is_visible(timeout=5000):
            page.fill('input[name="uidField"]', username)

            # Check if password field is visible (combined form)
            password_field = page.locator('input[name="password"]')
            if password_field.is_visible(timeout=500):
                page.fill('input[name="password"]', password)

            page.click('button[type="submit"]')
            page.wait_for_timeout(2000)

            # Check if we redirected back (fast login)
            if page.url.startswith(live_server_url):
                return page

            # Step 2: Password entry (if separate step)
            password_field = page.locator('input[name="password"]')
            if password_field.is_visible(timeout=3000):
                page.fill('input[name="password"]', password)
                page.click('button[type="submit"]')
                page.wait_for_timeout(2000)

        # Check for consent/continue button
        continue_btn = page.locator('button:has-text("Continue")')
        if continue_btn.is_visible(timeout=1000):
            continue_btn.click()
            page.wait_for_timeout(1000)

    except Exception:
        # Fallback: wait for username and try again
        page.wait_for_selector('input[name="uidField"]', timeout=15000)
        page.fill('input[name="uidField"]', username)
        password_field = page.locator('input[name="password"]')
        if password_field.is_visible(timeout=1000):
            page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_timeout(2000)

        # Try password in separate step
        password_field = page.locator('input[name="password"]')
        if password_field.is_visible(timeout=3000):
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')

    # Check if we already redirected back
    if page.url.startswith(live_server_url):
        return page

    # Handle MFA if present
    totp_secret = os.getenv("TEST_TOTP_SECRET")
    if totp_secret:
        try:
            import pyotp

            # Check for MFA selection screen
            if "Select an authentication method" in page.content():
                page.click("text=TOTP Device")
                page.wait_for_timeout(1000)

            totp = pyotp.TOTP(totp_secret)
            code_input = page.locator('input[name="code"]')
            if code_input.is_visible(timeout=3000):
                page.fill('input[name="code"]', totp.now())
                page.click('button[type="submit"]')
                page.wait_for_timeout(2000)
        except ImportError, TimeoutError:
            pass

    # Check if we already redirected back
    if page.url.startswith(live_server_url):
        return page

    # Final consent screen check
    continue_btn = page.locator('button:has-text("Continue")')
    if continue_btn.is_visible(timeout=1000):
        continue_btn.click()

    # Wait for redirect back to application
    page.wait_for_url(f"{live_server_url}/**", timeout=15000)
    return page


@pytest.fixture(scope="session")
def authenticated_page(browser, browser_context_args, authentik_credentials, live_server_url) -> Page:
    """
    Create an authenticated page by logging in via Authentik OAuth.
    This performs a real OAuth login flow.

    Session-scoped to avoid repeated OAuth logins which trigger Authentik's
    reputation system when too many concurrent sessions are created.
    """
    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    _perform_authentik_login(
        page, authentik_credentials["username"], authentik_credentials["password"], live_server_url
    )
    yield page
    page.close()
    context.close()


# ============================================================================
# Multi-User Browser Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def ops_user_credentials():
    """Ops/support user credentials from environment."""
    return {
        "username": os.getenv("TEST_OPS_USERNAME", os.getenv("TEST_AUTHENTIK_USERNAME")),
        "password": os.getenv("TEST_OPS_PASSWORD", os.getenv("TEST_AUTHENTIK_PASSWORD")),
    }


@pytest.fixture(scope="session")
def team_user_credentials():
    """Team member credentials from environment."""
    return {
        "username": os.getenv("TEST_TEAM_USERNAME", os.getenv("TEST_AUTHENTIK_USERNAME")),
        "password": os.getenv("TEST_TEAM_PASSWORD", os.getenv("TEST_AUTHENTIK_PASSWORD")),
    }


@pytest.fixture(scope="session")
def admin_user_credentials():
    """Admin user credentials from environment."""
    return {
        "username": os.getenv("TEST_ADMIN_USERNAME", os.getenv("TEST_AUTHENTIK_USERNAME")),
        "password": os.getenv("TEST_ADMIN_PASSWORD", os.getenv("TEST_AUTHENTIK_PASSWORD")),
    }


@pytest.fixture(scope="session")
def ops_page(browser, browser_context_args, ops_user_credentials, live_server_url) -> Page:
    """Browser page authenticated as ops/support user.

    Scoped to session to avoid repeated OAuth logins.
    """
    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    _perform_authentik_login(page, ops_user_credentials["username"], ops_user_credentials["password"], live_server_url)
    yield page
    page.close()
    context.close()


def _ensure_team_membership(username: str, team_number: int) -> None:
    """Ensure user has team membership in the shared database."""
    from django.contrib.auth import get_user_model

    from core.models import UserGroups
    from team.models import DiscordLink, Team

    user_model = get_user_model()

    user = user_model.objects.filter(username=username).first()
    if not user:
        return

    team_group = f"WCComps_BlueTeam{team_number}"

    team, _ = Team.objects.get_or_create(
        team_number=team_number,
        defaults={
            "team_name": f"Test Team {team_number}",
            "authentik_group": team_group,
            "is_active": True,
        },
    )

    # Add team group to UserGroups (required for web access)
    try:
        user_groups = user.usergroups
        if team_group not in user_groups.groups:
            user_groups.groups = [team_group] + user_groups.groups
            user_groups.save()
    except UserGroups.DoesNotExist:
        UserGroups.objects.create(
            user=user,
            groups=[team_group],
            authentik_id="test-integration",
        )

    # Create or update DiscordLink (required for ticket assignment)
    link, created = DiscordLink.objects.get_or_create(
        user=user,
        defaults={
            "discord_id": 123456789012345678,  # Fake Discord ID for testing
            "team": team,
            "is_active": True,
        },
    )
    if not created and link.team != team:
        link.team = team
        link.is_active = True
        link.save()


@pytest.fixture(scope="session")
def team_page(browser, browser_context_args, team_user_credentials, live_server_url) -> Page:
    """Browser page authenticated as team member with team membership.

    Scoped to session to avoid repeated OAuth logins.
    """
    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    _perform_authentik_login(
        page, team_user_credentials["username"], team_user_credentials["password"], live_server_url
    )

    # After OAuth login, ensure user has team membership
    # DB access is always enabled for integration tests via _enable_db_access_for_all_tests
    team_number = int(os.getenv("TEST_TEAM_ID", "50"))
    _ensure_team_membership(team_user_credentials["username"], team_number)

    # Refresh the page to pick up the new permissions
    page.reload()

    yield page
    page.close()
    context.close()


@pytest.fixture(scope="session")
def admin_page(
    browser, browser_context_args, admin_user_credentials, ops_user_credentials, ops_page, live_server_url
) -> Page:
    """Browser page authenticated as admin user.

    If admin credentials match ops credentials, reuses ops_page to avoid
    OAuth issues with multiple sessions for the same user.
    """
    if (
        admin_user_credentials["username"] == ops_user_credentials["username"]
        and admin_user_credentials["password"] == ops_user_credentials["password"]
    ):
        yield ops_page
        return

    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    _perform_authentik_login(
        page, admin_user_credentials["username"], admin_user_credentials["password"], live_server_url
    )
    yield page
    page.close()
    context.close()


# ============================================================================
# HTTP Client Fixtures
# ============================================================================


@pytest.fixture
def http_client():
    """Create HTTP client for API testing."""
    import httpx

    client = httpx.Client(headers={"User-Agent": "WCComps Integration Tests"})

    yield client
    client.close()


@pytest.fixture
def authenticated_http_client(http_client, authentik_credentials):
    """
    Create authenticated HTTP client by performing OAuth login.
    Returns an httpx.Client with valid session cookies.
    """
    # OAuth flow is complex to implement via HTTP client
    # Browser-based tests (authenticated_page fixture) handle this properly
    # This fixture is a placeholder for now
    return http_client


# ============================================================================
# Helper Functions
# ============================================================================


def create_test_ticket(title: str, description: str = "Test ticket", team_id: int = 50):
    """
    Create a test ticket in the database.
    Tickets are automatically marked with [INTEGRATION TEST] prefix.
    """
    from team.models import Team
    from ticketing.models import Ticket

    team = Team.objects.get(team_number=team_id)

    return Ticket.objects.create(
        title=f"[INTEGRATION TEST] {title}",
        description=description,
        team=team,
        status="open",
    )


def cleanup_test_tickets():
    """Manually clean up all test tickets."""
    from ticketing.models import Ticket

    Ticket.objects.filter(title__startswith="[INTEGRATION TEST]").delete()


# ============================================================================
# Pytest Configuration
# ============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "critical: Critical tests that must pass on every deployment")
    config.addinivalue_line("markers", "integration: Integration tests with real database and APIs")
    config.addinivalue_line("markers", "browser: Browser-based UI tests using Playwright")
    config.addinivalue_line("markers", "load: Load and stress tests for concurrency and performance")
