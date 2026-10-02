"""Metadata sync: always a fresh fetch, and a failed one keeps the last good metadata."""

from unittest.mock import patch

import pytest

from scoring.models import QuotientMetadataCache
from scoring.quotient_sync import sync_quotient_metadata

pytestmark = pytest.mark.django_db


def test_a_failed_sync_keeps_the_last_metadata():
    """One Quotient blip mid-competition used to empty the red-team and incident dropdowns for 15 minutes."""
    QuotientMetadataCache.objects.create(boxes=[{"name": "web", "ip": "10.0.0.5"}], services=[{"name": "web:http"}])

    with patch("scoring.quotient_sync.QuotientClient") as client:
        client.return_value.get_infrastructure.return_value = None
        with pytest.raises(ValueError, match="Failed to retrieve infrastructure"):
            sync_quotient_metadata()

    client.return_value.get_infrastructure.assert_called_once_with(force_refresh=True)
    assert QuotientMetadataCache.objects.get().boxes == [{"name": "web", "ip": "10.0.0.5"}]
