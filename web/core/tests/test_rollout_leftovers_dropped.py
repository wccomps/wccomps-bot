"""Columns and tables kept only for rolling deploys are gone once no pod runs the release that used them."""

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


def test_kept_tables_are_dropped() -> None:
    tables = set(connection.introspection.table_names())
    assert not tables & {"final_score", "core_dashboardupdate"}


def test_kept_columns_are_dropped() -> None:
    assert "last_check" not in _columns("core_competitionconfig")
    assert "max_members" not in _columns("team_team")
