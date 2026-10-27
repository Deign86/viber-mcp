"""Backend selection. Returns the singleton Backend for the current platform."""

from __future__ import annotations

import sys

from .base import Backend, OCRError

_backend: Backend | None = None


def get_backend() -> Backend:
    """Return the platform Backend (cached). Raises on unsupported platforms."""
    global _backend
    if _backend is None:
        if sys.platform == "darwin":
            from .macos import MacBackend
            _backend = MacBackend()
        elif sys.platform == "win32":
            from .windows import WindowsBackend
            _backend = WindowsBackend()
        else:
            # Port target: implement backends/linux.py (X11 easier than Wayland), then wire here.
            raise NotImplementedError(
                f"No Viber backend for platform {sys.platform!r} yet "
                "(macOS and Windows implemented; Linux not started)."
            )
    return _backend


__all__ = ["Backend", "OCRError", "get_backend"]
