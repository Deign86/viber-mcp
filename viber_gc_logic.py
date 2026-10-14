"""Pure text logic for turning Viber client templates into GC names/messages."""
import re
import unicodedata

_INVISIBLE = dict.fromkeys(map(ord, "⁨⁩‎‏﻿"))

_LABELS = [
    ("company", r"company\s*name"),
    ("name", r"name"),
    ("property_type", r"type\s+of\s+property|property"),
    ("sqm", r"preferred\s+sqm"),
    ("budget", r"preferred\s+budget"),
    ("location", r"preferred\s+location"),
    ("viber", r"viber(?:\s+number)?"),
    ("intended_use", r"intended\s+use"),
    ("line_of_business", r"line\s+of\s+business"),
    ("listing", r"listings?"),
    ("intern", r"intern"),
    ("date", r"date"),
]
_LABEL_RES = [(k, re.compile(rf"(?:{p})\s*:\s*(.*)$", re.I)) for k, p in _LABELS]

# Privacy: only these fields are ever forwarded to the client GC.
_CLIENT_FIELDS = (
    "company", "property_type", "name", "sqm", "budget",
    "location", "intended_use", "line_of_business", "viber",
)

_BARE_DATE = re.compile(
    r"^\s*(?:[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*$"
)
_URL_ONLY = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.I)

PROVINCES = {
    "BULACAN", "CAVITE", "LAGUNA", "RIZAL", "BATANGAS", "PAMPANGA", "BATAAN",
    "NUEVA ECIJA", "TARLAC", "ZAMBALES", "QUEZON PROVINCE", "ILOCOS SUR",
    "ILOCOS NORTE", "PANGASINAN", "LA UNION", "AURORA", "ALBAY",
    "CAMARINES SUR", "CAMARINES NORTE", "ISABELA", "BENGUET", "MARINDUQUE",
}

_NO_PLACE = {"", "?", "~", "-", "NONE", "N/A", "NA", "TBD", "ANY", "FLEXIBLE", "ANYWHERE"}
_ROAD_WORDS = re.compile(r"\b(ROAD|RD|STREET|ST|AVENUE|AVE|HIGHWAY|BLVD|BOULEVARD)\b\.?")

_TYPE_RULES = [
    (r"virtual\s+office", "VIRTUALOFFICE"),
    (r"warehouse", "WH"),
    (r"commercial|showroom|retail", "CS"),
    (r"house", "HOUSE"),
    (r"condo", "CONDO"),
    (r"\blot\b", "LOT"),
    (r"office", "OS"),
]


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").translate(_INVISIBLE)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _label_of(line: str):
    stripped = re.sub(r"^[^\w]+", "", line)
    for key, rx in _LABEL_RES:
        m = rx.match(stripped)
        if m:
            return key, m.group(1).strip()
    return None


def parse_template(text: str) -> dict:
    fields = {k: "" for k, _ in _LABELS}
    in_listing = False
    listing_lines: list[str] = []
    for line in clean(text).split("\n"):
        label = _label_of(line)
        if label:
            key, value = label
            in_listing = key == "listing"
            if in_listing:
                if value and not _URL_ONLY.match(value):
                    listing_lines.append(value)
            elif not fields[key]:
                fields[key] = value
            continue
        if _BARE_DATE.match(line):
            in_listing = False
            fields["date"] = fields["date"] or line.strip()
        elif in_listing:
            if not line.strip():
                # Blank lines right after "Listing:" are common; a blank line only ends the
                # section once something has been collected.
                in_listing = not listing_lines
            elif not _URL_ONLY.match(line):
                listing_lines.append(line.strip())
    fields["listing"] = "\n".join(listing_lines)
    return fields


def client_message(text: str) -> str:
    kept = []
    for line in clean(text).split("\n"):
        label = _label_of(line)
        if label and label[0] in _CLIENT_FIELDS:
            kept.append(line.rstrip())
    return "\n".join(kept)


