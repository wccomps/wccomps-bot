"""Seeded ticket categories survive transactional-test flushes under xdist."""

import pytest

from ticketing.models import TicketCategory
from ticketing.tests.seeded_categories import SEEDED_CATEGORY_IDS, ensure_seeded_categories

pytestmark = pytest.mark.django_db


def test_restores_categories_after_flush():
    """A transactional test's flush empties the table; the helper puts the seed rows back."""
    TicketCategory.objects.all().delete()

    ensure_seeded_categories()

    assert set(TicketCategory.objects.values_list("pk", flat=True)) == set(SEEDED_CATEGORY_IDS)
    assert TicketCategory.objects.get(pk=6).display_name == "Other / General Issue"


def test_new_category_after_reseed_does_not_collide():
    """Seeds use explicit ids, so the sequence must be moved past them."""
    TicketCategory.objects.all().delete()
    ensure_seeded_categories()

    created = TicketCategory.objects.create(display_name="Extra", points=0, sort_order=99)

    assert created.pk not in SEEDED_CATEGORY_IDS


def test_noop_when_categories_present():
    before = list(TicketCategory.objects.order_by("pk").values_list("pk", "display_name"))

    ensure_seeded_categories()

    assert list(TicketCategory.objects.order_by("pk").values_list("pk", "display_name")) == before
