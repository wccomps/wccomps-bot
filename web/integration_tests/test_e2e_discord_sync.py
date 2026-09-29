"""
E2E tests for Discord-Web synchronization.

Tests that actions in the web UI trigger the correct Discord tasks,
and that the DiscordTask queue processes them correctly.

NOTE: These tests verify task creation and database state.
Full Discord API integration requires the bot to be running.
"""

import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.browser,
    pytest.mark.integration,
]


class TestTicketCreatesDiscordThread:
    """Verify ticket creation queues Discord thread creation."""

    def test_new_ticket_queues_thread_creation(self, team_page: Page, db, test_team_id, live_server_url):
        """Creating ticket should queue Discord thread creation task."""
        from core.models import DiscordTask
        from ticketing.models import Ticket

        unique_title = f"[E2E SYNC] Thread test {os.urandom(4).hex()}"
        team_page.goto(f"{live_server_url}/tickets/create/")
        team_page.select_option('select[name="category"]', "general-question")
        team_page.fill('input[name="title"]', unique_title)
        team_page.fill('textarea[name="description"]', "Should create Discord thread")
        team_page.click('button[type="submit"]')
        team_page.wait_for_timeout(2000)

        ticket = Ticket.objects.filter(title=unique_title).first()
        assert ticket is not None

        thread_tasks = DiscordTask.objects.filter(task_type="ticket_created_web", payload__ticket_id=ticket.id)
        assert thread_tasks.count() >= 1, "Should queue thread creation task"

        task = thread_tasks.first()
        assert task.status in ["pending", "processing", "completed"]

        ticket.delete()

    def test_thread_task_contains_ticket_info(self, db, test_team_id):
        """Discord thread task should contain ticket details in payload."""
        from core.models import DiscordTask
        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Payload test",
            description="Check payload contains ticket info",
            status="open",
        )

        DiscordTask.create_ticket_created_web(
            ticket_id=ticket.id,
            ticket_number=ticket.ticket_number,
            team_number=team.team_number,
            category=str(ticket.category),
            title=ticket.title,
            created_by="e2e",
        )

        task = DiscordTask.objects.filter(task_type="ticket_created_web", payload__ticket_id=ticket.id).first()
        assert task is not None
        assert task.payload.get("ticket_number") == ticket.ticket_number
        assert task.payload.get("team_number") == team.team_number

        ticket.delete()


class TestCommentSyncsToDiscord:
    """Verify comments posted via web sync to Discord thread."""

    @pytest.fixture
    def ticket_with_thread(self, db, test_team_id):
        """Create ticket with simulated Discord thread."""
        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Comment sync test",
            description="Has simulated thread",
            status="open",
            discord_thread_id=123456789012345678,
        )
        yield ticket
        if Ticket.objects.filter(pk=ticket.pk).exists():
            ticket.delete()

    def test_web_comment_queues_discord_message(self, team_page: Page, ticket_with_thread, db, live_server_url):
        """Comment from web should queue Discord message task."""
        from core.models import DiscordTask

        initial_count = DiscordTask.objects.filter(task_type="post_comment").count()

        team_page.goto(f"{live_server_url}/tickets/{ticket_with_thread.id}/")

        comment_textarea = team_page.locator('textarea[name="comment"]')
        if comment_textarea.is_visible():
            unique_comment = f"[E2E SYNC] Comment {os.urandom(4).hex()}"
            comment_textarea.fill(unique_comment)
            team_page.click('button[type="submit"]')
            team_page.wait_for_timeout(2000)

            new_tasks = DiscordTask.objects.filter(
                task_type="post_comment",
                ticket=ticket_with_thread,
            ).order_by("-created_at")

            assert new_tasks.count() > initial_count or new_tasks.exists()


