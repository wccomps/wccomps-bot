"""Tests for clear tickets functionality."""

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from core.models import AuditLog, UserGroups
from team.models import Team
from ticketing.models import Ticket, TicketAttachment, TicketCategory, TicketComment, TicketHistory
from ticketing.utils import clear_all_tickets

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup_teams() -> tuple[Team, Team]:
    """Create test teams."""
    team1 = Team.objects.create(
        team_name="Team Alpha",
        team_number=1,
        max_members=5,
        ticket_counter=5,
    )
    team2 = Team.objects.create(
        team_name="Team Beta",
        team_number=2,
        max_members=5,
        ticket_counter=3,
    )
    return team1, team2


@pytest.fixture
def setup_tickets(setup_teams: tuple[Team, Team]) -> tuple[Team, Team, list[Ticket]]:
    """Create test tickets with related data."""
    team1, team2 = setup_teams

    ticket1 = Ticket.objects.create(
        ticket_number="T001-001",
        team=team1,
        category=TicketCategory.objects.get(pk=6),
        title="Test Ticket 1",
        description="Test description",
        status="open",
    )

    ticket2 = Ticket.objects.create(
        ticket_number="T001-002",
        team=team1,
        category=TicketCategory.objects.get(pk=6),
        title="Test Ticket 2",
        description="Test description",
        status="claimed",
    )

    ticket3 = Ticket.objects.create(
        ticket_number="T002-001",
        team=team2,
        category=TicketCategory.objects.get(pk=6),
        title="Test Ticket 3",
        description="Test description",
        status="resolved",
    )

    # Add related data
    TicketComment.objects.create(
        ticket=ticket1,
        comment_text="Test comment",
    )

    TicketAttachment.objects.create(
        ticket=ticket1,
        file_data=b"test data",
        filename="test.txt",
        mime_type="text/plain",
        uploaded_by="test_user",
    )

    TicketHistory.objects.create(
        ticket=ticket1,
        action="created",
        details={"created_by": "test_user"},
    )

    return team1, team2, [ticket1, ticket2, ticket3]


@pytest.mark.django_db
class TestClearAllTickets:
    """clear_all_tickets: shared by the ops page and /tickets clear."""

    def test_clears_all_tickets(self, setup_tickets: tuple[Team, Team, list[Ticket]]) -> None:
        team1, team2, _ = setup_tickets

        counts = clear_all_tickets(actor="web:admin")

        assert counts["tickets_deleted"] == 3
        assert counts["teams_reset"] == 2
        assert Ticket.objects.count() == 0
        assert TicketComment.objects.count() == 0
        assert TicketAttachment.objects.count() == 0
        assert TicketHistory.objects.count() == 0
        team1.refresh_from_db()
        team2.refresh_from_db()
        assert (team1.ticket_counter, team2.ticket_counter) == (0, 0)
        audit = AuditLog.objects.get(action="clear_tickets")
        assert audit.admin_user == "web:admin"
        assert audit.details == counts

    def test_handles_no_tickets(self, setup_teams: tuple[Team, Team]) -> None:
        assert clear_all_tickets(actor="web:admin")["tickets_deleted"] == 0


@pytest.mark.django_db
class TestClearTicketsWebView:
    """Test clear_tickets web view."""

    @pytest.fixture
    def setup_users_and_auth(self, setup_teams: tuple[Team, Team]) -> dict[str, Any]:
        """Create test users with authentication."""
        team1, team2 = setup_teams

        # Admin user
        admin_user = User.objects.create_user(username="admin_user", password="test123")
        UserGroups.objects.create(user=admin_user, authentik_id="admin_uid", groups=["WCComps_Ticketing_Admin"])

        # Support user (not admin)
        support_user = User.objects.create_user(username="support_user", password="test123")
        UserGroups.objects.create(user=support_user, authentik_id="support_uid", groups=["WCComps_Ticketing_Support"])

        # Team user
        team_user = User.objects.create_user(username="team_user", password="test123")
        UserGroups.objects.create(user=team_user, authentik_id="team_uid", groups=["WCComps_BlueTeam01"])

        return {
            "admin_user": admin_user,
            "support_user": support_user,
            "team_user": team_user,
        }

    def test_clear_requires_admin_permission(
        self, setup_tickets: tuple[Team, Team, list[Ticket]], setup_users_and_auth: dict[str, Any]
    ) -> None:
        """Test only admins can clear tickets."""
        client = Client()

        # Support user should be denied
        client.force_login(setup_users_and_auth["support_user"])
        response = client.post("/tickets/clear-all/")
        assert response.status_code == 403

        # Team user should be denied
        client.force_login(setup_users_and_auth["team_user"])
        response = client.post("/tickets/clear-all/")
        assert response.status_code == 403

    def test_clear_requires_post_method(
        self, setup_tickets: tuple[Team, Team, list[Ticket]], setup_users_and_auth: dict[str, Any]
    ) -> None:
        """Test clear endpoint requires POST."""
        client = Client()
        client.force_login(setup_users_and_auth["admin_user"])

        response = client.get("/tickets/clear-all/")
        assert response.status_code == 405

    def test_clear_deletes_all_tickets(
        self, setup_tickets: tuple[Team, Team, list[Ticket]], setup_users_and_auth: dict[str, Any]
    ) -> None:
        """Test admin can clear all tickets via web UI."""
        team1, team2, _ = setup_tickets
        client = Client()
        client.force_login(setup_users_and_auth["admin_user"])

        # Verify initial state
        assert Ticket.objects.count() == 3
        assert team1.ticket_counter == 5
        assert team2.ticket_counter == 3

        response = client.post("/tickets/clear-all/")

        # Should redirect to ticket list
        assert response.status_code == 302
        assert response["Location"] == "/tickets/"

        # Verify all tickets deleted
        assert Ticket.objects.count() == 0

        # Verify counters reset
        team1.refresh_from_db()
        team2.refresh_from_db()
        assert team1.ticket_counter == 0
        assert team2.ticket_counter == 0

        # Verify audit log
        audit = AuditLog.objects.filter(action="clear_tickets").first()
        assert audit is not None
        assert audit.admin_user == "admin_user"
        assert audit.details["tickets_deleted"] == 3
