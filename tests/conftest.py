from __future__ import annotations

import pytest


@pytest.fixture
def browser_static_storage(settings):  # type: ignore[no-untyped-def]
    """Serve unhashed source assets through pytest-django's live server."""

    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

