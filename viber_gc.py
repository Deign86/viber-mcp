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
    fields = logic.infer_fields(text, logic.parse_template(text))
    naming = logic.gc_name(fields)
    client_display = ""
    problems = []
    try:
        client = logic.viber_e164(fields.get("viber") or "")
    except ValueError as exc:
        client = ""
        problems.append(f"Client Viber number: {exc}")
    intro = ""
    if client:
        client_display = viber_ui.number_on_viber(client) or ""
        if not client_display:
            # Numbers only show up in the group picker once we've chatted with them, so message
            # the client 1:1 first (if the number has no Viber account, that send fails loudly).
            intro = logic.intro_message(fields, cfg.get("intro_message", ""))

    listing = None
    candidates = []
    listing_note = ""
    if listing_folder_id:
        listing = {"folder_id": listing_folder_id}
    elif fields.get("listing"):
        query = fields["listing"].splitlines()[0].lstrip("*").strip()
        pick = logic.choose_listing(query, viber_drive.find_listings(query)["listings"])
        listing, candidates = pick["listing"], pick["candidates"][:8]
        if pick["problem"]:
            problems.append(pick["problem"])
    else:
        # Not a blocker: the GC is still made (template + mention); a listing can be passed.
        listing_note = ("The template names no listing — the GC will be created without one. "
                        "Pass listing_folder_id (from find_listings) to include a listing.")
    if listing and listing.get("folder_id") == "none":
        listing = None

    # Only the client's name and Viber number are required: a missing type, size or place is
    # taken from the listing (its folder name "City, sqm Area" and category).
    if listing and naming["uncertain"]:
        if "name" not in listing:
            try:
                meta = viber_drive._drive().files().get(
                    fileId=listing["folder_id"], fields="name,parents",
                    supportsAllDrives=True).execute(num_retries=viber_drive.RETRIES)
                listing["name"] = meta["name"]
                listing.setdefault("category", viber_drive._drive().files().get(
                    fileId=viber_drive._drive().files().get(
                        fileId=meta["parents"][0], fields="parents", supportsAllDrives=True
                    ).execute(num_retries=viber_drive.RETRIES)["parents"][0],
                    fields="name", supportsAllDrives=True).execute(
                        num_retries=viber_drive.RETRIES)["name"])
            except Exception:  # noqa: BLE001 — naming help only; never block on it
                pass
        naming = logic.fill_from_listing(naming, listing.get("name", ""), listing.get("category", ""))
    name = gc_name.strip() or naming["gc_name"]
    if not gc_name.strip():
        problems += list(naming["uncertain"])

    done = logic.done_text(name)
    visible = viber_ui._messages(viber_ui._backend().elements())
    if any(logic_norm(m["text"]) == logic_norm(done) for m in visible):
        problems.append(f"'{done}' is already in this chat — this GC was made already.")
    exists, moved = gc_exists(name)
    if exists:
        problems.append(f"GC ALREADY EXISTS: a chat named '{name}' is already in Viber — not "
                        "creating a duplicate.")
    if moved and cfg.get("templates_chat"):
        viber_send.search_and_open(cfg["templates_chat"])  # put the templates chat back

    members = [m for m in cfg["members"] if logic_norm(m) != logic_norm(cfg.get("self", ""))]
    return {
        "gc_name": name,
        "done_reply": logic.done_text(name),
        "members_in_order": [f"client {client}"] + members,
        "client_number": client,
        "client_viber_name": client_display,
        # Set when the number isn't in the group picker yet: sent 1:1 to the client first.
        "intro_message_first": intro,
        "client_message": logic.client_message(text),
        "listing": listing,
        "listing_candidates": candidates if not listing else [],
        "listing_note": listing_note,
        "templates_chat": cfg.get("templates_chat", ""),
        "template_text": text,
        "problems": problems,
        "ready": not problems,
    }


logic_norm = logic.norm


