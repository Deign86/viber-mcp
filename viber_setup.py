"""Getting the access Viber MCP needs, and saying what's still missing.

At server start (server.py, in a background thread) request_access() asks for everything up
front, so the first real request doesn't fail on it: macOS's Accessibility and Screen Recording
prompts, and Google's sign-in page when Drive uploads are set up but nobody is signed in (the
sign-in is remembered in the data folder, like a browser session). status() is the check_setup
tool: what's in place, and in `to_fix`, exactly what the user has to do about the rest.
"""

from __future__ import annotations

import sys

import backends
import viber_config as config
import viber_drive
import viber_update

FIX = {
    "viber": "Open Viber Desktop and sign in. Keep its window open (behind other windows is fine).",
    "accessibility": "System Settings → Privacy & Security → Accessibility: turn on the app Claude "
                     "starts Viber MCP with (uv, or \"Viber MCP\"), then quit and reopen Claude.",
    "screen_recording": "System Settings → Privacy & Security → Screen Recording: turn on the same "
                        "app, then quit and reopen Claude.",
    "drive": "Google Drive isn't set up: pick the Drive key (google_service_account.json) in the "
             "extension's settings or put it in {data}; for uploads, the OAuth client "
             "(google_oauth_client.json) too.",
    "sign_in": "Sign in to Google: ask Claude to run drive_sign_in(write=True), which opens the "
               "sign-in page in the browser.",
}


def _log(message: str) -> None:
    print(f"viber-mcp setup: {message}", file=sys.stderr, flush=True)  # stdout is the MCP connection


def request_access() -> None:
    """At server start: the OS prompts for missing permissions, then Google's sign-in page if
    it's needed. Never raises: the server runs either way and check_setup says what's missing."""
    try:
        missing = [name for name, ok in backends.get_backend().access(ask=True).items() if not ok]
        _log(f"asked the OS for: {', '.join(missing)}" if missing else "OS permissions in place")
    except Exception as exc:  # noqa: BLE001
        _log(f"couldn't check the OS permissions: {exc}")
    _log(f"Google: {viber_drive.sign_in_at_start()}")


def status() -> dict:
    """ready, and what's behind it: Viber running, the OS permissions, Google Drive access
    (read: finding and sending listings; upload: posting new ones), the version, `to_fix`."""
    backend = backends.get_backend()
    viber_running = backend.app_running()
    permissions = backend.access()
    drive = viber_drive.access_status()
    to_fix = [] if viber_running else [FIX["viber"]]
    to_fix += [FIX[name] for name, ok in permissions.items() if not ok]
    if drive["read"] == "not set up":
        to_fix.append(FIX["drive"].format(data=config.DATA_DIR))
    elif "not signed in" in (drive["read"], drive["upload"]):
        to_fix.append(FIX["sign_in"])
    result = {"ready": not to_fix, "viber_running": viber_running, "permissions": permissions,
              "google_drive": drive, "version": viber_update.installed_version() or "git checkout",
              "to_fix": to_fix}
    if drive["upload"] == "not set up":
        result["note"] = ("Posting new listings to Drive isn't set up (no OAuth client); "
                          "everything else works without it.")
    return result
