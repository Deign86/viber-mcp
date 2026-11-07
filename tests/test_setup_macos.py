"""setup_macos.sh's launcher app: nothing is written into the bundle after it's signed, and the
signature names the bundle id, so a rebuild keeps the permissions.

Regression (2026-11-07): the script wrote Contents/Resources/command after `codesign` (and again on
every re-run), which voided the ad-hoc signature; macOS then didn't match the app to its
Accessibility grant and Claude Desktop asked for it on every start although "Viber MCP" was allowed.

Runs the real script with fake uv/cc/codesign/xattr on PATH and a temporary HOME and app path. The
fake codesign signs by hashing every file in the bundle, so any write after signing fails verify,
and keeps the designated requirement it was given (`-d -r-` prints it); without one it's the hash,
like a default ad-hoc signature.
"""

import os
import shutil
import time
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not Path("/usr/bin/python3").exists(),
    reason="runs setup_macos.sh under bash")

FAKE_CODESIGN = r"""#!/usr/bin/env python3
import hashlib, os, sys
def digest(app):
    h = hashlib.sha256()
    for d, _, files in sorted(os.walk(app)):
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, app).encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()
app = sys.argv[-1]
sig = os.environ["FAKE_SIG"]
req = sig + ".req"
with open(os.environ["FAKE_LOG"], "a") as log:
    if "-d" in sys.argv:
        if not os.path.exists(req):
            sys.exit(1)
        print("Executable=" + app + "\n" + open(req).read())
        sys.exit(0)
    if "--verify" in sys.argv:
        log.write("verify\n")
        ok = os.path.exists(sig) and open(sig).read() == digest(app)
        sys.exit(0 if ok else 1)
    log.write("sign\n")
    open(sig, "w").write(digest(app))
    given = sys.argv[sys.argv.index("--requirements") + 1] if "--requirements" in sys.argv else None
    open(req, "w").write(given[1:] if given else 'designated => cdhash H"' + digest(app)[:40] + '"')
"""

FAKE_TCCUTIL = """#!/bin/sh
echo "tccutil $*" >> "$FAKE_LOG"
"""

FAKE_CC = """#!/bin/sh
while [ "$1" != "-o" ]; do shift; done
printf 'binary' > "$2" && chmod +x "$2"
"""


@pytest.fixture
def setup(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"uv": "#!/bin/sh\nexit 0\n", "xattr": "#!/bin/sh\nexit 0\n",
                       "cc": FAKE_CC, "codesign": FAKE_CODESIGN,
                       "tccutil": FAKE_TCCUTIL}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    app = tmp_path / "Viber MCP.app"
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "codesign.log"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home), "VIBER_MCP_APP": str(app),
           "FAKE_SIG": str(tmp_path / "signature"), "FAKE_LOG": str(log)}

    def run(repo=ROOT):
        log.write_text("")
        out = subprocess.run(["bash", str(Path(repo) / "setup_macos.sh")], env=env,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return out.stdout, log.read_text().splitlines()

    def verified():
        return subprocess.run([str(bin_dir / "codesign"), "--verify", str(app)],
                              env=env).returncode == 0

    def snapshot():
        return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in app.rglob("*") if p.is_file()}

    run.sig = tmp_path / "signature"
    return run, verified, snapshot, app


def resets(calls):
    return [c for c in calls if c.startswith("tccutil reset")]


def test_the_command_file_is_written_before_signing(setup):
    run, verified, _, app = setup
    out, calls = run()
    assert "built and signed" in out and [c for c in calls if c in ("sign", "verify")] == ["sign"]
    assert verified()  # nothing written after codesign
    assert (app / "Contents/Resources/command").read_text().splitlines()[1:] == [
        "--directory", str(ROOT), "run", "server.py"]


def test_a_rerun_leaves_the_signed_bundle_alone(setup):
    run, verified, snapshot, _ = setup
    run()
    before = snapshot()
    time.sleep(0.01)  # a rewrite with the same bytes would show in the mtime
    out, calls = run()
    assert "up to date" in out and "sign" not in calls
    assert snapshot() == before and verified()


def test_an_app_broken_by_the_old_script_is_signed_again(setup):
    # The old script's install: command written after codesign.
    run, verified, _, app = setup
    run()
    cmd = app / "Contents/Resources/command"
    cmd.write_text(cmd.read_text() + "\n")
    assert not verified()
    out, calls = run()
    assert "signature was broken" in out and "sign" in calls and verified()


def test_a_moved_checkout_rebuilds_and_signs_last(setup, tmp_path):
    run, verified, _, app = setup
    run()
    moved = tmp_path / "moved"
    (moved / "macos").mkdir(parents=True)
    for f in ("setup_macos.sh", "macos/launcher.c", "macos/AppIcon.icns"):
        shutil.copy(ROOT / f, moved / f)
    out, calls = run(moved)
    assert "checkout or uv moved" in out and "sign" in calls and verified()
    assert str(moved) in (app / "Contents/Resources/command").read_text()


def test_a_finder_custom_icon_in_the_bundle_is_replaced_by_a_signed_rebuild(setup):
    # The old script set a Finder custom icon (Icon\r in the bundle root) on apps built before
    # AppIcon.icns: unsealed contents in the bundle root, so the signature no longer verified.
    run, verified, _, app = setup
    run()
    (app / "Icon\r").write_bytes(b"")
    assert not verified()
    out, calls = run()
    assert "signature was broken" in out and verified() and not (app / "Icon\r").exists()


REQ = 'designated => identifier "com.alphapremier.viber-mcp"'


def test_the_app_is_signed_for_its_bundle_id_not_one_build(setup):
    # Regression (2026-11-07): ad-hoc signed by hash, every rebuild was a new app to macOS, and
    # Screen Recording kept asking although the old "Viber MCP" entry was switched on.
    run, _, _, app = setup
    run()
    assert Path(str(run.sig) + ".req").read_text() == REQ
    assert "<string>com.alphapremier.viber-mcp</string>" in (app / "Contents/Info.plist").read_text()


def test_a_rebuild_keeps_the_permissions(setup, tmp_path):
    run, verified, _, _ = setup
    run()
    moved = tmp_path / "moved"
    (moved / "macos").mkdir(parents=True)
    for f in ("setup_macos.sh", "macos/launcher.c", "macos/AppIcon.icns"):
        shutil.copy(ROOT / f, moved / f)
    out, calls = run(moved)
    assert "sign" in calls and verified()
    assert "permissions kept" in out and not resets(calls)


def test_an_app_signed_the_old_way_is_re_signed_and_its_stale_grants_cleared(setup):
    run, verified, _, app = setup
    run()
    # What the previous setup_macos.sh made: the default ad-hoc requirement (the hash).
    Path(str(run.sig) + ".req").write_text('designated => cdhash H"0123"')
    out, calls = run()
    assert "signed the old way" in out and "sign" in calls and verified()
    assert resets(calls) == ["tccutil reset Accessibility com.alphapremier.viber-mcp",
                             "tccutil reset ScreenCapture com.alphapremier.viber-mcp"]
    assert Path(str(run.sig) + ".req").read_text() == REQ
    out, calls = run()
    assert "up to date" in out and "sign" not in calls
