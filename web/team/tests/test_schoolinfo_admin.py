"""The Django admin can't edit a team's stored packet password: it would change only the portal's copy."""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import UserGroups
from team.models import SchoolInfo, Team

pytestmark = pytest.mark.django_db


def test_admin_save_leaves_the_stored_password_alone():
    team = Team.objects.create(team_number=7, team_name="Team 07")
    info = SchoolInfo.objects.create(
        team=team, school_name="Example University", contact_email="c@example.com", password="Real-Pass-1!"
    )
    admin = User.objects.create(username="admin", is_staff=True, is_superuser=True)
    UserGroups.objects.update_or_create(user=admin, defaults={"groups": ["WCComps_Discord_Admin"], "authentik_id": "x"})
    client = Client()
    client.force_login(admin)
    url = reverse("admin:team_schoolinfo_change", args=[info.pk])

    page = client.get(url).content.decode()
    response = client.post(
        url,
        {
            "team": team.pk,
            "school_name": "Example University",
            "contact_email": "c@example.com",
            "secondary_email": "",
            "notes": "",
            "password": "Typed-Here-2!",
        },
    )

    assert 'name="password"' not in page
    assert response.status_code == 302
    info.refresh_from_db()
    assert info.password == "Real-Pass-1!"
