"""Build the Claude Desktop extension: dist/viber-mcp-<version>.mcpb.

    uv run extension/build.py            (or any Python 3.11+: it needs no packages)

Install the result by double-clicking it (Claude Desktop opens it), or in Claude Desktop:
Settings → Extensions → Advanced settings → Install Extension.

Only the files in BUNDLE go in. A working copy also holds contacts.json (real numbers),
gc_members.json and google_oauth_client.json, which must never ship; that's also why the manifest
lives in extension/ and not at the repo root, where `mcpb pack` would zip the whole folder.

The bundle is staged in dist/viber-mcp/ first, so the official CLI can check it:
    npx @anthropic-ai/mcpb validate dist/viber-mcp/manifest.json
The manifest's `tools` list is filled in here from server.py's @mcp.tool() functions.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import tomllib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# What the server needs at run time, relative to the repo root (globs don't recurse).
# Claude Desktop installs the dependencies from pyproject.toml + uv.lock with uv.
BUNDLE = [
    "server.py",
    "viber_*.py",
    "backends/*.py",
    "guidelines.md",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "contacts.example.json",
    "gc_members.example.json",
    "LICENSE",
    "README.md",
]


def bundle_files(root: Path = ROOT) -> dict[str, Path]:
    """{path in the bundle: source file} for everything BUNDLE matches under `root`."""
    files: dict[str, Path] = {}
    for pattern in BUNDLE:
        matches = [p for p in sorted(root.glob(pattern)) if p.is_file()]
        if not matches:
            raise SystemExit(f"build: nothing matches {pattern!r} in {root}")
        for path in matches:
            files[path.relative_to(root).as_posix()] = path
    return files


def declared(source: str, kind: str = "tool") -> list[dict]:
    """[{name, description}] for each function decorated with @mcp.<kind>() in `source`;
    the description is the docstring's first paragraph on one line."""
    found = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (isinstance(target, ast.Attribute) and target.attr == kind
                    and isinstance(target.value, ast.Name) and target.value.id == "mcp"):
                doc = ast.get_docstring(node) or ""
                found.append({"name": node.name, "description": " ".join(doc.split("\n\n")[0].split())})
    return found


def manifest(root: Path = ROOT) -> dict:
    """extension/manifest.json with the tool list filled in; its version must be pyproject's."""
    data = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if data["version"] != project["version"]:
        raise SystemExit(f"build: extension/manifest.json version {data['version']} != "
                         f"pyproject.toml version {project['version']}; bump both together.")
    data["tools"] = declared((root / data["server"]["entry_point"]).read_text(encoding="utf-8"))
    return data


def build(out_dir: Path = ROOT / "dist") -> Path:
    """Stage the bundle in <out_dir>/viber-mcp/ and zip it to <out_dir>/viber-mcp-<version>.mcpb."""
    info = manifest()
    stage = out_dir / "viber-mcp"
    if stage.exists():
        shutil.rmtree(stage)
    for rel, src in bundle_files().items():
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    shutil.copy2(HERE / info["icon"], stage / info["icon"])
    (stage / "manifest.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")

    target = out_dir / f"{info['name']}-{info['version']}.mcpb"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(stage).as_posix())
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist/)")
    target = build(parser.parse_args().out.resolve())
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    tools = len(json.loads((target.parent / "viber-mcp" / "manifest.json").read_text(encoding="utf-8"))["tools"])
    print(f"{target}  ({target.stat().st_size // 1024} KB, {len(names)} files, {tools} tools)")


if __name__ == "__main__":
    main()
