import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

# Identifiers in guidelines.md / prompts that are parameters, fields or file names, not tools.
NON_TOOL_NAMES = {
    "client_text", "intro_message_first", "dry_run", "include_internal", "low_confidence", "confirmation_dialog_buttons",
    "gc_members.json", "listing_folder_id", "gc_name", "preview", "failed", "verified", "STOP",
    "folder_id", "photo_paths", "folder_name",
}
SNAKE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


def tool_names():
    return {t.name for t in asyncio.run(server.mcp.list_tools())}


def prompt_texts():
    texts = []
    for prompt in asyncio.run(server.mcp.list_prompts()):
        args = {a.name: "x" for a in (prompt.arguments or []) if a.required}
        rendered = asyncio.run(server.mcp.get_prompt(prompt.name, args))
        texts.append("\n".join(m.content.text for m in rendered.messages))
    return texts


def test_instructions_equal_guidelines():
    assert server.mcp.instructions == (ROOT / "guidelines.md").read_text(encoding="utf-8")


def test_guidelines_backticked_names_are_real_tools():
    guidelines = (ROOT / "guidelines.md").read_text(encoding="utf-8")
    mentioned = {n for n in re.findall(r"`([A-Za-z_.]+)`", guidelines) if "_" in n}
    prompts = {p.name for p in asyncio.run(server.mcp.list_prompts())}
    unknown = mentioned - NON_TOOL_NAMES - tool_names() - prompts
    assert not unknown, f"guidelines.md mentions non-existent tools: {sorted(unknown)}"


def test_prompt_texts_only_name_real_tools():
    names = tool_names()
    for text in prompt_texts():
        unknown = set(SNAKE.findall(text)) - NON_TOOL_NAMES - names
        assert not unknown, f"prompt mentions non-existent tools: {sorted(unknown)}"


def test_expected_prompts_exist():
    prompts = {p.name for p in asyncio.run(server.mcp.list_prompts())}
    assert {"inbox_triage", "draft_reply", "unsend", "property_details", "client_gc"} <= prompts


def test_every_tool_has_a_description():
    empty = [t.name for t in asyncio.run(server.mcp.list_tools()) if not (t.description or "").strip()]
    assert not empty, f"tools without docstring: {empty}"
