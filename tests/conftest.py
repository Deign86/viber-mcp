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
