"""Client group chats: from a staff template in the templates chat (e.g. APG CLIENTS) to a
ready client GC.

Flow (create_client_gc): read the template message → derive the GC name → create the group with
the staff members (gc_members.json, in order) and then the client's Viber number → post the
client's template (client fields only) → post the listing (details above the dashed line +
photos) → "Hello @client" → back in the templates chat, quote-reply "DONE-<GC NAME>" to the
template. dry_run (default) returns the plan without touching anything.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

import viber_config as config
import viber_drive
import viber_gc_logic as logic
import viber_ocr
import viber_send
import viber_ui

CONFIG = config.user_file("VIBER_MCP_GC_MEMBERS", "gc_members.json")
# Steps a run finished, per template, so a run that stalled or timed out is resumed instead of
# repeated (2026-11-01: a picker stall, then a rerun sent the client the intro a second time).
PROGRESS_FILE = config.DATA_DIR / "gc_progress.json"
PROGRESS_DAYS = 7
# One run at a time: an MCP call that timed out keeps running in the server, and a retry
# started next to it would drive the same picker.
_RUN_LOCK = threading.Lock()


class GCError(Exception):
    """Raised when a client GC can't be planned or created safely."""


def _progress_key(template_text: str) -> str:
    return hashlib.sha1(logic.norm(template_text).encode()).hexdigest()[:16]


def _read_progress() -> dict:
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cutoff = time.time() - PROGRESS_DAYS * 86400
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("at", 0) >= cutoff}


def _write_progress(data: dict) -> None:
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(PROGRESS_FILE)
    except OSError:
        pass  # resuming is a help; never fail a GC over it


def done_steps(template_text: str) -> dict:
    """The steps an earlier run finished for this template ({} when none)."""
    return _read_progress().get(_progress_key(template_text), {}).get("steps", {})


def _mark(template_text: str, step: str, value=True) -> None:
    data = _read_progress()
    entry = data.setdefault(_progress_key(template_text), {"steps": {}})
    entry["steps"][step] = value
    entry["at"] = time.time()
    _write_progress(data)


def _forget(template_text: str) -> None:
    data = _read_progress()
    if data.pop(_progress_key(template_text), None) is not None:
        _write_progress(data)


def _intro_already_sent(intro: str) -> bool:
    """Is the intro already one of my messages in the open 1:1 chat? Catches runs that sent it
    before stalling, including ones from before the progress file existed."""
    mine = viber_send.own_message_texts() or []
    return any(logic_norm(t) == logic_norm(intro) for t in mine)


def _config() -> dict:
    if not CONFIG.exists():
        raise GCError(f"{CONFIG} is missing — copy gc_members.example.json there and fill in the "
                      "staff members to add to client GCs (or pick the file in the Claude "
                      "extension's settings: GC members file).")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _template(n: int) -> str:
    msg = viber_ui._message(n)
    if msg["kind"] != "text" or not msg["text"]:
        raise GCError(f"Message n={n} isn't a text template.")
    return msg["text"]


def plan_client_gc(template_n: int, gc_name: str = "", listing_folder_id: str = "",
                   template_text: str = "") -> dict:
    """Work out everything create_client_gc would do, without changing anything. With
    `template_text` (the template already located by its text), message n isn't read: n counts
    from the newest message on screen, so it moves with every new post and every reopen."""
    cfg = _config()
    text = template_text or _template(template_n)
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
        # The group is built from the client's 1:1 chat (Add participants), so message them 1:1
        # first — if the number has no Viber account, that send fails loudly.
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
        # Staff rule (2026-10-27): no listing in the template = the employee sends it themselves.
        listing_note = ("The template names no listing — the GC is created without one; the "
                        "employee who posted it sends the listing. Don't pick one for them.")
    if listing and listing.get("folder_id") == "none":
        listing = None

    # Only the client's name and Viber number are required: a missing type, size or place is
    # taken from the listing (its folder name "City, sqm Area" and category).
    # A blank SQM is "~" without being "uncertain" — CE-WH-(~, TAYTAY) kept its "~" that way.
    if listing and (naming["uncertain"] or naming["parts"].get("sqm") in (None, "~")):
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
        # Not sent with the listing (staff rule 2026-11-13): only offered if the client wants more
        # options (viber_drive.send_listing_link).
        "listing_link": viber_drive.website_url(listing["folder_id"]) if listing else "",
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
                     done_chat: str = "", template_text: str = "") -> dict:
    """Plan (dry_run) or run the whole client-GC flow for template message n of the OPEN chat.

    A run that stalled or timed out is resumed: the steps it finished (intro, group, template,
    listing, mention) are kept in PROGRESS_FILE and skipped, and the GC it made is reopened by
    name instead of counting as an existing client's."""
    if dry_run:
        return _create_client_gc(template_n, gc_name, listing_folder_id, True, members, done_chat,
                                 template_text)
    if not _RUN_LOCK.acquire(blocking=False):
        raise GCError("A client GC is still being made (an earlier call that timed out keeps "
                      "running). Wait for it to finish, check Viber, then run again — finished "
                      "steps are skipped.")
    try:
        return _create_client_gc(template_n, gc_name, listing_folder_id, False, members, done_chat,
                                 template_text)
    finally:
        _RUN_LOCK.release()


