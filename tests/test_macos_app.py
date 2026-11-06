"""MacBackend's start/quit/restart pieces with pyobjc faked (macOS only: the module imports
Quartz/AppKit). Each mirrors a Windows backend test in test_windows_backend.py: where Viber is
installed, the launch, the polite-then-forced quit, "not responding", and updates in progress."""

import os
import plistlib
import signal
import sys
import types
from pathlib import Path

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS backend", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import macos  # noqa: E402
from backends.base import OCRError  # noqa: E402


def _backend():
    b = macos.MacBackend.__new__(macos.MacBackend)
    b._pid = None
    b._target_el = b._focus_el = "stale"
    b._app_path = None
    return b


class URL:
    def __init__(self, path):
        self._path = path

    def path(self):
        return self._path


def _bundle(tmp_path, name="Viber.app", executable=True):
    app = tmp_path / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleExecutable": "Viber", "CFBundleIdentifier": "com.viber.osx"}, fh)
    if executable:
        (app / "Contents" / "MacOS" / "Viber").write_text("")
    return str(app)


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(macos.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(macos.time, "time", lambda: now[0])
    return now


# --- where Viber.app is (Windows: the viber:// handler in the registry) ---------------------

def test_viber_app_is_launch_services_then_the_last_seen_then_applications(monkeypatch, tmp_path):
    b = _backend()
    ls_app = _bundle(tmp_path / "ls")
    monkeypatch.setattr(b, "_ls_app_path", lambda: ls_app)
    assert b._viber_app_path() == ls_app

    monkeypatch.setattr(b, "_ls_app_path", lambda: None)
    b._app_path = _bundle(tmp_path / "seen")
    assert b._viber_app_path() == b._app_path                 # where Viber was seen running
    b._app_path = None
    default = _bundle(tmp_path / "apps")
    monkeypatch.setattr(macos, "_APP_FOLDERS", (default,))
    assert b._viber_app_path() == default                     # the usual install folder
    monkeypatch.setattr(macos, "_APP_FOLDERS", (str(tmp_path / "none" / "Viber.app"),))
    with pytest.raises(OCRError, match="Viber.app wasn't found"):
        b._viber_app_path()


def test_viber_pid_is_this_users_viber_and_remembers_its_bundle(monkeypatch):
    b = _backend()
    calls = []

    def run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(stdout="512\n", returncode=0)
    monkeypatch.setattr(macos.subprocess, "run", run)
    app = types.SimpleNamespace(bundleURL=lambda: URL("/Applications/Viber.app"))
    monkeypatch.setattr(macos, "NSRunningApplication", types.SimpleNamespace(
        runningApplicationWithProcessIdentifier_=lambda pid: app))
    assert b._viber_pid() == 512
    assert calls == [["pgrep", "-x", "-U", str(os.getuid()), "Viber"]]
    assert b._app_path == "/Applications/Viber.app"


# --- the launch (Windows: a detached process out of the server's job) -----------------------

class Config:
    @classmethod
    def configuration(cls):
        return cls()

    def setActivates_(self, value):
        self.activates = value

    def setAddsToRecentItems_(self, value):
        self.recent = value

    def setPromptsUserIfNeeded_(self, value):
        self.prompts = value


class Workspace:
    def __init__(self, error=None):
        self.error, self.opened = error, []

    def openApplicationAtURL_configuration_completionHandler_(self, url, config, handler):
        self.opened.append((str(url.path()), config))
        handler(None, self.error)


def _launch(monkeypatch, tmp_path, error=None):
    b = _backend()
    app = _bundle(tmp_path)
    workspace, runs, threads = Workspace(error), [], []
    monkeypatch.setattr(b, "_viber_app_path", lambda: app)
    monkeypatch.setattr(b, "foreground", lambda: 77)
    monkeypatch.setattr(macos, "NSWorkspace", types.SimpleNamespace(sharedWorkspace=lambda: workspace))
    monkeypatch.setattr(macos, "AppKit", types.SimpleNamespace(NSWorkspaceOpenConfiguration=Config))

    def run(args, **kw):
        runs.append(args)
        return types.SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(macos.subprocess, "run", run)

    class Thread:
        def __init__(self, target, args=(), name="", daemon=False):
            threads.append((args, name, daemon))

        def start(self):
            pass
    monkeypatch.setattr(macos.threading, "Thread", Thread)
    b.launch_app()
    return b, app, workspace, runs, threads


def test_launch_app_opens_viber_through_launch_services_without_activating_it(monkeypatch, tmp_path):
    b, app, workspace, runs, threads = _launch(monkeypatch, tmp_path)
    (path, config), = workspace.opened
    assert path == app and config.activates is False and config.prompts is False
    assert runs == []                                          # no `open` needed
    assert threads == [((77, 20.0), "viber-fg-guard", True)]   # the user's app gets the front back
    assert b._target_el is None and b._focus_el is None


def test_launch_app_falls_back_to_open_g_when_launch_services_fails(monkeypatch, tmp_path):
    error = types.SimpleNamespace(localizedDescription=lambda: "The application can't be opened.")
    _, app, _, runs, _ = _launch(monkeypatch, tmp_path, error=error)
    assert runs == [["open", "-g", app]]


# --- quitting (Windows: WM_CLOSE, then ending Viber.exe and its helpers) --------------------

def _quit(monkeypatch, clock, quits_on_request=True):
    b = _backend()
    alive = {500, 501, 502, 503}
    killed = []
    app = types.SimpleNamespace(terminated=False, forced=False)
    app.bundleURL = lambda: URL("/Applications/Viber.app")

    def terminate():
        app.terminated = True
        if quits_on_request:
            alive.discard(500)

    def force():
        app.forced = True
        alive.discard(500)
    app.terminate, app.forceTerminate = terminate, force
    me = os.getuid()
    processes = [(500, me, "/Applications/Viber.app/Contents/MacOS/Viber"),
                 (501, me, "/Applications/Viber.app/Contents/Frameworks/QtWebEngineCore.framework/"
                           "Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess"),
                 (502, me + 1, "/Applications/Viber.app/Contents/MacOS/Viber"),  # another user's
                 (503, me, "/System/Applications/Notes.app/Contents/MacOS/Notes")]
    monkeypatch.setattr(b, "_viber_pid", lambda: 500)
    monkeypatch.setattr(b, "_processes", lambda: processes)
    monkeypatch.setattr(macos, "NSRunningApplication", types.SimpleNamespace(
        runningApplicationWithProcessIdentifier_=lambda pid: app))
    monkeypatch.setattr(macos, "_alive", lambda pid: pid in alive)

    def kill(pid, sig):
        killed.append((pid, sig))
        alive.discard(pid)
    monkeypatch.setattr(macos.os, "kill", kill)
    forced = b.quit_app(10.0)
    return b, app, forced, killed, alive


def test_quit_app_asks_viber_to_quit_then_ends_its_leftover_helpers(monkeypatch, clock):
    b, app, forced, killed, alive = _quit(monkeypatch, clock)
    assert app.terminated and not app.forced and forced is False
    assert killed == [(501, signal.SIGKILL)]                   # only this user's Viber helper
    assert alive == {502, 503} and b._pid is None and b._target_el is None


def test_quit_app_forces_a_viber_that_doesnt_quit(monkeypatch, clock):
    _, app, forced, killed, _ = _quit(monkeypatch, clock, quits_on_request=False)
    assert app.terminated and app.forced and forced is True
    assert (500, signal.SIGKILL) in killed and (501, signal.SIGKILL) in killed
    assert clock[0] >= 10.0                                    # it got its time to quit


# --- "not responding" (Windows: IsHungAppWindow) ---------------------------------------------

def test_app_responding_is_an_ax_request_that_doesnt_time_out(monkeypatch):
    b = _backend()
    b._pid = 500
    timeouts, answer = [], [macos._AX_CANNOT_COMPLETE]
    monkeypatch.setattr(macos, "_alive", lambda pid: True)
    monkeypatch.setattr(macos, "AXIsProcessTrusted", lambda: True)
    monkeypatch.setattr(macos, "AXUIElementCreateApplication", lambda pid: ("app", pid))
    monkeypatch.setattr(macos, "_set_messaging_timeout", lambda el, s: timeouts.append(s))
    monkeypatch.setattr(macos, "AXUIElementCopyAttributeValue",
                        lambda el, attr, out: (answer[0], None))
    assert b.app_responding() is False
    assert timeouts == [macos._RESPONDING_TIMEOUT]             # bounded, not macOS's 6 s default
    answer[0] = 0
    assert b.app_responding() is True
    monkeypatch.setattr(macos, "AXIsProcessTrusted", lambda: False)
    answer[0] = macos._AX_CANNOT_COMPLETE
    assert b.app_responding() is True                          # can't ask: can't tell


# --- updates in progress (Windows: Viber.exe being replaced, or an installer) ----------------

def test_updater_running_sees_viber_app_being_replaced_or_its_updater(monkeypatch, tmp_path):
    b = _backend()
    app = _bundle(tmp_path, executable=False)                  # the executable is mid-swap
    processes = []
    monkeypatch.setattr(b, "_ls_app_path", lambda: app)
    monkeypatch.setattr(b, "_processes", lambda: processes)
    assert b.updater_running() == "Viber.app (being replaced)"
    (Path(app) / "Contents" / "MacOS" / "Viber").write_text("")
    assert b.updater_running() is None
    processes.append((900, os.getuid(), app + "/Contents/Frameworks/Sparkle.framework/Versions/B/"
                                             "Autoupdate"))
    assert b.updater_running() == "Autoupdate"
