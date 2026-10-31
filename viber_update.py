"""Update notice for the Claude Desktop extension.

Every merge to main is published as a GitHub Release (tests.yml, `release` job) with the bundle
attached as viber-mcp.mcpb. Claude Desktop doesn't update an extension installed from a file, so
the server compares its own version (the manifest.json next to it, stamped by
extension/build.py --release) with the latest release, and tells Claude to pass the link on.
A git checkout has no manifest.json next to server.py (it's updated with git pull): no check.
"""

from __future__ import annotations

import http.client
import json
import re
import ssl
import time
import urllib.error
import urllib.request

import viber_config as config

REPO = "Deign86/viber-mcp"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/viber-mcp.mcpb"
CACHE_FILE = config.DATA_DIR / "update_check.json"
# The check runs while the server starts: at most once per CACHE_HOURS (a failed one too), and
# never longer than TIMEOUT, so being offline costs one short wait, not one per start.
CACHE_HOURS = 6
TIMEOUT = 2.0
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def installed_version() -> str | None:
    """The running extension's version, or None when this isn't the extension."""
    try:
        data = json.loads((config.HERE / "manifest.json").read_text(encoding="utf-8"))
        version = str(data.get("version", ""))
    except (OSError, ValueError, AttributeError):
        return None
    return version if _VERSION.match(version) else None


def _get(request: urllib.request.Request, context: ssl.SSLContext) -> dict:
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
        return json.load(response)


def _fetch() -> dict:
    """GitHub's latest (non-draft, non-prerelease) release. TLS is checked against the system's
    certificates (they include an office proxy's), then certifi's (the Google client's bundle)."""
    request = urllib.request.Request(LATEST_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "viber-mcp-update-check"})
    try:
        return _get(request, ssl.create_default_context())
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        try:
            import certifi
        except ImportError:
            raise exc from None
        return _get(request, ssl.create_default_context(cafile=certifi.where()))


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
    """installed / latest / update_available, plus the download link when there's an update."""
    installed = installed_version()
    if installed is None:
        return {"installed": None, "latest": None, "update_available": False,
                "note": "Not running as the Claude Desktop extension (a git checkout): "
                        "update with git pull."}
    latest = latest_version(force)
    result = {"installed": installed, "latest": latest,
              "update_available": latest is not None and _key(latest) > _key(installed)}
    if result["update_available"]:
        result["download"] = DOWNLOAD_URL
        result["how"] = ("Download the file, double-click it and confirm in Claude Desktop. "
                         "contacts.json, the Drive key etc. live outside the extension and stay.")
    return result


def notice() -> str:
    """Appended to the server instructions when a newer release is out; "" otherwise."""
    if installed_version() is None:
        return ""
    info = status()
    if not info["update_available"]:
        return ""
    return (
        "\n\n## Update available\n\n"
        f"Viber MCP {info['latest']} is out; this computer runs {info['installed']}. Tell the "
        "user once in this conversation, in one line at the end of your first reply: download "
        f"{DOWNLOAD_URL}, double-click it and confirm in Claude Desktop, then start a new chat.\n"
    )