def _create_client_gc(template_n, gc_name, listing_folder_id, dry_run, members, done_chat,
                      template_text=""):
    plan = plan_client_gc(template_n, gc_name, listing_folder_id, template_text=template_text)
    done = done_steps(plan["template_text"])
    # The GC an earlier run made (it then stalled) isn't an existing client's: resume in it.
    resume_gc = done.get("group") if done.get("group") == plan["gc_name"] else None
    if resume_gc:
        plan["problems"] = [p for p in plan["problems"] if not p.startswith("GC ALREADY EXISTS")]
        plan["ready"] = not plan["problems"]
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
        plan["already_done"] = sorted(done)
        plan["note"] = ("Preview only — nothing was done." if plan["ready"] else
                        "NOT READY — fix the problems listed (gc_name / listing_folder_id / the "
                        "template's number) before running; do not retry as-is.")
        if done:
            plan["note"] += (" An earlier run finished " + ", ".join(sorted(done)) +
                             "; those steps will be skipped.")
        return plan

    cfg = _config()
    steps = {}
    template_text = plan["template_text"]
    # One GC per client (hard rule): check "Groups in common" BEFORE the intro, so an existing
    # client isn't told "I'll be creating a GC for you" when no GC will be made. A number whose
    # 1:1 chat can't be checked yet (never messaged) falls through; create_group_from_chat
    # checks again after the intro. Skipped when resuming: the GC we made is in common now.
    existing = None
    try:
        viber_send.open_chat(plan["client_number"])
        if not resume_gc:
            existing = viber_ui.existing_group_with(plan["client_number"])
    except (viber_send.SendError, viber_ui.UIError):
        pass
    if existing:
        raise GCError(f"EXISTING CLIENT — GC NOT CREATED: {plan['client_number']} already shares a "
                      f"group with us ('{existing}'). Nothing was sent to the client. Send the new "
                      "listing there, rename that GC to the new inquiry, then quote-reply "
                      "'DONE-<NEW NAME> — existing client po, GC already created.' to the template. "
                      "Never make a second GC for this client. Do not retry.")
    if plan["intro_message_first"]:
        if done.get("intro") or _intro_already_sent(plan["intro_message_first"]):
            steps["intro"] = {"skipped": "already sent"}
        else:
            try:
                steps["intro"] = viber_send.send_message(plan["client_number"],
                                                         plan["intro_message_first"])
            except viber_send.SendError as exc:
                raise GCError(f"GC NOT CREATED — couldn't message {plan['client_number']} first: "
                              f"{exc} — Report this to the staff member and stop; do not "
                              "retry.") from exc
        _mark(template_text, "intro")
    # Unsaved numbers show as "Unknown"; the mention list uses their Viber name, which their 1:1
    # chat's "<Name> +63… is not in your contact list" banner shows.
    client_name = plan["client_viber_name"]
    if viber_ui.is_placeholder_name(client_name):
        client_name = client_name_from_chat(plan["client_number"])
    if resume_gc:
        created = _reopen_gc(resume_gc)
    else:
        # Build the group from the client's 1:1 chat (header "Add participants" → staff → Save
        # → rename): the New group screen kept failing on its name box and picker.
        viber_send.open_chat(plan["client_number"])
        try:
            created = viber_ui.create_group_from_chat(
                plan["gc_name"], members if members is not None else cfg["members"],
                plan["client_number"], self_name=cfg.get("self", ""))
        except viber_ui.UIError as exc:
            raise GCError(f"GC NOT CREATED — {exc} — Report this to the staff member and stop; "
                          "do not retry.") from exc
        if created.get("existing_group") is not None:
            raise GCError(f"EXISTING CLIENT — GC NOT CREATED: {plan['client_number']} already "
                          f"shares a group with us ('{created['existing_group']}'). Send the new "
                          "listing there, rename that GC to the new inquiry, then quote-reply "
                          "'DONE-<NEW NAME> — existing client po, GC already created.' to the "
                          "template. Do not retry.")
        if not created["created"]:
            raise GCError("The group didn't appear after Save in Add participants — check Viber "
                          "before retrying (it may exist already, named 'Group').")
        _mark(template_text, "group", plan["gc_name"])
        if not created.get("renamed", True):
            raise GCError(f"The group was created but couldn't be renamed to '{plan['gc_name']}' "
                          "— it's the open chat, probably named 'Group'. Rename it by hand; "
                          "nothing was posted and DONE wasn't replied. Then run again: it "
                          "continues in that GC.")
        if created.get("privacy_blocked") is not None:
            who = created["privacy_blocked"] or plan["client_number"]
            raise GCError(f"CLIENT NOT ADDED — GC '{plan['gc_name']}' was created with the staff "
                          f"only: {who} ({plan['client_number']}) couldn't be added because of "
                          "their Viber privacy settings. Nothing was posted in the GC and DONE "
                          "wasn't replied. Ask the client in their 1:1 chat to allow group "
                          "invites (More → Settings → Privacy → Groups and Communities), then add "
                          "them to the GC and post the template, listing and mention. Do not "
                          "retry (that would make a second GC).")
    steps["group"] = created
    # Everything from here goes into the GC, and only there: a new post can bring another chat
    # to the front at any time (APR LISTING ONLY did mid-rename on 2026-10-22), and the
    # template holds the client's details. Each post names the GC (expect_chat).
    gc = plan["gc_name"]
    if done.get("template"):
        steps["template"] = {"skipped": "already posted"}
    else:
        steps["template"] = _in_gc(gc, "the template", lambda: viber_send.reply(
            plan["client_message"], expect_chat=gc))
        _mark(template_text, "template")
    if plan["listing"]:
        if done.get("listing"):
            steps["listing"] = {"skipped": "already sent"}
        else:
            # Drive way (staff rule 2026-11-13, replacing the website-link style of 2026-11-12):
            # the listing's text + photos (+ videos), no website link; the link is only for a
            # client who wants more options.
            steps["listing"] = _in_gc(gc, "the listing", lambda: viber_drive.send_listing(
                plan["listing"]["folder_id"], expect_chat=gc))
            _mark(template_text, "listing")
    if viber_ui.is_placeholder_name(client_name):
        client_name = created.get("client_display_name", "")
    greeting, after = "Hello", ""
    if viber_ui.is_placeholder_name(client_name):
        # Brand-new numbers are "Unknown" in the picker and the mention list too; staff mention
        # them as @Unknown. send_with_mention refuses if more than one participant is "Unknown".
        client_name, greeting = "Unknown", "Hello po,"
        after = "! Please check the listing above po." if plan["listing"] else "!"
    if done.get("mention"):
        steps["mention"] = {"skipped": "already sent"}
    else:
        _in_gc(gc, "the mention", lambda: viber_send.require_open_chat(gc))
        try:
            steps["mention"] = viber_ui.mention(client_name, greeting,
                                                number=plan["client_number"], after=after)
        except viber_ui.UIError as exc:
            raise GCError(f"GC '{plan['gc_name']}' was created and the template/listing posted, "
                          f"but the client couldn't be mentioned as @{client_name} ({exc}), so "
                          "DONE wasn't replied. Report this and stop; do not retry.") from exc
        _mark(template_text, "mention")

    # Back to the templates chat: quote-reply DONE to the original template.
    chat = done_chat or plan["templates_chat"]
    viber_send.search_and_open(chat)
    viber_ui._backend().wait_still()
    target = _find_message(template_text)
    if target is None:
        steps["done_reply"] = {"sent": False, "note": f"Template not found in '{chat}' — reply "
                               f"'{plan['done_reply']}' to it manually."}
    else:
        steps["done_reply"] = viber_ui.reply_to_message(target["n"], plan["done_reply"], send=True)
    _forget(template_text)
    steps["cleanup"] = viber_drive.prune_downloads()  # only photos older than a few days
    out = {"done": True, "gc_name": plan["gc_name"], "steps": steps}
    if done:
        out["resumed"] = sorted(done)
    if steps["mention"].get("verified_number") is False:
        out["WARNING"] = (f"The @{client_name} mention didn't open {plan['client_number']}'s "
                          "profile — it may have mentioned someone else. Check the GC and tell the "
                          "staff member.")
    return out


