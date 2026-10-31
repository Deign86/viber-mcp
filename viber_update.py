"""Keeps the Claude Desktop extension up to date by itself.

Every merge to main is published as a GitHub Release (tests.yml, `release` job) with the bundle
attached as viber-mcp.mcpb. Claude Desktop doesn't update an extension installed from a file, so
the server does: when Claude Desktop starts it, server.py calls update_and_restart() before
loading anything else. That compares the version in the manifest.json next to it (stamped by
extension/build.py --release) with the latest release, installs a newer one over its own folder,
and runs the new server in its place on the same connection, so Claude talks to the new version
from its first message. When that can't be done (offline, the folder isn't writable, the
"Update automatically" setting is off), the instructions tell Claude to pass the download link on.
A git checkout has no manifest.json next to server.py (it's updated with git pull): no check.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import re
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import viber_config as config

REPO = "Deign86/viber-mcp"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/viber-mcp.mcpb"
# The asset of one release, by tag: "latest" could move between the check and the download.
RELEASE_ASSET = f"https://github.com/{REPO}/releases/download/v{{version}}/viber-mcp.mcpb"
CACHE_FILE = config.DATA_DIR / "update_check.json"
# The check runs while the server starts: at most once per CACHE_HOURS (a failed one too), and
# never longer than TIMEOUT, so being offline costs one short wait, not one per start.
CACHE_HOURS = 1
TIMEOUT = 2.0
DOWNLOAD_TIMEOUT = 15.0
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
# A bundle without these isn't a server release.
REQUIRED = {"manifest.json", "server.py", "pyproject.toml", "uv.lock", "guidelines.md"}
# Set for the server an update starts, so it doesn't check again.
RESTARTED = "VIBER_MCP_UPDATED"
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class UpdateError(Exception):
    """The downloaded release can't be installed."""


def _log(message: str) -> None:
    print(f"viber-mcp update: {message}", file=sys.stderr, flush=True)  # stdout is the MCP connection


def auto_update_on() -> bool:
    return config.env("VIBER_MCP_AUTO_UPDATE", "true").lower() not in {"false", "0", "no", "off"}


def installed_version() -> str | None:
    """The running extension's version, or None when this isn't the extension."""
    try:
        data = json.loads((config.HERE / "manifest.json").read_text(encoding="utf-8"))
        version = str(data.get("version", ""))
    except (OSError, ValueError, AttributeError):
        return None
    return version if _VERSION.match(version) else None


def _get(request: urllib.request.Request, context: ssl.SSLContext, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read(MAX_BUNDLE_BYTES + 1)


def _open(url: str, timeout: float, accept: str) -> bytes:
    """GET `url`. TLS is checked against the system's certificates (they include an office
    proxy's), then certifi's (the Google client's bundle)."""
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "viber-mcp-update"})
    try:
        return _get(request, ssl.create_default_context(), timeout)
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        try:
            import certifi
        except ImportError:
            raise exc from None
        return _get(request, ssl.create_default_context(cafile=certifi.where()), timeout)


def _fetch() -> dict:
    """GitHub's latest (non-draft, non-prerelease) release."""
    return json.loads(_open(LATEST_API, TIMEOUT, "application/vnd.github+json"))


def _download(version: str) -> bytes:
    data = _open(RELEASE_ASSET.format(version=version), DOWNLOAD_TIMEOUT, "application/octet-stream")
    if len(data) > MAX_BUNDLE_BYTES:
        raise UpdateError(f"the {version} download is over {MAX_BUNDLE_BYTES // 2**20} MB")
    return data


def latest_version(force: bool = False) -> str | None:
    """The newest release's version (cached for CACHE_HOURS unless `force`); None when it can't
    be found out: offline, rate-limited, or no release yet."""
    now = time.time()
    if not force:
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if 0 <= now - float(cached["checked"]) < CACHE_HOURS * 3600:
                latest = cached.get("latest")
                return latest if isinstance(latest, str) and _VERSION.match(latest) else None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    try:  # offline, timeout, TLS, HTTP 403/404, bad JSON: no answer, and the server starts anyway
        release = _fetch()
    except (OSError, ValueError, http.client.HTTPException):
        release = {}
    tag = str(release.get("tag_name", "")).removeprefix("v") if isinstance(release, dict) else ""
    latest = tag if _VERSION.match(tag) else None
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"checked": now, "latest": latest}), encoding="utf-8")
    except OSError:
        pass
    return latest


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def status(force: bool = False) -> dict:
    """installed / latest / update_available / auto_update, and what to do about an update."""
    installed = installed_version()
    if installed is None:
        return {"installed": None, "latest": None, "update_available": False,
                "note": "Not running as the Claude Desktop extension (a git checkout): "
                        "update with git pull."}
    latest = latest_version(force)
    result = {"installed": installed, "latest": latest, "auto_update": auto_update_on(),
              "update_available": latest is not None and _key(latest) > _key(installed)}
    if result["update_available"]:
        if result["auto_update"]:
            result["how"] = ("It installs itself the next time Claude Desktop starts the "
                             "extension: quit Claude Desktop and open it again.")
        else:
            result["download"] = DOWNLOAD_URL
            result["how"] = ("Download the file, double-click it and confirm in Claude Desktop. "
                             "contacts.json, the Drive key etc. live outside the extension and stay.")
    return result


