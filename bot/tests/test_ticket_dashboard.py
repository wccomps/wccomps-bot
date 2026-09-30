"""Tests for ticket dashboard functionality."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import User
from django.test import TestCase

from bot.ticket_dashboard import (
    format_ticket_embed,
    post_ticket_to_dashboard,
    update_ticket_dashboard,
)
from team.models import DiscordLink, Team
from ticketing.models import Ticket, TicketCategory


@pytest.mark.django_db(transaction=True)
class TicketDashboardTest(TestCase):
    """Test ticket dashboard embed generation."""

    def setUp(self) -> None:
        self.team = Team.objects.create(
            team_number=40,
            team_name="Test Team",
            authentik_group="test-group",
        )

    def test_format_ticket_embed_comprehensive(self) -> None:
        """Test formatting ticket embeds with various states and categories."""
        from django.utils import timezone as tz

        from core.tickets_config import get_all_categories

        # Test basic open ticket
        box_reset = TicketCategory.objects.get(pk=2)
        open_ticket = Ticket.objects.create(
            ticket_number="T001-001",
            team=self.team,
            category=box_reset,
            title="Test Open",
            description="Open description",
            status="open",
        )
        open_embed = format_ticket_embed(open_ticket)
        self.assertIsNotNone(open_embed)
        self.assertIn("Test Open", str(open_embed.title))

        # Create DiscordLink for assignment testing
        volunteer_user = User.objects.create(username="volunteer1")
        DiscordLink.objects.create(
            user=volunteer_user,
            discord_id=123456789,
            discord_username="volunteer1",
        )

        # Test claimed ticket with assignment (assigned_to is now User, not DiscordLink)
        scoring_check = TicketCategory.objects.get(pk=3)
        claimed_ticket = Ticket.objects.create(
            ticket_number="T001-002",
            team=self.team,
            category=scoring_check,
            title="Test Claimed",
            description="Claimed description",
            status="claimed",
            assigned_to=volunteer_user,
        )
        claimed_embed = format_ticket_embed(claimed_ticket)
        field_names = [field.name for field in claimed_embed.fields]
        self.assertIn("Assigned To", field_names)

        # Create user for resolver
        admin_user = User.objects.create(username="admin1")
        DiscordLink.objects.create(
            user=admin_user,
            discord_id=987654321,
            discord_username="admin1",
        )

        # Test resolved ticket with resolution info (resolved_by is now User, not DiscordLink)
        other_cat = TicketCategory.objects.get(pk=6)
        resolved_ticket = Ticket.objects.create(
            ticket_number="T001-003",
            team=self.team,
            category=other_cat,
            title="Test Resolved",
            description="Resolved description",
            status="resolved",
            resolved_at=tz.now(),
            resolved_by=admin_user,
            resolution_notes="All fixed",
            points_charged=10,
        )
        resolved_embed = format_ticket_embed(resolved_ticket)
        resolved_field_names = [field.name for field in resolved_embed.fields]
        self.assertIn("Resolved At", resolved_field_names)

        # Test that all ticket categories can be formatted
        all_categories = get_all_categories()
        for idx, (cat_pk, cat_info) in enumerate(all_categories.items(), start=10):
            cat_obj = TicketCategory.objects.get(pk=cat_pk)
            ticket = Ticket.objects.create(
                ticket_number=f"T001-{idx:03d}",
                team=self.team,
                category=cat_obj,
                title=f"Test {cat_info['display_name']}",
                description="Test",
                status="open",
            )
            embed = format_ticket_embed(ticket)
            self.assertIsNotNone(embed)

    def test_format_ticket_embed_includes_category_fields(self) -> None:
        """Embed should include hostname, service_name, and ip_address when present."""
        ticket = Ticket.objects.create(
            ticket_number="T001-099",
            team=self.team,
            category=TicketCategory.objects.get(pk=2),
            title="Reset Request",
            description="Please reset my box",
            status="open",
            hostname="webserver01",
            service_name="web:http",
            ip_address="10.0.0.42",
        )
        embed = format_ticket_embed(ticket)
        field_names = [field.name for field in embed.fields]
        field_values = [field.value for field in embed.fields]

        self.assertIn("Hostname", field_names)
        self.assertIn("Service", field_names)
        self.assertIn("IP Address", field_names)
        self.assertIn("webserver01", field_values)
        self.assertIn("web:http", field_values)
        self.assertIn("10.0.0.42", field_values)

    def test_format_ticket_embed_omits_empty_category_fields(self) -> None:
        """Embed should omit hostname/service/IP fields when not set."""
        ticket = Ticket.objects.create(
            ticket_number="T001-100",
            team=self.team,
            category=TicketCategory.objects.get(pk=6),
            title="General Question",
            description="Just a question",
            status="open",
        )
        embed = format_ticket_embed(ticket)
        field_names = [field.name for field in embed.fields]

        self.assertNotIn("Hostname", field_names)
        self.assertNotIn("Service", field_names)
        self.assertNotIn("IP Address", field_names)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestDashboardUpdate:
    """Test dashboard update functions."""

    async def test_post_ticket_to_dashboard_with_unified_dashboard(self, box_reset_category: TicketCategory) -> None:
        """Test posting ticket when unified dashboard exists."""
        bot = MagicMock()
        unified_dashboard = MagicMock()
        unified_dashboard.trigger_update = MagicMock()
        bot.unified_dashboard = unified_dashboard

        team = await Team.objects.acreate(team_number=27, team_name="Test Team", authentik_group="test")
        ticket = await Ticket.objects.acreate(
            ticket_number="T001",
            team=team,
            category=box_reset_category,
            title="Test",
            description="Test",
            status="open",
        )

        post_ticket_to_dashboard(bot, ticket)

        unified_dashboard.trigger_update.assert_called_once()

    async def test_post_ticket_to_dashboard_no_unified_dashboard(self, box_reset_category: TicketCategory) -> None:
        """Test posting ticket when unified dashboard doesn't exist."""
        bot = MagicMock()
        bot.unified_dashboard = None

        team = await Team.objects.acreate(team_number=28, team_name="Test Team", authentik_group="test")
        ticket = await Ticket.objects.acreate(
            ticket_number="T002",
            team=team,
            category=box_reset_category,
            title="Test",
            description="Test",
            status="open",
        )

        post_ticket_to_dashboard(bot, ticket)

    async def test_update_ticket_dashboard_with_unified_dashboard(self, box_reset_category: TicketCategory) -> None:
        """Test updating dashboard when unified dashboard exists."""
        bot = MagicMock()
        unified_dashboard = MagicMock()
        unified_dashboard.trigger_update = MagicMock()
        bot.unified_dashboard = unified_dashboard

        team = await Team.objects.acreate(team_number=29, team_name="Test Team", authentik_group="test")
        ticket = await Ticket.objects.acreate(
            ticket_number="T003",
            team=team,
            category=box_reset_category,
            title="Test",
            description="Test",
            status="open",
        )

        update_ticket_dashboard(bot, ticket)

        unified_dashboard.trigger_update.assert_called_once()
