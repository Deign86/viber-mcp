"""Client group chats: from a staff template in the templates chat (e.g. APG CLIENTS) to a
ready client GC.

Flow (create_client_gc): read the template message → derive the GC name → create the group with
the staff members (gc_members.json, in order) and then the client's Viber number → post the
client's template (client fields only) → post the listing (details above the dashed line +
photos) → "Hello @client" → back in the templates chat, quote-reply "DONE-<GC NAME>" to the
template. dry_run (default) returns the plan without touching anything.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import viber_drive
import viber_gc_logic as logic
import viber_send
import viber_ui

CONFIG = Path(__file__).with_name("gc_members.json")


class GCError(Exception):
    """Raised when a client GC can't be planned or created safely."""


def _config() -> dict:
    if not CONFIG.exists():
        raise GCError("gc_members.json is missing — copy gc_members.example.json and fill in the "
                      "staff members to add to client GCs.")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _template(n: int) -> str:
    msg = viber_ui._message(n)
    if msg["kind"] != "text" or not msg["text"]:
        raise GCError(f"Message n={n} isn't a text template.")
    return msg["text"]


def plan_client_gc(template_n: int, gc_name: str = "", listing_folder_id: str = "") -> dict:
    """Work out everything create_client_gc would do, without changing anything."""
    cfg = _config()
    text = _template(template_n)
    fields = logic.parse_template(text)
    naming = logic.gc_name(fields)
    name = gc_name.strip() or naming["gc_name"]
    problems = [] if gc_name.strip() else list(naming["uncertain"])
    try:
        client = logic.viber_e164(fields.get("viber") or "")
    except ValueError as exc:
        client = ""
        problems.append(f"Client Viber number: {exc}")

    listing = None
    candidates = []
    if listing_folder_id:
        listing = {"folder_id": listing_folder_id}
    elif fields.get("listing"):
        query = fields["listing"].splitlines()[0]
        found = viber_drive.find_listings(query)
        # Sold properties are never offered to clients.
        candidates = [c for c in found["listings"] if "SOLD" not in c["category"].upper()][:8]
        exact = [c for c in candidates if logic_norm(c["name"]) == logic_norm(query)]
        if len(exact) == 1 or len(candidates) == 1:
            listing = (exact or candidates)[0]
        else:
            problems.append(f"Listing '{query}' matched {len(candidates)} active folders — "
                            "pass listing_folder_id.")
    else:
        problems.append("The template has no 'Listings:' line — pass listing_folder_id, or "
                        "listing_folder_id='none' to skip the listing.")
    if listing and listing.get("folder_id") == "none":
        listing = None
        problems = [p for p in problems if "Listings:" not in p]

    done = logic.done_text(name)
    visible = viber_ui._messages(viber_ui._backend().elements())
    if any(logic_norm(m["text"]) == logic_norm(done) for m in visible):
        problems.append(f"'{done}' is already in this chat — this GC was made already.")

    members = [m for m in cfg["members"] if logic_norm(m) != logic_norm(cfg.get("self", ""))]
    return {
        "gc_name": name,
        "done_reply": logic.done_text(name),
        "members_in_order": members + [f"client {client}"],
        "client_number": client,
        "client_message": logic.client_message(text),
        "listing": listing,
        "listing_candidates": candidates if not listing else [],
        "templates_chat": cfg.get("templates_chat", ""),
        "template_text": text,
        "problems": problems,
        "ready": not problems,
    }


def logic_norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def create_client_gc(template_n: int, gc_name: str = "", listing_folder_id: str = "",
                     dry_run: bool = True, members: list[str] | None = None,
                     done_chat: str = "") -> dict:
    """Plan (dry_run) or run the whole client-GC flow for template message n of the OPEN chat."""
    plan = plan_client_gc(template_n, gc_name, listing_folder_id)
    if dry_run or not plan["ready"]:
        plan.pop("template_text")
        plan["note"] = ("Nothing was done. Show this plan to the staff member; after a yes, call "
                        "again with dry_run=false." if plan["ready"] else
                        "Fix the problems listed (pass gc_name / listing_folder_id), then retry.")
        return plan

    cfg = _config()
    steps = {}
    created = viber_ui.create_group(plan["gc_name"], members if members is not None else cfg["members"],
                                    plan["client_number"], self_name=cfg.get("self", ""))
    if not created["created"]:
        raise GCError("The group didn't appear after 'Start chat' — check Viber before retrying "
                      "(it may exist already).")
    steps["group"] = created
    steps["template"] = viber_send.reply(plan["client_message"])
    if plan["listing"]:
        steps["listing"] = viber_drive.send_listing(plan["listing"]["folder_id"])
    steps["mention"] = viber_ui.mention(created["client_display_name"])

    # Back to the templates chat: quote-reply DONE to the original template.
    chat = done_chat or plan["templates_chat"]
    viber_send.search_and_open(chat)
    time.sleep(1.0)
    target = _find_message(plan["template_text"])
    if target is None:
        steps["done_reply"] = {"sent": False, "note": f"Template not found in '{chat}' — reply "
                               f"'{plan['done_reply']}' to it manually."}
    else:
        steps["done_reply"] = viber_ui.reply_to_message(target["n"], plan["done_reply"], send=True)
    return {"done": True, "gc_name": plan["gc_name"], "steps": steps}


