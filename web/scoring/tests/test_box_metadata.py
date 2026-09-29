"""The submit forms read box metadata from the synced cache, never from Quotient during the request."""

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from scoring.models import QuotientMetadataCache

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("url_name", "user_fixture"),
    [("scoring:submit_incident_report", "gold_team_user"), ("scoring:submit_red_score", "red_team_user")],
)
def test_submit_page_uses_cached_metadata_without_calling_quotient(request, url_name, user_fixture):
    QuotientMetadataCache.objects.create(
        boxes=[{"name": "web01", "ip": "10.0.0.5", "services": [{"name": "http"}, {"name": "ssh"}]}]
    )
    client = Client()
    client.force_login(request.getfixturevalue(user_fixture))

    with patch("quotient.client.QuotientClient._request") as quotient_request:
        response = client.get(reverse(url_name))

    assert response.status_code == 200
    quotient_request.assert_not_called()
    assert response.context["box_metadata"] == {"web01": {"ip": "10.0.0.5", "services": ["http", "ssh"]}}
