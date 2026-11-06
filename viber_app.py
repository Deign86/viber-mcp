"""Starting, quitting and restarting Viber Desktop — on request, and on its own.

Viber crashes, freezes and updates itself, and every tool that drives it needs it up. So before
such a tool runs (server.py's `viber_tool`), `ensure_running()`:
- starts Viber when it isn't running (it crashed, or was quit), and
- restarts it when its window has stopped responding for HUNG_GRACE seconds (frozen),
unless the "Start Viber automatically" setting (VIBER_MCP_AUTO_START) is off.

`start` / `stop` / `restart` are the start_viber, quit_viber and restart_viber tools. A restart
is also how a Viber update that's waiting for one gets applied.

Updates: Viber's installer quits Viber, replaces it and starts it again itself. While one runs
(`backend.updater_running()`), Viber isn't launched (the installer would find it running) and
isn't quit, for up to UPDATER_GRACE seconds — a process name is only a guess, so it can't hold
Viber down for good.

"Up" means Viber's main screen: its chat list and chat pane are in the UI tree, not a splash
screen, an update page or the sign-in screen.

The platform part (finding and launching the app, quitting it, telling a frozen window) is the
backend's: launch_app, quit_app, app_responding, updater_running.
"""

from __future__ import annotations

import sys
import threading
import time

import backends
import viber_config as config
import viber_ocr

START_TIMEOUT = 60.0    # a cold start that loads a big chat list takes ~10-30 s
QUIT_TIMEOUT = 10.0     # a polite quit before it's forced
HUNG_GRACE = 10.0       # a window that hasn't answered for this long (after the OS's own ~5 s)
UPDATER_GRACE = 90.0    # longest an installer can keep Viber from being launched or quit
RELAUNCH_AFTER = 20.0   # launched, but neither Viber nor an installer is running: launch again
_POLL = 1.0

_LOCK = threading.Lock()  # one start/quit at a time: a tool's check and a tool call can race

NOT_UP = ("its main screen didn't come up within {s:.0f} s: it may still be loading or "
          "updating, or be signed out. Take a screenshot to see which; if it's stuck, "
          "restart_viber.")
UPDATING = ("Viber is being updated ({updater} is running) and isn't back yet. Check again in a "
            "minute (start_viber).")
NO_ACCESS = ("Viber was started, but the OS permissions to read it are missing, so it can't be "
             "checked: call check_setup.")


class ViberAppError(Exception):
    """Viber couldn't be started or quit."""


def auto_start_on() -> bool:
    return config.env("VIBER_MCP_AUTO_START", "true").lower() not in {"false", "0", "no", "off"}


def _backend():
    return backends.get_backend()


def _log(message: str) -> None:
    print(f"viber-mcp: {message}", file=sys.stderr, flush=True)  # stdout is the MCP connection


def _main_screen(b) -> bool:
    try:
        return viber_ocr._chat_pane(b.elements()) is not None
    except Exception:  # noqa: BLE001 — a window still starting up fails in all sorts of ways
        return False


def _bring_up(b, timeout: float) -> dict:
    """Launch Viber unless it's running or an installer is at work, and wait up to `timeout` s
    for its main screen. An update can make Viber exit and its installer start it again, so this
    keeps watching: it launches (again) only when neither is running."""
    can_read = all(b.access().values())
    start = time.monotonic()
    deadline = start + timeout
    launched_at = None
    launches = 0
    waited_for = None
    while True:
        now = time.monotonic()
        updater = b.updater_running() if now - start < UPDATER_GRACE else None
        if updater:
            waited_for = waited_for or updater
            launched_at = None  # the installer starts Viber itself; launch only after it
        elif b.app_running():
            if not can_read:
                return {"up": None, "launches": launches, "waited_for_update": waited_for}
            if _main_screen(b):
                return {"up": True, "launches": launches, "waited_for_update": waited_for}
        elif launched_at is None or now - launched_at >= RELAUNCH_AFTER:
            b.launch_app()
            launched_at = now
            launches += 1
            _log("started Viber" if launches == 1 else f"started Viber again (try {launches})")
        if time.monotonic() >= deadline:
            return {"up": False, "launches": launches, "waited_for_update": waited_for}
        time.sleep(_POLL)


