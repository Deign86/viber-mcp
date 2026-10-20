import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backends  # noqa: E402


class FakeBackend:
    """Contract-level stand-in for the platform backend: serves canned elements."""

    def __init__(self, elements=None):
        self._elements = elements or []

    def elements(self):
        return self._elements


@pytest.fixture
def fake_backend():
    previous = backends._backend
    backends._backend = FakeBackend()
    yield backends._backend
    backends._backend = previous


@pytest.fixture(autouse=True)
def no_real_drive_uploads(monkeypatch, tmp_path):
    """Tests must never start the real OAuth sign-in (a test once opened the browser and hung):
    point the upload client/token at files that don't exist."""
    import viber_drive

    monkeypatch.setattr(viber_drive, "CLIENT_FILE", tmp_path / "no_client.json")
    monkeypatch.setattr(viber_drive, "WRITE_TOKEN_FILE", tmp_path / "no_token.json")
    monkeypatch.setattr(viber_drive, "_writer_service", None)
