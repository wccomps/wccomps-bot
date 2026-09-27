"""Test support for ticketing: restore migration-seeded categories."""

import importlib

from django.apps import apps
from django.core.management.color import no_style
from django.db import connection

from ticketing.models import TicketCategory

SEEDED_CATEGORY_IDS = (1, 2, 3, 4, 5, 6)


def ensure_seeded_categories() -> None:
    """Recreate the categories from migration 0009 if they are missing.

    They exist only because of a data migration, and pytest-django flushes every table after a
    transactional test. Under xdist worksteal a worker can run ordinary tests after that flush.
    """
    if TicketCategory.objects.filter(pk__in=SEEDED_CATEGORY_IDS).count() == len(SEEDED_CATEGORY_IDS):
        return

    TicketCategory.objects.filter(pk__in=SEEDED_CATEGORY_IDS).delete()
    seed = importlib.import_module("ticketing.migrations.0009_seed_ticket_categories")
    seed.seed_categories(apps, None)

    # Seeds use explicit ids; move the sequence past them so later creates don't collide
    with connection.cursor() as cursor:
        for sql in connection.ops.sequence_reset_sql(no_style(), [TicketCategory]):
            cursor.execute(sql)
