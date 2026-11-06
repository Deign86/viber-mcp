"""Starting, quitting and restarting Viber (viber_app), with a fake backend whose Viber starts,
freezes, updates and quits on a virtual clock, plus the server wiring: every tool that drives
Viber brings it up first, the Drive-only ones don't."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backends  # noqa: E402
import viber_app  # noqa: E402
from backends import _maclogic as ml  # noqa: E402
from backends import _winlogic as wl  # noqa: E402
from backends.base import looks_like_updater  # noqa: E402

PANE = {"type": "Pane", "class": "SplitView", "name": "", "text": "", "parent": None,
        "rect": [300, 0, 1100, 900]}


class AppBackend:
    """Viber as a process on the fake clock: `launch_app` makes it run after `start_delay` s
    (never, if None) and show its main screen `load` s later; an installer can be running
    until `updater_until`; the window can be frozen until `frozen_until`."""

    def __init__(self, clock, running=True, start_delay=2.0, load=5.0, frozen_until=0.0,
                 updater_until=0.0, installer_restarts=False, quit_ok=True, access=None):
        self.clock = clock
        self.started_at = clock[0] - 100 if running else None
        self.start_delay, self.load = start_delay, load
        self.frozen_until, self.updater_until = frozen_until, updater_until
        self.installer_restarts = installer_restarts  # the installer starts Viber when done
        self.quit_ok, self._access = quit_ok, access or {}
        self.launches, self.quits = [], []

    def now(self):
        return self.clock[0]

    def _up_since(self):
        if self.installer_restarts and self.started_at is None and self.now() >= self.updater_until:
            self.started_at = self.updater_until
        return self.started_at

    def access(self, ask=False):
        return self._access

    def app_running(self):
        since = self._up_since()
        return since is not None and self.now() >= since

    def app_responding(self):
        return self.now() >= self.frozen_until

    def updater_running(self):
        return "ViberSetup.exe" if self.now() < self.updater_until else None

    def elements(self):
        since = self._up_since()
        if since is None or self.now() < since + self.load:
            raise backends.OCRError("Viber window not found.")
        return [PANE]

    def launch_app(self):
        self.launches.append(self.now())
        if self.start_delay is not None and self.started_at is None:
            self.started_at = self.now() + self.start_delay

    def quit_app(self, timeout):
        self.quits.append(timeout)
        if self.quit_ok:
            self.started_at = None
            self.frozen_until = 0.0
        return self.frozen_until > self.now()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(viber_app.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(viber_app.time, "monotonic", lambda: now[0])
    monkeypatch.delenv("VIBER_MCP_AUTO_START", raising=False)
    return now


def _use(monkeypatch, fake):
    monkeypatch.setattr(backends, "_backend", fake)
    return fake


# ---------------------------------------------------------------- automatic (before a tool)

def test_a_running_responsive_viber_is_left_alone(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock))
    assert viber_app.ensure_running() is None
    assert fake.launches == [] and fake.quits == [] and clock[0] == 1000.0  # no waiting either


def test_a_crashed_viber_is_started_and_the_tool_waits_for_its_main_screen(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, running=False))
    assert viber_app.ensure_running() == {"started": True, "reason": "Viber wasn't running"}
    assert fake.launches == [1000.0]
    assert clock[0] >= 1000.0 + 2.0 + 5.0   # running after 2 s, chat list 5 s later


def test_nothing_is_started_with_the_setting_off(monkeypatch, clock):
    monkeypatch.setenv("VIBER_MCP_AUTO_START", "false")
    fake = _use(monkeypatch, AppBackend(clock, running=False))
    assert viber_app.ensure_running() is None
    assert fake.launches == []


def test_a_frozen_viber_is_restarted_after_the_grace(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, frozen_until=10 ** 9))
    assert viber_app.ensure_running() == {"restarted": True,
                                          "reason": "Viber had stopped responding"}
    assert fake.quits == [viber_app.QUIT_TIMEOUT] and len(fake.launches) == 1
    assert fake.launches[0] >= 1000.0 + viber_app.HUNG_GRACE


def test_a_viber_busy_for_a_moment_is_not_restarted(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, frozen_until=1000.0 + 4))
    assert viber_app.ensure_running() is None
    assert fake.quits == [] and fake.launches == []


def test_viber_that_never_shows_its_main_screen_stops_the_tool(monkeypatch, clock):
    # Signed out (or stuck loading): the tool mustn't run against the sign-in screen.
    _use(monkeypatch, AppBackend(clock, running=False, load=10 ** 9))
    with pytest.raises(viber_app.ViberAppError, match="main screen didn't come up"):
        viber_app.ensure_running()


def test_a_launch_that_doesnt_take_is_tried_again(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, running=False, start_delay=None))
    with pytest.raises(viber_app.ViberAppError):
        viber_app.ensure_running()
    assert len(fake.launches) >= 2
    assert fake.launches[1] - fake.launches[0] >= viber_app.RELAUNCH_AFTER


# ---------------------------------------------------------------- updates

def test_viber_isnt_launched_while_its_installer_runs(monkeypatch, clock):
    # The installer quit Viber to replace it and starts it again itself when it's done.
    fake = _use(monkeypatch, AppBackend(clock, running=False, updater_until=1000.0 + 30,
                                        installer_restarts=True))
    out = viber_app.start()
    assert out["ready"] is True and out["started"] is False   # the installer brought it back
    assert out["waited_for_update"] == "ViberSetup.exe" and fake.launches == []


def test_after_an_update_viber_is_launched_if_the_installer_didnt(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, running=False, updater_until=1000.0 + 30))
    out = viber_app.start()
    assert out["ready"] is True and out["started"] is True
    assert fake.launches and fake.launches[0] >= 1000.0 + 30


def test_an_installer_cant_hold_viber_down_for_good(monkeypatch, clock):
    # "Updater" is a guess from a process name: past UPDATER_GRACE Viber is launched anyway.
    fake = _use(monkeypatch, AppBackend(clock, running=False, updater_until=10 ** 9))
    out = viber_app.start(timeout=viber_app.UPDATER_GRACE + 30)
    assert out["ready"] is True
    assert fake.launches[0] >= 1000.0 + viber_app.UPDATER_GRACE


def test_start_says_viber_is_updating_when_it_isnt_back_in_time(monkeypatch, clock):
    _use(monkeypatch, AppBackend(clock, running=False, updater_until=10 ** 9))
    out = viber_app.start()
    assert out["ready"] is False and "being updated (ViberSetup.exe" in out["note"]


def test_viber_isnt_quit_mid_update(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, updater_until=10 ** 9))
    with pytest.raises(viber_app.ViberAppError, match="being updated"):
        viber_app.stop()
    assert fake.quits == []


# ---------------------------------------------------------------- the tools

def test_start_when_viber_is_already_up_launches_nothing(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock))
    assert viber_app.start() == {"running": True, "ready": True, "started": False}
    assert fake.launches == []


def test_start_without_the_os_permissions_doesnt_wait_for_a_screen_it_cant_read(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, running=False,
                                        access={"accessibility": False, "screen_recording": True}))
    out = viber_app.start()
    assert out["ready"] is None and "check_setup" in out["note"] and fake.launches
    assert clock[0] < 1000.0 + 10


def test_stop_quits_and_says_when_it_had_to_force(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock))
    assert viber_app.stop() == {"running": False, "quit": True, "forced": False}
    assert fake.quits == [viber_app.QUIT_TIMEOUT]
    assert viber_app.stop() == {"running": False, "quit": False, "note": "Viber wasn't running."}


def test_stop_reports_a_viber_that_wont_quit(monkeypatch, clock):
    _use(monkeypatch, AppBackend(clock, quit_ok=False))
    with pytest.raises(viber_app.ViberAppError, match="still running"):
        viber_app.stop()


def test_restart_quits_then_waits_for_the_main_screen(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock))
    out = viber_app.restart()
    assert out == {"running": True, "ready": True, "restarted": True, "forced": False}
    assert len(fake.quits) == 1 and len(fake.launches) == 1


def test_restart_starts_a_viber_that_wasnt_running(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, running=False))
    out = viber_app.restart()
    assert out["ready"] is True and out["forced"] is None and fake.quits == []


def test_status_never_starts_viber(monkeypatch, clock):
    fake = _use(monkeypatch, AppBackend(clock, running=False))
    assert viber_app.status() == {"running": False, "responding": False, "main_screen": False,
                                  "updating": None, "auto_start": True}
    assert fake.launches == []


# ---------------------------------------------------------------- server wiring

DRIVE_ONLY = {"list_contacts", "drive_sign_in", "find_listings", "get_listing",
              "view_listing_photos", "add_listing_to_drive", "drive_upload_files",
              "update_listing_doc", "record_availability", "move_listing_to_sold", "drive_search",
              "drive_download", "clear_downloads", "check_setup", "check_for_update",
              "start_viber", "quit_viber", "restart_viber"}


def test_tools_that_drive_viber_bring_it_up_first(monkeypatch):
    import server

    def down():
        raise viber_app.ViberAppError("Viber can't be started")
    monkeypatch.setattr(server.viber_app, "ensure_running", down)
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for name in sorted(tools - DRIVE_ONLY):
        assert getattr(server, name).__wrapped__, name
        with pytest.raises(viber_app.ViberAppError):
            getattr(server, name)(*([None] * _required(server, name)))
    for name in DRIVE_ONLY:
        assert not hasattr(getattr(server, name), "__wrapped__"), name


def _required(server, name):
    import inspect
    params = inspect.signature(getattr(server, name)).parameters.values()
    return sum(p.default is inspect.Parameter.empty for p in params)


def test_wrapped_tools_keep_their_arguments_and_descriptions():
    import server
    by_name = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    send = by_name["send_message"]
    assert set(send.inputSchema["properties"]) == {"to", "text", "dry_run"}
    assert send.inputSchema["required"] == ["to", "text"]
    assert send.description.startswith("Send a Viber message.")
    assert {"start_viber", "quit_viber", "restart_viber"} <= set(by_name)


# ---------------------------------------------------------------- pure helpers

def test_updaters_are_told_from_viber_and_this_server():
    assert looks_like_updater("ViberSetup.exe")
    assert looks_like_updater("Viber Updater")
    assert looks_like_updater("/Applications/Viber.app/Contents/Frameworks/Sparkle.framework/"
                              "Versions/B/Autoupdate")
    assert not looks_like_updater("Viber.exe")
    assert not looks_like_updater("/Applications/Viber.app/Contents/MacOS/Viber")
    assert not looks_like_updater("QtWebEngineProcess.exe")
    assert not looks_like_updater("/Users/me/viber-mcp/.venv/bin/python")
    assert not looks_like_updater("GoogleUpdate.exe")


def test_viber_exe_is_read_from_the_link_handlers_command():
    assert wl.exe_from_command(r'"C:\Users\me\AppData\Local\Viber\Viber.exe" "%1"') \
        == r"C:\Users\me\AppData\Local\Viber\Viber.exe"
    assert wl.exe_from_command(r"C:\Viber\Viber.exe %1") == r"C:\Viber\Viber.exe"
    assert wl.exe_from_command("rundll32 url.dll") is None
    assert wl.exe_from_command("") is None and wl.exe_from_command(None) is None


def test_mac_app_executable_is_read_from_the_bundle(tmp_path):
    import plistlib
    app = tmp_path / "Viber.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    assert ml.app_executable(str(app)) is None                # no Info.plist
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleExecutable": "Viber"}, fh)
    assert ml.app_executable(str(app)) is None                # mid-update: no executable yet
    (app / "Contents" / "MacOS" / "Viber").write_text("")
    assert ml.app_executable(str(app)) == str(app / "Contents" / "MacOS" / "Viber")
    assert ml.app_executable(str(app) + "/") == str(app / "Contents" / "MacOS" / "Viber")
    (app / "Contents" / "Info.plist").write_text("not a plist")   # half-written by an update
    assert ml.app_executable(str(app)) is None


def test_mac_ps_rows_keep_paths_with_spaces():
    text = ("  512   501 /Applications/Viber.app/Contents/MacOS/Viber\n"
            "  900   501 /Applications/Rakuten Viber.app/Contents/MacOS/Viber Helper\n"
            "garbage\n")
    assert ml.parse_ps(text) == [
        (512, 501, "/Applications/Viber.app/Contents/MacOS/Viber"),
        (900, 501, "/Applications/Rakuten Viber.app/Contents/MacOS/Viber Helper")]


def test_mac_helpers_are_this_users_processes_inside_viber_app():
    procs = [(512, 501, "/Applications/Viber.app/Contents/MacOS/Viber"),
             (513, 501, "/Applications/Viber.app/Contents/Frameworks/QtWebEngineProcess"),
             (514, 502, "/Applications/Viber.app/Contents/Frameworks/QtWebEngineProcess"),
             (515, 501, "/Applications/Viber.application/x"),
             (516, 501, "/System/Applications/Notes.app/Contents/MacOS/Notes")]
    assert ml.bundle_processes(procs, "/Applications/Viber.app", 501, skip=512) == [513]
    assert ml.bundle_processes(procs, None, 501) == []


def test_mac_updater_is_found_by_its_whole_path():
    procs = [(512, 501, "/Applications/Viber.app/Contents/MacOS/Viber"),
             (900, 501, "/Applications/Viber.app/Contents/Frameworks/Sparkle.framework/Versions/B/"
                        "Autoupdate")]
    assert ml.updater_process(procs) == "Autoupdate"
    assert ml.updater_process(procs[:1]) is None