def _in_gc(gc_name: str, what: str, post):
    """Run `post` (which passes expect_chat=gc_name); a SendError from that guard means another
    chat is open: nothing was posted, and a rerun reopens the GC and carries on from this step."""
    try:
        return post()
    except viber_send.SendError as exc:
        raise GCError(f"GC '{gc_name}' exists, but {what} wasn't posted: {exc} Run create_client_gc "
                      "again: it reopens the GC by name and continues from this step "
                      "(finished steps are skipped).") from exc


def _reopen_gc(name: str) -> dict:
    """Open the GC an earlier run of this template made, confirmed by its header."""
    viber_send.search_and_open(name)
    if not viber_send._poll(lambda: viber_send._header_matches(name), timeout=3.0, first=0.3):
        raise GCError(f"An earlier run created '{name}', but it isn't the open chat after "
                      "searching for it (still named 'Group'?). Open and rename it by hand, then "
                      "run again — finished steps are skipped.")
    return {"created": True, "renamed": True, "resumed": True}


def client_name_from_chat(number: str) -> str:
    """Open the number's 1:1 chat and read the Viber name from its "not in your contact list"
    banner. "" when it can't be read."""
    viber_send.open_chat(number)
    return viber_send._poll(
        lambda: viber_ui.name_from_contact_banner([o["text"] for o in viber_ui._ocr_px()], number),
        timeout=3.0, interval=0.3) or ""


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
    viber_send.type_search_query(name[:30], b, settle=False)
    # The results column, read once the result-section header is up (was a fixed 2 s).
    obs, _ = viber_send._await_results(name[:30])
    texts = [o["text"] for o in obs if o["y"] > 0.1 and o["x"] < 0.3]
    verdict = logic.search_says_gc_exists(texts, name)

    def leave_search():
        b.click_search()
        b.delete()
        if not (getattr(b, "avoid_spare_escape", False) and viber_ocr.search_closed(b)):
            b.press_key("escape")
        b.wait_still()
    if verdict == "unknown":
        header = next((o for o in obs if o["text"].strip().lower() in
                       ("conversations", "groups", "chats")), None)
        if header is not None:
            b.click_result(header["y"] + 0.05)  # the first row under the header
            exists = bool(viber_send._poll(lambda: viber_send._header_matches(name), timeout=2.0,
                                           first=0.3))
            leave_search()
            return exists, True
        verdict = "no"
    leave_search()
    return verdict == "yes", False