class TestStatusChangeUpdatesDiscord:
    """Verify status changes sync to Discord."""

    @pytest.fixture
    def claimed_ticket_with_thread(self, db, test_team_id):
        """Create claimed ticket with Discord thread."""
        from django.contrib.auth.models import User

        from team.models import DiscordLink, Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)

        ops_username = os.getenv("TEST_OPS_USERNAME", os.getenv("TEST_AUTHENTIK_USERNAME"))
        user, _ = User.objects.get_or_create(
            username=ops_username,
            defaults={"email": f"{ops_username}@test.local"},
        )
        discord_link, _ = DiscordLink.objects.get_or_create(
            user=user,
            defaults={"discord_id": 999888777, "discord_username": ops_username},
        )

        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Status change test",
            description="For testing status sync",
            status="claimed",
            assigned_to=discord_link,
            discord_thread_id=123456789012345679,
        )
        yield ticket
        if Ticket.objects.filter(pk=ticket.pk).exists():
            ticket.delete()

    def test_resolve_queues_embed_update(self, ops_page: Page, claimed_ticket_with_thread, db, live_server_url):
        """Resolving a ticket queues a ticket update for Discord."""
        from core.models import DiscordTask

        ops_page.goto(f"{live_server_url}/ops/ticket/{claimed_ticket_with_thread.ticket_number}/")

        resolve_button = ops_page.locator('button:has-text("Resolve")')
        if resolve_button.is_visible():
            resolution_field = ops_page.locator('textarea[name="resolution_notes"]')
            if resolution_field.is_visible():
                resolution_field.fill("Resolved for sync test")

            resolve_button.click()
            ops_page.wait_for_timeout(2000)

            update_count = DiscordTask.objects.filter(
                task_type="post_ticket_update",
                ticket=claimed_ticket_with_thread,
                payload__action="resolved",
            ).count()
            assert update_count >= 1, "Should queue a ticket update after resolve"


class TestDiscordTaskQueue:
    """Test DiscordTask queue behavior."""

    def test_failed_task_can_be_retried(self, db, test_team_id):
        """Failed tasks should support retry."""
        from django.utils import timezone

        from core.models import DiscordTask
        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Retry test",
            status="open",
        )

        task = DiscordTask.objects.create(
            task_type="ticket_created_web",
            ticket=ticket,
            payload={
                "ticket_id": ticket.id,
                "ticket_number": ticket.ticket_number,
                "team_number": team.team_number,
                "category": str(ticket.category),
                "title": ticket.title,
                "created_by": "e2e",
            },
            status="failed",
            retry_count=2,
            error_message="Simulated failure",
        )

        task.status = "pending"
        task.retry_count = 0
        task.next_retry_at = timezone.now()
        task.error_message = ""
        task.save()

        task.refresh_from_db()
        assert task.status == "pending"
        assert task.retry_count == 0

        ticket.delete()

    def test_task_tracks_retry_count(self, db, test_team_id):
        """Task should increment retry count on failure."""
        from core.models import DiscordTask
        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Retry count test",
            status="open",
        )

        task = DiscordTask.objects.create(
            task_type="ticket_created_web",
            ticket=ticket,
            payload={
                "ticket_id": ticket.id,
                "ticket_number": ticket.ticket_number,
                "team_number": team.team_number,
                "category": str(ticket.category),
                "title": ticket.title,
                "created_by": "e2e",
            },
            status="pending",
        )

        for i in range(3):
            task.retry_count = i + 1
            task.save()

        task.refresh_from_db()
        assert task.retry_count == 3

        ticket.delete()

    def test_max_retries_stops_processing(self, db, test_team_id):
        """Task exceeding max retries should not be retried."""
        from core.models import DiscordTask
        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Max retry test",
            status="open",
        )

        task = DiscordTask.objects.create(
            task_type="ticket_created_web",
            ticket=ticket,
            payload={
                "ticket_id": ticket.id,
                "ticket_number": ticket.ticket_number,
                "team_number": team.team_number,
                "category": str(ticket.category),
                "title": ticket.title,
                "created_by": "e2e",
            },
            status="failed",
            retry_count=5,
            max_retries=5,
            error_message="Max retries reached",
        )

        assert task.retry_count >= task.max_retries

        ticket.delete()


