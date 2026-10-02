"""Live comment/history polling on the ticket detail page."""

import re

import pytest
from django.test import Client
from django.urls import reverse

from team.models import Team
from ticketing.models import Ticket, TicketCategory

pytestmark = pytest.mark.django_db


@pytest.fixture
def tickets():
    own = Team.objects.create(team_number=1, team_name="Team 01", authentik_group="WCComps_BlueTeam01")
    other = Team.objects.create(team_number=2, team_name="Team 02", authentik_group="WCComps_BlueTeam02")
    category = TicketCategory.objects.get(pk=6)
    return (
        Ticket.objects.create(team=own, ticket_number="T001-001", category=category, title="Own"),
        Ticket.objects.create(team=other, ticket_number="T002-001", category=category, title="Other"),
    )


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def test_team_member_polls_own_ticket(blue_team_user, tickets):
    response = _client(blue_team_user).get(reverse("ticket_detail_dynamic", args=["T001-001"]))
    assert response.status_code == 200
    assert b'id="comments-list"' in response.content


def test_team_member_cannot_poll_other_teams_ticket(blue_team_user, tickets):
    response = _client(blue_team_user).get(reverse("ticket_detail_dynamic", args=["T002-001"]))
    assert response.status_code == 403


@pytest.mark.parametrize("list_id", ["comments-list", "history-list"])
def test_polled_list_is_not_the_polling_element(ticketing_support_user, tickets, list_id):
    """The partial's list has no hx-* attributes, so swapping it over the poller would stop polling."""
    html = _client(ticketing_support_user).get(reverse("ticket_detail", args=["T001-001"])).content.decode()
    tag = re.search(rf'<div[^>]*id="{list_id}"[^>]*>', html)
    assert tag and "hx-get" not in tag.group(0)
    assert f'hx-select="#{list_id}"' in html


def _with_staff_history(ticket):
    from django.contrib.auth.models import User

    from ticketing.models import TicketHistory

    staff = User.objects.create(username="staff-volunteer")
    TicketHistory.objects.create(
        ticket=ticket, action="verified", actor=staff, details={"approval_notes": "staff-only note"}
    )


def test_team_poll_carries_no_history(blue_team_user, tickets):
    """History holds staff notes, points and usernames; the team page never shows it, so the poll mustn't send it."""
    _with_staff_history(tickets[0])

    content = _client(blue_team_user).get(reverse("ticket_detail_dynamic", args=["T001-001"])).content

    assert b"staff-volunteer" not in content
    assert b"staff-only note" not in content


def test_staff_poll_carries_history(ticketing_support_user, tickets):
    _with_staff_history(tickets[0])

    content = _client(ticketing_support_user).get(reverse("ticket_detail_dynamic", args=["T001-001"])).content

    assert b"staff-volunteer" in content