def notice() -> str:
    """Appended to the server instructions when this is still behind the latest release (the
    update at start didn't happen); "" otherwise."""
    if installed_version() is None:
        return ""
    info = status()
    if not info["update_available"]:
        return ""
    why = ("couldn't update itself" if info["auto_update"]
           else "has automatic updates turned off")
    return (
        "\n\n## Update available\n\n"
        f"Viber MCP {info['latest']} is out; this computer runs {info['installed']} and {why}. "
        "Tell the user once in this conversation, in one line at the end of your first reply: "
        f"download {DOWNLOAD_URL}, double-click it and confirm in Claude Desktop, then start a "
        "new chat.\n"
    )


def _files(bundle: zipfile.ZipFile) -> list[str]:
    return [name for name in bundle.namelist() if not name.endswith("/")]


def _bundle(data: bytes, version: str) -> zipfile.ZipFile:
    """The downloaded release, checked: a viber-mcp bundle of `version` with the files the
    server needs, and no path that would land outside the extension's folder."""
    try:
        bundle = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(bundle.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise UpdateError(f"the download isn't a viber-mcp bundle ({exc})") from None
    if (not isinstance(manifest, dict) or manifest.get("name") != "viber-mcp"
            or manifest.get("version") != version):
        raise UpdateError(f"the download isn't viber-mcp {version}")
    for name in _files(bundle):
        parts = PurePosixPath(name).parts
        if "\\" in name or ":" in name or name.startswith("/") or ".." in parts:
            raise UpdateError(f"the bundle has an unsafe path: {name!r}")
    missing = REQUIRED - set(_files(bundle))
    if missing:
        raise UpdateError(f"the bundle lacks {sorted(missing)}")
    return bundle


def install(bundle: zipfile.ZipFile, target: Path) -> None:
    """Write the release over the extension in `target`: each file to a temporary name, then
    renamed over the old one, manifest.json last, so an install cut short still reads as the old
    version and is redone at the next start. Then the .py files the release dropped are deleted
    (only those: uv's .venv and the caches stay)."""
    names = _files(bundle)
    for name in sorted(names, key=lambda n: n == "manifest.json"):
        dst = target.joinpath(*PurePosixPath(name).parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".updating")
        tmp.write_bytes(bundle.read(name))
        os.replace(tmp, dst)
    shipped = set(names)
    for old in [*target.glob("*.py"), *target.glob("backends/*.py")]:
        if old.relative_to(target).as_posix() not in shipped:
            old.unlink()


def _install_latest(version: str) -> bool:
    try:
        install(_bundle(_download(version), version), config.HERE)
    except (OSError, ValueError, http.client.HTTPException, UpdateError) as exc:
        _log(f"couldn't install {version}: {exc}")
        return False
    _log(f"installed {version}")
    return True


def _restart() -> None:
    """Run the freshly installed server in this one's place, on the same stdin/stdout (Claude
    Desktop's connection), through uv so a new dependency is installed first; exit with its
    status. Not os.execv: on Windows that starts a new process and ends this one, and Claude
    Desktop would see the server exit."""
    uv = os.environ.get("UV")  # set by `uv run`
    command = ([uv, "run", "--frozen", "--directory", str(config.HERE), "server.py"] if uv
               else [sys.executable, str(config.HERE / "server.py")])
    child = subprocess.Popen(command, env={**os.environ, RESTARTED: "1"})
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda signum, _frame: child.send_signal(signum))
        except (OSError, ValueError):
            pass
    sys.exit(child.wait())


def update_and_restart() -> None:
    """At the extension's start (before anything else loads): install a newer release and hand
    over to it. Returns, and this version runs, when there's nothing newer or it can't update."""
    if os.environ.get(RESTARTED) or not auto_update_on() or installed_version() is None:
        return
    info = status()
    if info["update_available"] and _install_latest(info["latest"]):
        _restart()