def _quit(b) -> bool:
    """Quit Viber (it's running); True if it had to be forced. Raises if it's still running."""
    updater = _wait_updater(b)
    if updater:
        raise ViberAppError(f"Viber is being updated ({updater} is running). Not quitting it "
                            "mid-update: try again in a few minutes.")
    forced = b.quit_app(QUIT_TIMEOUT)
    if b.app_running():
        raise ViberAppError("Viber is still running after it was quit and then ended by force.")
    _log("quit Viber" + (" (forced)" if forced else ""))
    return forced


def _wait_updater(b) -> str | None:
    """Wait up to UPDATER_GRACE s for a running installer to finish; its name if it hasn't."""
    deadline = time.monotonic() + UPDATER_GRACE
    while True:
        updater = b.updater_running()
        if not updater or time.monotonic() >= deadline:
            return updater
        time.sleep(_POLL)


def _result(up: dict, timeout: float, **extra) -> dict:
    result = {"running": True, "ready": up["up"], **extra}
    if up["waited_for_update"]:
        result["waited_for_update"] = up["waited_for_update"]
    if up["up"] is None:
        result["note"] = NO_ACCESS
    elif not up["up"]:
        result["note"] = _not_up(up, timeout, "Viber is running, but ")
    return result


def _not_up(up: dict, timeout: float, lead: str) -> str:
    if up["waited_for_update"]:
        return UPDATING.format(updater=up["waited_for_update"])
    return lead + NOT_UP.format(s=timeout)


def status() -> dict:
    """Is Viber running, answering, showing its main screen, being updated? Never starts it."""
    b = _backend()
    running = b.app_running()
    return {"running": running,
            "responding": running and b.app_responding(),
            "main_screen": running and _main_screen(b),
            "updating": b.updater_running(),
            "auto_start": auto_start_on()}


def start(timeout: float = START_TIMEOUT) -> dict:
    """Start Viber if it isn't running and wait for its main screen. `started` says whether it
    had to be launched; `ready` whether its main screen is up (None: it can't be read)."""
    with _LOCK:
        b = _backend()
        up = _bring_up(b, timeout)
        return _result(up, timeout, started=up["launches"] > 0)


def stop() -> dict:
    """Quit Viber: politely, then by force after QUIT_TIMEOUT s. `forced` says which."""
    with _LOCK:
        b = _backend()
        if not b.app_running():
            return {"running": False, "quit": False, "note": "Viber wasn't running."}
        return {"running": False, "quit": True, "forced": _quit(b)}


def restart(timeout: float = START_TIMEOUT) -> dict:
    """Quit Viber (if it's running) and start it again, waiting for its main screen."""
    with _LOCK:
        b = _backend()
        forced = _quit(b) if b.app_running() else None
        up = _bring_up(b, timeout)
        return _result(up, timeout, restarted=True, forced=forced)


def ensure_running(timeout: float = START_TIMEOUT) -> dict | None:
    """Before a tool drives Viber: start it when it isn't running, restart it when it's frozen.
    None when Viber was fine (or the setting is off: the tool then says Viber isn't running);
    else what was done. Raises ViberAppError when Viber can't be brought back, so the tool
    doesn't run against a Viber that isn't there."""
    b = _backend()
    if not auto_start_on() or (b.app_running() and b.app_responding()):
        return None  # the usual case: two cheap checks
    with _LOCK:
        if not b.app_running():
            action = {"started": True, "reason": "Viber wasn't running"}
        else:
            if _responds_within(b, HUNG_GRACE):
                return None
            _log(f"Viber hasn't responded for {HUNG_GRACE:.0f} s: restarting it")
            _quit(b)
            action = {"restarted": True, "reason": "Viber had stopped responding"}
        up = _bring_up(b, timeout)
        if up["up"] is False:
            raise ViberAppError(_not_up(up, timeout, f"{action['reason']}, so it was started, but "))
        return action


def _responds_within(b, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if b.app_responding():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL)
