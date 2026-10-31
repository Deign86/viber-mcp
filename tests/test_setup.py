"""Getting the access the server needs (viber_setup, the Google sign-in in viber_drive, the
backends' OS permissions): asked for once at start, reported by check_setup."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import backends  # noqa: E402
import viber_drive  # noqa: E402
import viber_setup  # noqa: E402


class FakeCreds:
    valid = True

    def to_json(self):
        return '{"token": "t"}'


@pytest.fixture
def google(monkeypatch, tmp_path):
    """The OAuth client is here; Google's sign-in page is a fake that counts its openings."""
    client = tmp_path / "google_oauth_client.json"
    client.write_text("{}")
    monkeypatch.setattr(viber_drive, "CLIENT_FILE", client)

    class Flow:
        opened = []

        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            flow = cls()
            flow.scopes = scopes
            return flow

        def run_local_server(self, **kwargs):
            Flow.opened.append((self.scopes, kwargs))
            print("this would corrupt the MCP connection" if kwargs.get("authorization_prompt_message")
                  else "", end="")
            return FakeCreds()

    import google_auth_oauthlib.flow
    monkeypatch.setattr(google_auth_oauthlib.flow, "InstalledAppFlow", Flow)
    return Flow


def test_sign_in_page_prints_nothing_on_the_mcp_connection(google, capsys):
    # The library prints its prompt to stdout, which is the MCP stdio stream.
    viber_drive._browser_sign_in(viber_drive.WRITE_SCOPES, viber_drive.WRITE_TOKEN_FILE)
    (_, kwargs), = google.opened
    assert kwargs["authorization_prompt_message"] is None and kwargs["open_browser"] is True
    assert capsys.readouterr().out == ""
    assert viber_drive.WRITE_TOKEN_FILE.read_text() == '{"token": "t"}'  # remembered


def test_a_sign_in_finished_while_waiting_isnt_asked_again(google, monkeypatch):
    # The start-up sign-in and a tool can both want one: the second finds the first's.
    monkeypatch.setattr(viber_drive, "_saved_creds", lambda scopes, token_file: FakeCreds())
    assert isinstance(viber_drive._browser_sign_in(viber_drive.WRITE_SCOPES,
                                                   viber_drive.WRITE_TOKEN_FILE), FakeCreds)
    assert google.opened == []


def test_reads_use_the_upload_sign_in_so_one_is_enough(google, monkeypatch):
    signed_in = FakeCreds()
    monkeypatch.setattr(viber_drive, "_saved_creds", lambda scopes, token_file:
                        signed_in if token_file == viber_drive.WRITE_TOKEN_FILE else None)
    assert viber_drive._creds() is signed_in
    assert google.opened == []


def test_start_opens_the_sign_in_once_when_nobody_is_signed_in(google):
    assert viber_drive.sign_in_at_start() == "signed in"
    (scopes, _), = google.opened
    assert scopes == viber_drive.WRITE_SCOPES  # the upload account: covers reads too


def test_start_asks_at_most_every_few_hours(google, monkeypatch):
    def closed(**_kwargs):
        raise TimeoutError("closed the page")
    monkeypatch.setattr(google, "run_local_server", lambda self, **kw: closed(**kw))
    assert "not completed" in viber_drive.sign_in_at_start()  # never raises
    assert "asked recently" in viber_drive.sign_in_at_start()


def test_start_doesnt_ask_without_a_client_or_when_signed_in(google, monkeypatch):
    monkeypatch.setattr(viber_drive, "_saved_creds", lambda scopes, token_file: FakeCreds())
    assert viber_drive.sign_in_at_start() == "already signed in"
    monkeypatch.setattr(viber_drive, "CLIENT_FILE", viber_drive.CLIENT_FILE.with_name("gone.json"))
    assert "nothing to sign in to" in viber_drive.sign_in_at_start()
    assert google.opened == []