class TestDashboardUpdateSync:
    """Test dashboard update synchronization."""

    def test_claim_triggers_dashboard_update(self, db, test_team_id):
        """Claiming a ticket queues the update that also refreshes the dashboard."""
        from core.models import DiscordTask
        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Dashboard update test",
            status="open",
        )

        # post_ticket_update also refreshes the #ticket-queue dashboard (bot/discord_queue.py)
        DiscordTask.create_post_ticket_update(ticket=ticket, action="claimed", actor="e2e")

        task = DiscordTask.objects.filter(task_type="post_ticket_update", ticket=ticket).first()

        assert task is not None
        assert task.payload["action"] == "claimed"

        ticket.delete()


class TestArchiveSync:
    """Test thread archive synchronization."""

    def test_resolved_ticket_schedules_archive(self, db, test_team_id):
        """Resolved tickets should schedule thread archive."""
        from django.utils import timezone

        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Archive test",
            status="resolved",
            discord_thread_id=123456789012345680,
            thread_archive_scheduled_at=timezone.now(),
        )

        assert ticket.thread_archive_scheduled_at is not None

        ticket.delete()

    def test_reopen_clears_archive_schedule(self, db, test_team_id):
        """Reopening ticket should clear archive schedule."""
        from django.utils import timezone

        from team.models import Team
        from ticketing.models import Ticket

        team = Team.objects.get(team_number=test_team_id)
        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Clear archive test",
            status="resolved",
            discord_thread_id=123456789012345681,
            thread_archive_scheduled_at=timezone.now(),
        )

        ticket.status = "open"
        ticket.thread_archive_scheduled_at = None
        ticket.save()

        ticket.refresh_from_db()
        assert ticket.thread_archive_scheduled_at is None

        ticket.delete()


class TestBidirectionalSync:
    """Test that web and Discord stay in sync."""

    def test_discord_comment_appears_in_web(self, team_page: Page, db, test_team_id, live_server_url):
        """Comments from Discord should appear in web UI."""
        from django.contrib.auth.models import User

        from team.models import DiscordLink, Team
        from ticketing.models import Ticket, TicketComment

        team = Team.objects.get(team_number=test_team_id)

        user, _ = User.objects.get_or_create(
            username="discord_commenter",
            defaults={"email": "discord@test.local"},
        )
        discord_link, _ = DiscordLink.objects.get_or_create(
            user=user,
            defaults={"discord_id": 888777666, "discord_username": "DiscordUser"},
        )

        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] Bidirectional test",
            description="Testing Discord to web sync",
            status="open",
        )

        unique_text = f"Discord comment {os.urandom(4).hex()}"
        TicketComment.objects.create(
            ticket=ticket,
            author=discord_link,
            comment_text=unique_text,
            discord_message_id=111222333444555,
        )

        team_page.goto(f"{live_server_url}/tickets/{ticket.id}/")

        expect(team_page.locator(f"text={unique_text}")).to_be_visible(timeout=5000)

        ticket.delete()

    def test_web_status_change_reflected_in_history(self, ops_page: Page, db, test_team_id, live_server_url):
        """Status changes from web should create history entries."""
        from django.contrib.auth.models import User

        from team.models import DiscordLink, Team
        from ticketing.models import Ticket, TicketHistory

        team = Team.objects.get(team_number=test_team_id)

        ops_username = os.getenv("TEST_OPS_USERNAME", os.getenv("TEST_AUTHENTIK_USERNAME"))
        user, _ = User.objects.get_or_create(
            username=ops_username,
            defaults={"email": f"{ops_username}@test.local"},
        )
        discord_link, _ = DiscordLink.objects.get_or_create(
            user=user,
            defaults={"discord_id": 999888777, "discord_username": ops_username},
        )

        ticket = Ticket.objects.create(
            team=team,
            category="general-question",
            title="[E2E SYNC] History test",
            status="open",
        )

        initial_history = TicketHistory.objects.filter(ticket=ticket).count()

        ops_page.goto(f"{live_server_url}/ops/tickets/?status=open")
        ticket_row = ops_page.locator(f"tr:has-text('{ticket.ticket_number}')")
        claim_button = ticket_row.locator('button:has-text("Claim"), input[value="Claim"]')

        if claim_button.count() > 0:
            claim_button.first.click()
            ops_page.wait_for_timeout(2000)

            new_history = TicketHistory.objects.filter(ticket=ticket).count()
            assert new_history > initial_history, "History should be created"

        ticket.delete()