def create_client_gc(template_n: int, gc_name: str = "", listing_folder_id: str = "",
                     dry_run: bool = False, members: list[str] | None = None,
                     done_chat: str = "") -> dict:
    """Plan (dry_run) or run the whole client-GC flow for template message n of the OPEN chat."""
    plan = plan_client_gc(template_n, gc_name, listing_folder_id)
    exists_only = plan["problems"] and all(p.startswith("GC ALREADY EXISTS") for p in plan["problems"])
    if not dry_run and exists_only:
        return {"done": False, "existing_client": True, "gc_name": plan["gc_name"],
                "notice": notify_existing(plan["template_text"], plan["gc_name"])}
    if not dry_run and not plan["ready"]:
        # Fail loudly: nothing is created, and the caller must not loop on it.
        raise GCError("GC NOT CREATED — " + " | ".join(plan["problems"]) +
                      " — Report this to the staff member and stop; do not retry.")
    if dry_run:
        plan.pop("template_text")
        plan["note"] = ("Preview only — nothing was done." if plan["ready"] else
                        "NOT READY — fix the problems listed (gc_name / listing_folder_id / the "
                        "template's number) before running; do not retry as-is.")
        return plan

    cfg = _config()
    steps = {}
    if plan["intro_message_first"]:
        try:
            steps["intro"] = viber_send.send_message(plan["client_number"], plan["intro_message_first"])
        except viber_send.SendError as exc:
            raise GCError(f"GC NOT CREATED — couldn't message {plan['client_number']} first: {exc} "
                          "— Report this to the staff member and stop; do not retry.") from exc
        if not viber_ui.number_on_viber(plan["client_number"]):
            raise GCError(f"GC NOT CREATED — {plan['client_number']} still isn't in Viber's group "
                          "picker after the intro message. Report this and stop; do not retry.")
    # The picker shows unsaved numbers as "Unknown"; the mention list uses their Viber name, which
    # their 1:1 chat's "<Name> +63… is not in your contact list" banner shows.
    client_name = plan["client_viber_name"]
    if viber_ui.is_placeholder_name(client_name):
        client_name = client_name_from_chat(plan["client_number"])
    created = viber_ui.create_group(plan["gc_name"], members if members is not None else cfg["members"],
                                    plan["client_number"], self_name=cfg.get("self", ""))
    if created.get("existing_group") is not None:
        raise GCError(f"EXISTING CLIENT — GC NOT CREATED: {plan['client_number']} already shares a "
                      f"group with the staff ('{created['existing_group']}'). Send the new listing "
                      "there, rename that GC to the new inquiry, then quote-reply 'DONE-<NEW NAME> — "
                      "existing client po, GC already created.' to the template. Do not retry.")
    if not created["created"]:
        raise GCError("The group didn't appear after 'Start chat' — check Viber before retrying "
                      "(it may exist already).")
    if created.get("privacy_blocked") is not None:
        who = created["privacy_blocked"] or plan["client_number"]
        raise GCError(f"CLIENT NOT ADDED — GC '{plan['gc_name']}' was created with the staff only: "
                      f"{who} ({plan['client_number']}) couldn't be added because of their Viber "
                      "privacy settings. Nothing was posted in the GC and DONE wasn't replied. Ask the "
                      "client in their 1:1 chat to allow group invites (More → Settings → Privacy → "
                      "Groups and Communities), then add them to the GC and post the template, "
                      "listing and mention. Do not retry (that would make a second GC).")
    steps["group"] = created
    steps["template"] = viber_send.reply(plan["client_message"])
    if plan["listing"]:
        steps["listing"] = viber_drive.send_listing(plan["listing"]["folder_id"])
    if viber_ui.is_placeholder_name(client_name):
        client_name = created["client_display_name"]
    greeting, after = "Hello", ""
    if viber_ui.is_placeholder_name(client_name):
        # Brand-new numbers are "Unknown" in the picker and the mention list too; staff mention
        # them as @Unknown. send_with_mention refuses if more than one participant is "Unknown".
        client_name, greeting = "Unknown", "Hello po,"
        after = "! Please check the listing above po." if plan["listing"] else "!"
    try:
        steps["mention"] = viber_ui.mention(client_name, greeting, number=plan["client_number"],
                                            after=after)
    except viber_ui.UIError as exc:
        raise GCError(f"GC '{plan['gc_name']}' was created and the template/listing posted, but the "
                      f"client couldn't be mentioned as @{client_name} ({exc}), so DONE wasn't "
                      "replied. Report this and stop; do not retry.") from exc

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
    steps["cleanup"] = viber_drive.prune_downloads()  # only photos older than a few days
    out = {"done": True, "gc_name": plan["gc_name"], "steps": steps}
    if steps["mention"].get("verified_number") is False:
        out["WARNING"] = (f"The @{client_name} mention didn't open {plan['client_number']}'s "
                          "profile — it may have mentioned someone else. Check the GC and tell the "
                          "staff member.")
    return out


def client_name_from_chat(number: str) -> str:
    """Open the number's 1:1 chat and read the Viber name from its "not in your contact list"
    banner. "" when it can't be read."""
    viber_send.open_chat(number)
    for _ in range(3):
        name = viber_ui.name_from_contact_banner([o["text"] for o in viber_ui._ocr_px()], number)
        if name:
            return name
        time.sleep(1.0)
    return ""


def notify_existing(template_text: str, gc_name: str) -> dict:
    """The template's GC already exists: quote-reply to the template, @mentioning the employee
    who posted it, starting with DONE-<GC NAME> so later scans treat it as handled."""
    target = _find_message(template_text)
    if target is None:
        return {"notified": False, "note": "template not found to reply to"}
    done = logic.done_text(gc_name)
    employee = viber_ui.message_sender(target["n"])
    tail = " existing client po, GC already created."
    if employee:
        try:
            sent = viber_ui.send_with_mention(f"{done} — ", employee, tail, quote_n=target["n"])
            return {"notified": True, "employee": employee, "text": sent["text"]}
        except viber_ui.UIError:
            pass  # employee not in the mention list: fall back to a plain quote-reply
    target = _find_message(template_text) or target
    sent = viber_ui.reply_to_message(target["n"], f"{done} — existing client po, GC already created.")
    return {"notified": True, "employee": employee or None, "text": sent.get("text")}


