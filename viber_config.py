"""Where the server finds its settings and per-user files.

Settings are `VIBER_MCP_*` environment variables: set by hand, or by Claude Desktop from the
extension's settings page (extension/manifest.json maps each setting to one variable).
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Per-user data that outlives the code: Drive tokens and key, the download cache, and (searched
# after the code folder) contacts.json, gc_members.json and the OAuth client. The extension's own
# folder is replaced on every update, so this is where its files can live without a setting.
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "viber-mcp"


def env(name: str, default: str = "") -> str:
    """The variable's value, or `default` when it's unset or blank.

    Claude Desktop only substitutes extension settings that have a value: one the user left
    empty arrives as the literal "${user_config.<key>}", which counts as unset too."""
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("${"):
        return default
    return value


def user_file(env_name: str, filename: str) -> Path:
    """A per-user file: the path in `env_name` when set, else `filename` next to server.py (a git
    checkout) or in DATA_DIR, whichever exists first; next to server.py when neither does."""
    configured = env(env_name)
    if configured:
        return Path(configured).expanduser()
    for folder in (HERE, DATA_DIR):
        if (folder / filename).exists():
            return folder / filename
    return HERE / filename
