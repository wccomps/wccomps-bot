"""Production settings must not change behavior because a test runner is loaded."""

import runpy
from pathlib import Path

import portal
from django.conf import settings

SETTINGS_PATH = Path(portal.__file__).parent / "settings.py"


def test_production_settings_use_manifest_storage_under_pytest() -> None:
    prod = runpy.run_path(str(SETTINGS_PATH))

    assert prod["STORAGES"]["staticfiles"]["BACKEND"] == "whitenoise.storage.CompressedManifestStaticFilesStorage"
    assert prod["DATABASES"]["default"]["CONN_MAX_AGE"] == 600
    assert "PASSWORD_HASHERS" not in prod


def test_suite_runs_on_test_settings() -> None:
    assert settings.SETTINGS_MODULE == "portal.test_settings"
    assert settings.STORAGES["staticfiles"]["BACKEND"] == "django.contrib.staticfiles.storage.StaticFilesStorage"
    assert settings.DATABASES["default"]["CONN_MAX_AGE"] == 0