def test_access_status_says_where_drive_access_comes_from(google, monkeypatch):
    assert viber_drive.access_status() == {"read": "not signed in", "upload": "not signed in"}
    viber_drive.SERVICE_ACCOUNT_FILE.write_text("{}")
    assert viber_drive.access_status() == {"read": "service account", "upload": "not signed in"}
    monkeypatch.setattr(viber_drive, "_saved_creds", lambda scopes, token_file: FakeCreds())
    assert viber_drive.access_status()["upload"] == "signed in"
    viber_drive.SERVICE_ACCOUNT_FILE.unlink()
    monkeypatch.setattr(viber_drive, "CLIENT_FILE", viber_drive.CLIENT_FILE.with_name("gone.json"))
    monkeypatch.setattr(viber_drive, "_saved_creds", lambda scopes, token_file: None)
    assert viber_drive.access_status() == {"read": "not set up", "upload": "not set up"}


class FakeBackend:
    def __init__(self, running=True, permissions=None, broken=False):
        self.running, self.permissions, self.broken, self.asked = running, permissions or {}, broken, []

    def app_running(self):
        return self.running

    def access(self, ask=False):
        if self.broken:
            raise RuntimeError("no AX")
        self.asked.append(ask)
        return dict(self.permissions)


def _drive(monkeypatch, read, upload):
    monkeypatch.setattr(viber_drive, "access_status", lambda: {"read": read, "upload": upload})


def test_check_setup_is_ready_when_everything_is_in_place(monkeypatch):
    monkeypatch.setattr(backends, "_backend", FakeBackend(permissions={"accessibility": True}))
    _drive(monkeypatch, "service account", "signed in")
    result = viber_setup.status()
    assert result["ready"] is True and result["to_fix"] == []


def test_check_setup_says_what_to_fix(monkeypatch):
    fake = FakeBackend(running=False, permissions={"accessibility": False, "screen_recording": True})
    monkeypatch.setattr(backends, "_backend", fake)
    _drive(monkeypatch, "service account", "not signed in")
    result = viber_setup.status()
    assert result["ready"] is False and fake.asked == [False]  # checking opens no prompt
    assert result["to_fix"] == [viber_setup.FIX["viber"], viber_setup.FIX["accessibility"],
                                viber_setup.FIX["sign_in"]]


def test_check_setup_without_drive(monkeypatch):
    monkeypatch.setattr(backends, "_backend", FakeBackend())
    _drive(monkeypatch, "not set up", "not set up")
    result = viber_setup.status()
    assert "Google Drive isn't set up" in result["to_fix"][0] and "note" in result


def test_start_asks_the_os_then_google_and_never_raises(monkeypatch):
    fake = FakeBackend(permissions={"accessibility": False})
    monkeypatch.setattr(backends, "_backend", fake)
    signed = []
    monkeypatch.setattr(viber_drive, "sign_in_at_start", lambda: signed.append(1) or "signed in")
    viber_setup.request_access()
    assert fake.asked == [True] and signed == [1]
    monkeypatch.setattr(backends, "_backend", FakeBackend(broken=True))
    viber_setup.request_access()  # an OS check that fails still goes on to Google
    assert signed == [1, 1]


@pytest.mark.skipif(sys.platform != "darwin", reason="MacBackend needs macOS")
def test_mac_asks_once_for_each_missing_permission(monkeypatch):
    from backends import macos

    asked = []
    monkeypatch.setattr(macos, "AXIsProcessTrusted", lambda: False)
    monkeypatch.setattr(macos, "AXIsProcessTrustedWithOptions", lambda options: asked.append("ax"))
    monkeypatch.setattr(macos.Quartz, "CGPreflightScreenCaptureAccess", lambda: False, raising=False)
    monkeypatch.setattr(macos.Quartz, "CGRequestScreenCaptureAccess", lambda: asked.append("screen"),
                        raising=False)
    mac = macos.MacBackend.__new__(macos.MacBackend)
    mac._trusted, mac._asked_ax, mac._asked_screen = False, False, False
    assert mac.access() == {"accessibility": False, "screen_recording": False} and asked == []
    mac.access(ask=True)
    mac.access(ask=True)
    assert asked == ["ax", "screen"]  # macOS shows each prompt once per run
