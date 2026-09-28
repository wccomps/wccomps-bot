"""/livez/ answers "is this process serving?" without touching the database (Kubernetes liveness)."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext


@pytest.mark.django_db
def test_livez_is_public_and_needs_no_database():
    with CaptureQueriesContext(connection) as queries:
        response = Client().get("/livez/")

    assert response.status_code == 200
    assert response.content == b"ok"
    assert len(queries) == 0