# ------------------------------------------------------------ autonomous processing


def gc_exists(name: str) -> tuple[bool, bool]:
    """Does a chat named like `name` already exist? Returns (exists, opened_a_chat).

    Searches Viber; when the highlighted result title can't be read, opens the top result and
    compares the chat header (reliable). opened_a_chat tells the caller the open chat changed."""
    b = viber_ui._backend()
    b.click_search()
    b.delete()
    b.type_text(name[:30])
    time.sleep(2.0)
    obs = viber_ui._left_column(viber_ui._ocr_px())
    texts = [o["text"] for o in obs if o["y"] > obs[0]["H"] * 0.1] if obs else []
    verdict = logic.search_says_gc_exists(texts, name)
    if verdict == "unknown":
        header = next((o for o in obs if o["text"].strip().lower() in
                       ("conversations", "groups", "chats")), None)
        if header is not None:
            b.click_at(viber_ui._ocr_center(header)[0], header["y"] + header["h"] * 3.2)
            time.sleep(1.5)
            exists = viber_send._header_matches(name)
            b.click_search()
            b.delete()
            b.press_key("escape")
            time.sleep(0.6)
            return exists, True
        verdict = "no"
    b.click_search()
    b.delete()
    b.press_key("escape")
    time.sleep(0.6)
    return verdict == "yes", False


def _find_message(text: str, screens: int = 6):
    """Locate the visible message with this exact text: jump to the newest, then scroll up."""
    b = viber_ui._backend()
    want = logic_norm(text)
    viber_ui.jump_to_latest()
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
    viber_ui.jump_to_latest()
    for _ in range(screens):
        for m in reversed(viber_ui._messages(b.elements())):  # newest → oldest on screen
            if m["text"] and m["text"] not in seen:
                seen.append(m["text"])
        b.scroll("chat", 10)
        time.sleep(1.0)
    viber_ui.jump_to_latest()
    return list(reversed(seen))


def process_client_templates(limit: int = 5, screens: int = 4, preview: bool = False) -> dict:
    """GCs for every template in the templates chat that has no DONE reply yet. preview=True
    only lists what would be created; preview=False (default) creates them. Templates that
    can't be handled safely are skipped and reported."""
    cfg = _config()
    chat = cfg.get("templates_chat") or "APG CLIENTS"
    viber_send.search_and_open(chat)
    time.sleep(1.0)
    texts = _collect_templates_chat(screens)
    results, skipped, failed, existing = [], [], [], []
    for i in logic.pending_templates(texts):
        text = texts[i]
        if len(results) >= limit:
            skipped.append({"template": text[:80], "reason": f"limit of {limit} GCs per run"})
            continue
        name = logic.gc_name(logic.parse_template(text))
        if name["uncertain"]:
            skipped.append({"gc_name": name["gc_name"], "reason": "; ".join(name["uncertain"])})
            continue
        # Re-open the templates chat and locate this template by its exact text.
        viber_send.search_and_open(chat)
        time.sleep(1.0)
        target = _find_message(text, screens + 2)
        if target is None:
            skipped.append({"gc_name": name["gc_name"], "reason": "template scrolled out of reach"})
            continue
        try:
            plan = plan_client_gc(target["n"])  # includes the Viber lookup of the client number
        except Exception as exc:
            failed.append({"gc_name": name["gc_name"], "error": f"planning failed: {exc}"})
            continue
        if not plan["ready"]:
            if any(p.startswith("GC ALREADY EXISTS") for p in plan["problems"]) and not preview:
                existing.append({"gc_name": plan["gc_name"],
                                 "notice": notify_existing(text, plan["gc_name"])})
                continue
            failed.append({"gc_name": plan["gc_name"], "error": " | ".join(plan["problems"])})
            continue
        if preview:
            results.append({"would_create": plan["gc_name"], "client": plan["client_number"],
                            "client_viber_name": plan["client_viber_name"],
                            "listing": (plan["listing"] or {}).get("name"), "template": text})
            continue
        try:
            results.append(create_client_gc(target["n"], dry_run=False))
        except Exception as exc:  # one template failing must not stop or loop the others
            failed.append({"gc_name": name["gc_name"], "error": str(exc)})
    out = {"would_create" if preview else "created": results, "skipped": skipped,
           "failed": failed, "existing_clients": existing,
           "note": ("Preview only — nothing was created." if preview else
                    "Skipped templates need a staff decision (name, listing, or number).")}
    if failed:
        out["STOP"] = (f"{len(failed)} template(s) FAILED (see 'failed'). Report them to the staff "
                       "member and stop. Do not retry them until the template is fixed.")
    return out
