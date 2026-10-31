"""The Claude Desktop extension (extension/): manifest, bundle contents, and the settings it
passes to the server as VIBER_MCP_* variables (viber_config)."""

import asyncio
import importlib.util
import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import viber_config  # noqa: E402

_spec = importlib.util.spec_from_file_location("extension_build", ROOT / "extension" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

MANIFEST = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
SECRETS = {"contacts.json", "gc_members.json", "google_oauth_client.json",
           "google_service_account.json", "layout.windows.json"}


def test_manifest_version_is_pyprojects():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert MANIFEST["version"] == project["version"]


def test_manifest_runs_the_server_with_uv():
    server_cfg = MANIFEST["server"]
    assert server_cfg["type"] == "uv"
    assert (ROOT / server_cfg["entry_point"]).is_file()
    args = server_cfg["mcp_config"]["args"]
    assert server_cfg["mcp_config"]["command"] == "uv"
    assert args[0] == "run" and args[-1] == server_cfg["entry_point"]
    assert args[args.index("--directory") + 1] == "${__dirname}"
    assert (ROOT / "extension" / MANIFEST["icon"]).is_file()


def test_every_setting_reaches_the_server():
    env = MANIFEST["server"]["mcp_config"]["env"]
    used = {m for v in env.values() for m in re.findall(r"\$\{user_config\.([^}]+)\}", v)}
    assert used == set(MANIFEST["user_config"]), "a setting is never passed, or an env var names a missing one"
    source = "".join(p.read_text(encoding="utf-8") for p in ROOT.glob("viber_*.py"))
    for name in env:
        assert f'"{name}"' in source, f"{name} is passed by the extension but no module reads it"


def test_bundle_has_the_server_and_never_the_private_files(tmp_path):
    # A working copy holds real numbers and keys next to server.py; only BUNDLE may ship.
    for pattern in build.BUNDLE:
        path = tmp_path / pattern.replace("*", "x")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    for name in SECRETS | {"client.py", "CLAUDE.md", "tests/test_x.py", "notes.txt", "shot.png"}:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("secret")
    names = set(build.bundle_files(tmp_path))
    assert {"server.py", "viber_x.py", "backends/x.py", "guidelines.md", "uv.lock"} <= names
    assert not names & SECRETS
    assert names <= {p.replace("*", "x") for p in build.BUNDLE}


def test_built_extension_lists_the_servers_tools(tmp_path):
    target = build.build(tmp_path)
    assert target.name == f"viber-mcp-{MANIFEST['version']}.mcpb"
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        built = json.loads(zf.read("manifest.json"))
    assert {"manifest.json", "icon.png", "server.py", "viber_config.py", "backends/windows.py",
            "backends/macos.py", "guidelines.md", "pyproject.toml", "uv.lock"} <= names
    assert not {n.rsplit("/", 1)[-1] for n in names} & SECRETS
    assert not any(n.startswith(("tests/", ".venv/")) or "__pycache__" in n for n in names)
    served = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {t["name"] for t in built["tools"]} == served
    assert all(t["description"] for t in built["tools"])


def test_unset_extension_setting_counts_as_unset(monkeypatch):
    # Claude Desktop passes an optional setting the user left empty as the literal placeholder.
    monkeypatch.setenv("VIBER_MCP_TEST", "${user_config.contacts_file}")
    assert viber_config.env("VIBER_MCP_TEST", "dflt") == "dflt"
    monkeypatch.setenv("VIBER_MCP_TEST", "  ")
    assert viber_config.env("VIBER_MCP_TEST", "dflt") == "dflt"
    monkeypatch.delenv("VIBER_MCP_TEST")
    assert viber_config.env("VIBER_MCP_TEST", "dflt") == "dflt"
    monkeypatch.setenv("VIBER_MCP_TEST", " APR LISTING 2 ")
    assert viber_config.env("VIBER_MCP_TEST", "dflt") == "APR LISTING 2"


def test_user_file_prefers_the_setting_then_code_folder_then_data_folder(monkeypatch, tmp_path):
    code, data = tmp_path / "code", tmp_path / "data"
    code.mkdir()
    data.mkdir()
    monkeypatch.setattr(viber_config, "HERE", code)
    monkeypatch.setattr(viber_config, "DATA_DIR", data)
    monkeypatch.delenv("VIBER_MCP_TEST", raising=False)

    assert viber_config.user_file("VIBER_MCP_TEST", "contacts.json") == code / "contacts.json"
    (data / "contacts.json").write_text("{}")
    assert viber_config.user_file("VIBER_MCP_TEST", "contacts.json") == data / "contacts.json"
    (code / "contacts.json").write_text("{}")
    assert viber_config.user_file("VIBER_MCP_TEST", "contacts.json") == code / "contacts.json"
    monkeypatch.setenv("VIBER_MCP_TEST", "${user_config.contacts_file}")
    assert viber_config.user_file("VIBER_MCP_TEST", "contacts.json") == code / "contacts.json"
    picked = tmp_path / "picked" / "my contacts.json"
    monkeypatch.setenv("VIBER_MCP_TEST", str(picked))
    assert viber_config.user_file("VIBER_MCP_TEST", "contacts.json") == picked


def _git_repo(path, commits):
    import subprocess

    path.mkdir()
    (path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n')

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       cwd=path, check=True, capture_output=True)
    git("init", "-q")
    for n in range(commits):
        (path / "f.txt").write_text(str(n))
        git("add", "-A")
        git("commit", "-q", "-m", f"c{n}")
    return path


def test_release_version_counts_commits(tmp_path):
    # Every merge to main adds a commit, so each release is newer than the last.
    repo = _git_repo(tmp_path / "repo", 3)
    assert build.release_version(repo) == "0.1.3"


def test_release_version_refuses_a_shallow_clone(tmp_path):
    import subprocess

    repo = _git_repo(tmp_path / "repo", 3)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)],
                   check=True, capture_output=True)
    with pytest.raises(SystemExit, match="shallow"):
        build.release_version(shallow)


def test_release_build_stamps_its_version(tmp_path):
    target = build.build(tmp_path, version="0.1.123")
    assert target.name == "viber-mcp-0.1.123.mcpb"
    with zipfile.ZipFile(target) as zf:
        assert json.loads(zf.read("manifest.json"))["version"] == "0.1.123"
        assert "viber_update.py" in zf.namelist()