# ------------------------------------------------------------ autonomous processing


def _is_template(text: str) -> bool:
    f = logic.parse_template(text)
    return bool(f.get("name") and f.get("viber") and (f.get("property_type") or f.get("sqm")))


def _gc_exists(name: str) -> bool:
    """Does a chat whose title starts like `name` already exist? (Viber search, then cleared.)"""
    b = viber_ui._backend()
    b.click_search()
    b.delete()
    b.type_text(name[:18])
    time.sleep(1.8)
    head = viber_ui._norm(name)[:12]
    try:
        obs = viber_ui._left_column(viber_ui._ocr_px())
        return any(viber_ui._norm(o["text"]).startswith(head) for o in obs[2:])  # skip search box
    finally:
        b.click_search()
        b.delete()
        b.press_key("escape")
        time.sleep(0.6)


def _find_message(text: str, screens: int = 6):
    """Locate the visible message with this exact text: jump to the newest, then scroll up."""
    b = viber_ui._backend()
    want = logic_norm(text)
    for _ in range(3):
        b.scroll("chat", -60)
        time.sleep(0.8)
    for _ in range(screens + 1):
        msgs = viber_ui._messages(b.elements())
        hit = next((m for m in reversed(msgs) if logic_norm(m["text"]) == want), None)
        if hit:
            return hit
        b.scroll("chat", 10)
        time.sleep(1.0)
    return None


def _collect_templates_chat(screens: int) -> list[str]:
    """Texts of the templates chat's recent messages, oldest first (scrolls up, then back)."""
    b = viber_ui._backend()
    seen: list[str] = []
    # Start from the newest message, whatever the chat was scrolled to before.
    for _ in range(3):
        b.scroll("chat", -60)
        time.sleep(0.8)
    for _ in range(screens):
        for m in reversed(viber_ui._messages(b.elements())):  # newest → oldest on screen
            if m["text"] and m["text"] not in seen:
                seen.append(m["text"])
        b.scroll("chat", 10)
        time.sleep(1.0)
    b.scroll("chat", -50)
    time.sleep(1.0)
    return list(reversed(seen))


def process_client_templates(limit: int = 5, screens: int = 4, preview: bool = True) -> dict:
    """GCs for every template in the templates chat that has no DONE reply yet. preview=True
    (default) only lists what would be created; preview=False creates them. Templates that
    can't be handled safely are skipped and reported."""
    cfg = _config()
    chat = cfg.get("templates_chat") or "APG CLIENTS"
    viber_send.search_and_open(chat)
    time.sleep(1.0)
    texts = _collect_templates_chat(screens)
    is_done = [t.strip().upper().startswith("DONE-") for t in texts]
    done_lines = {logic_norm(t) for t, d in zip(texts, is_done) if d}

    results, skipped = [], []
    for i, text in enumerate(texts):
        if not _is_template(text):
            continue
        if len(results) >= limit:
            skipped.append({"template": text[:80], "reason": f"limit of {limit} GCs per run"})
            continue
        name = logic.gc_name(logic.parse_template(text))
        # Staff don't always name GCs by the template's Name (e.g. Leah → DONE-JNT-WH-...), so a
        # template counts as handled if its DONE matches OR any DONE was posted after it. Worst
        # case a template is skipped — never a duplicate GC for a client who already has one.
        if logic_norm(logic.done_text(name["gc_name"])) in done_lines or any(is_done[i + 1:]):
            continue
        if name["uncertain"]:
            skipped.append({"gc_name": name["gc_name"], "reason": "; ".join(name["uncertain"])})
            continue
        if _gc_exists(name["gc_name"]):
            skipped.append({"gc_name": name["gc_name"],
                            "reason": "a chat with this name already exists (no DONE reply yet)"})
            continue
        if preview:
            results.append({"would_create": name["gc_name"], "template": text})
            continue
        # Re-open the templates chat and locate this template by its exact text.
        viber_send.search_and_open(chat)
        time.sleep(1.0)
        target = _find_message(text, screens + 2)
        if target is None:
            skipped.append({"gc_name": name["gc_name"], "reason": "template scrolled out of reach"})
            continue
        try:
            plan = plan_client_gc(target["n"])
            if not plan["ready"]:
                skipped.append({"gc_name": plan["gc_name"], "reason": "; ".join(plan["problems"])})
                continue
            results.append(create_client_gc(target["n"], dry_run=False))
        except Exception as exc:  # report and carry on with the next template
            skipped.append({"gc_name": name["gc_name"], "reason": f"failed: {exc}"})
    return {"would_create" if preview else "created": results, "skipped": skipped,
            "note": ("Nothing was created. Show this list to the staff member and run again with "
                     "preview=false after a yes." if preview else
                     "Skipped templates need a staff decision (name, listing, or number).")}
