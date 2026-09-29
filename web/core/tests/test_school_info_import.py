"""School info CSV import: upload previews, confirm imports, and failures say why."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from team.models import SchoolInfo, Team

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(gold_team_user):
    client = Client()
    client.force_login(gold_team_user)
    return client


def test_upload_then_confirm_imports(client):
    Team.objects.create(team_number=1, team_name="Team 01", is_active=True)
    csv = SimpleUploadedFile("schools.csv", b"school_name,contact_email\nExample University,coach@example.edu\n")

    preview = client.post(reverse("school_info_import"), {"upload": "true", "csv_file": csv})
    assert b"Example University" in preview.content

    client.post(reverse("school_info_import"), {"confirm": "true"})
    assert SchoolInfo.objects.filter(school_name="Example University").exists()


def test_confirm_after_the_upload_expired_says_so(client):
    response = client.post(reverse("school_info_import"), {"confirm": "true"})
    assert b"no longer available" in response.content