def viber_e164(raw: str) -> str:
    digits = re.sub(r"\D", "", clean(raw))
    if digits.startswith("63"):
        national = digits[2:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits
    if not re.fullmatch(r"9\d{9}", national):
        raise ValueError(f"not a valid PH mobile number: {raw!r}")
    return "+63" + national


def _type_code(text: str):
    low = (text or "").lower()
    for pattern, code in _TYPE_RULES:
        if re.search(pattern, low):
            return code
    return None


_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"


def _sqm_part(text: str):
    """Return (sqm_or_tilde, uncertain_reason_or_None)."""
    raw = (text or "").strip()
    if raw.lower() in ("", "~", "none", "n/a", "na", "-"):
        return "~", None
    nums = [n.replace(",", "") for n in re.findall(_NUM, raw)]
    if not nums:
        return "~", f"SQM not numeric: {raw!r}"
    return (nums[0] if len(nums) == 1 else f"{nums[0]}-{nums[1]}"), None


def _location_part(text: str):
    """Return (LOC, uncertain_reason_or_None)."""
    raw = (text or "").strip()
    if re.match(r"(?i)(along|near|around)\b", raw):
        return "?", f"location not a recognizable place: {raw!r}"
    raw = raw.split("(")[0]
    raw = re.split(r"(?i)\bprefer(?:r)?ably\b|\bnear\b", raw)[0]
    parts = [re.sub(r"\s+", " ", p).strip().upper() for p in raw.split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return "?", f"location missing or unrecognizable: {text!r}"

    def norm(p: str) -> str:
        if p in ("QC", "QUEZON CITY"):
            return "QC"
        return re.sub(r"\s+CITY$", "", p)

    chosen = norm(parts[-1]) if len(parts) > 1 and norm(parts[-1]) in PROVINCES else norm(parts[0])
    if chosen in _NO_PLACE or _ROAD_WORDS.search(chosen) or re.search(r"\d", chosen):
        return "?", f"location not a recognizable place: {text!r}"
    return chosen, None


def gc_name(fields: dict) -> dict:
    uncertain: list[str] = []

    words = (fields.get("name") or "").split()
    name = "".join(c for c in (words[0] if words else "") if c.isalpha()).upper()
    if not name:
        name = "?"
        uncertain.append("name missing")

    ptype_raw = fields.get("property_type") or ""
    code = _type_code(ptype_raw)
    if code is None:
        code = "?"
        uncertain.append(f"unknown property type: {ptype_raw!r}")

    if code == "VIRTUALOFFICE":
        return {
            "gc_name": f"{name}-{code}",
            "uncertain": uncertain,
            "parts": {"name": name, "type": code, "sqm": None, "location": None},
        }

    sqm, sqm_issue = _sqm_part(fields.get("sqm") or "")
    loc, loc_issue = _location_part(fields.get("location") or "")
    uncertain += [i for i in (sqm_issue, loc_issue) if i]
    sqm_text = f"{sqm}SQM" if sqm != "~" else "~"
    return {
        "gc_name": f"{name}-{code}-({sqm_text}, {loc})",
        "uncertain": uncertain,
        "parts": {"name": name, "type": code, "sqm": sqm, "location": loc},
    }


def done_text(gc_name_: str) -> str:
    return "DONE-" + gc_name_


def norm(s: str) -> str:
    """Lowercase alphanumerics only — for comparing names and DONE lines."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def is_template(text: str) -> bool:
    """A client template has at least a name, a Viber number and a type or size."""
    f = parse_template(text)
    return bool(f.get("name") and f.get("viber") and (f.get("property_type") or f.get("sqm")))


def is_done_line(text: str) -> bool:
    return text.strip().upper().startswith("DONE-")


def pending_templates(texts: list[str]) -> list[int]:
    """Indexes of templates (in a chat's messages, oldest first) that still need a GC.

    Staff don't always name GCs after the template's Name (Leah's became DONE-JNT-WH-...), so a
    template counts as handled when its own DONE line exists OR any DONE line comes after it.
    The worst case is a skipped template — never a duplicate GC for a client who has one.
    """
    done_after = [False] * len(texts)
    seen_done = False
    for i in range(len(texts) - 1, -1, -1):
        done_after[i] = seen_done
        seen_done = seen_done or is_done_line(texts[i])
    done_names = {norm(t) for t in texts if is_done_line(t)}
    pending = []
    for i, text in enumerate(texts):
        if not is_template(text) or done_after[i]:
            continue
        if norm(done_text(gc_name(parse_template(text))["gc_name"])) in done_names:
            continue
        pending.append(i)
    return pending


def choose_listing(query: str, listings: list[dict]) -> dict:
    """Pick the listing folder for a template's "Listings:" line.

    Sold properties are never offered. A unique exact folder-name match wins; otherwise a single
    remaining candidate; otherwise no pick and a problem to report.
    """
    active = [c for c in listings if "SOLD" not in c.get("category", "").upper()]
    exact = [c for c in active if norm(c["name"]) == norm(query)]
    if len(exact) == 1:
        return {"listing": exact[0], "candidates": active, "problem": None}
    if len(active) == 1:
        return {"listing": active[0], "candidates": active, "problem": None}
    return {"listing": None, "candidates": active,
            "problem": f"Listing '{query}' matched {len(active)} active folders — pass listing_folder_id."}


DEFAULT_INTRO = ("Hello po {name}! Our admin will be creating a Viber group chat for you shortly, "
                 "where we'll send you the property details. Thank you po!")


def intro_message(template_fields: dict, configured: str = "") -> str:
    """The 1:1 message sent to a client whose number isn't in Viber's recent chats yet (so the
    group picker can't find it): tells them a GC is coming. `configured` may use {name}."""
    first = (template_fields.get("name") or "").split()
    name = first[0].capitalize() if first else ""
    text = (configured or DEFAULT_INTRO).replace("{name}", name)
    return " ".join(text.replace(" !", "!").split()) if not name else text