def _find_message(text: str, screens: int = 6):
    """Locate the visible message with this exact text: jump to the newest, then scroll up."""
    b = viber_ui._backend()
    want = logic_norm(text)
    viber_ui.jump_to_latest()
    msgs = viber_ui._messages(b.elements())
    for step in range(screens + 1):
        hit = next((m for m in reversed(msgs) if logic_norm(m["text"]) == want), None)
        if hit:
            return hit
        if step < screens:
            msgs = _scroll_up_a_screen(b, msgs)
    return None


def _scroll_up_a_screen(b, before: list[dict]) -> list[dict]:
    """Scroll the open chat up and wait until older messages have rendered (the topmost visible
    message changes), at most ~1 s: Viber loads history in chunks (was a fixed 1 s)."""
    top = before[0]["text"] if before else None
    b.scroll("chat", 10)
    msgs = viber_send._poll(
        lambda: m if (m := viber_ui._messages(b.elements())) and m[0]["text"] != top else None,
        timeout=1.2, first=0.15)
    return msgs if msgs is not None else viber_ui._messages(b.elements())


def _collect_templates_chat(screens: int) -> list[str]:
    """Texts of the templates chat's recent messages, oldest first (scrolls up, then back)."""
    b = viber_ui._backend()
    seen: list[str] = []
    # Start from the newest message, whatever the chat was scrolled to before.
    viber_ui.jump_to_latest()
    msgs = viber_ui._messages(b.elements())
    for step in range(screens):
        for m in reversed(msgs):  # newest → oldest on screen
            if m["text"] and m["text"] not in seen:
                seen.append(m["text"])
        if step < screens - 1:
            msgs = _scroll_up_a_screen(b, msgs)
        else:
            _scroll_up_a_screen(b, msgs)
    viber_ui.jump_to_latest()
    return list(reversed(seen))


def process_client_templates(limit: int = 5, screens: int = 4, preview: bool = False) -> dict:
    """GCs for every template in the templates chat that has no DONE reply yet. preview=True
    only lists what would be created; preview=False (default) creates them. Templates that
    can't be handled safely are skipped and reported."""
    cfg = _config()
    chat = cfg.get("templates_chat") or "APG CLIENTS"
    viber_send.search_and_open(chat)
    viber_ui._backend().wait_still()
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
        viber_ui._backend().wait_still()
        target = _find_message(text, screens + 2)
        if target is None:
            skipped.append({"gc_name": name["gc_name"], "reason": "template scrolled out of reach"})
            continue
        try:
            # By its text from here on: planning searches Viber and may reopen this chat at its
            # newest messages, after which target["n"] is another message.
            plan = plan_client_gc(target["n"], template_text=text)
        except Exception as exc:
            failed.append({"gc_name": name["gc_name"], "error": f"planning failed: {exc}"})
            continue
        # A GC an earlier run made and then stalled in: create_client_gc resumes it.
        resumable = done_steps(text).get("group") == plan["gc_name"] and all(
            p.startswith("GC ALREADY EXISTS") for p in plan["problems"])
        if not plan["ready"] and not resumable:
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
            results.append(create_client_gc(target["n"], dry_run=False, template_text=text))
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
