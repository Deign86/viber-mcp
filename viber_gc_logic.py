"""Pure text logic for turning Viber client templates into GC names/messages."""
import re
import unicodedata

_INVISIBLE = dict.fromkeys(map(ord, "⁨⁩‎‏﻿"))

# Staff type these templates by hand, so each field accepts the label variants seen in practice
# ("Viber No:", "Contact #:", "Size:", "Location:", ...). Order matters: first match wins.
_NO = r"(?:\s*(?:number|no\.?|num\.?|#))?"
_LABELS = [
    ("company", r"company(?:\s*name)?|business\s*name"),
    ("name", r"(?:client\s*|contact\s*)?name"),
    ("property_type", r"type\s+of\s+property|property\s+type|property|type"),
    ("sqm", r"preferred\s+(?:sqm|size|area|floor\s+area)|sqm|size|floor\s+area"),
    ("budget", r"(?:preferred\s+)?budget"),
    ("location", r"(?:preferred\s+)?location|area"),
    ("viber", rf"viber{_NO}|contact{_NO}|mobile{_NO}|cell(?:phone)?{_NO}|cp{_NO}|phone{_NO}"),
    ("intended_use", r"intended\s+use"),
    ("line_of_business", r"line\s+of\s+business|business"),
    ("listing", r"listings?"),
    ("intern", r"intern|agent"),
    ("date", r"date(?:\s+listed)?"),
]
_LABEL_RES = [(k, re.compile(rf"(?:{p})\s*[:\-–]\s*(.*)$", re.I)) for k, p in _LABELS]

# Privacy: only these fields are ever forwarded to the client GC.
_CLIENT_FIELDS = (
    "company", "property_type", "name", "sqm", "budget",
    "location", "intended_use", "line_of_business", "viber",
)

_BARE_DATE = re.compile(
    r"^\s*(?:[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*$"
)
_MOBILE = re.compile(r"(?:\+?63|0)\s*9\d{2}[\s-]?\d{3}[\s-]?\d{4}")
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
    client_lines: list[str] = []
    for line in clean(text).split("\n"):
        label = _label_of(line)
        if label:
            key, value = label
            in_listing = key == "listing"
            if key in _CLIENT_FIELDS:
                client_lines.append(line)
            if in_listing:
                if value and not _URL_ONLY.match(value):
                    listing_lines.append(value)
            elif not fields[key]:
                fields[key] = value
            continue
        if not in_listing and not _URL_ONLY.match(line):
            client_lines.append(line)
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
    if not _MOBILE.search(fields["viber"] or ""):
        # No usable number label: take the PH mobile number from the client's own lines —
        # never from the listing section, links, or intern/date lines.
        found = next((m.group(0) for line in client_lines for m in [_MOBILE.search(line)] if m), "")
        fields["viber"] = found or fields["viber"]
    return fields


def infer_fields(text: str, fields: dict) -> dict:
    """Fill fields a free-form note left unlabelled ("Oliver / J and T Express / Warehouse /
    600-1000 sqm / 0915…"): the name is the first unlabelled line with no digits, the type the
    first line naming a property type, the size the first line mentioning sqm. Only a name and
    a Viber number are required; the rest can come from the listing (fill_from_listing)."""
    lines = [l.strip() for l in clean(text).split("\n") if l.strip() and not _URL_ONLY.match(l)]
    free = [l for l in lines if not _label_of(l)]
    if not fields.get("name"):
        fields["name"] = next((l for l in free if not re.search(r"\d", l)), "")
    if not fields.get("property_type"):
        fields["property_type"] = next((l for l in free if _type_code(l)), "")
    if not fields.get("sqm"):
        fields["sqm"] = next((l for l in free if re.search(r"(?i)\bsq\.?\s*m|sqm", l)), "")
    return fields


_LISTING_TYPES = [("WAREHOUSE", "WH"), ("COMMERCIAL", "CS"), ("OFFICE", "OS"),
                  ("CONDO", "CONDO"), ("HOUSE", "HOUSE"), ("LOT", "LOT")]


def fill_from_listing(naming: dict, listing_name: str, listing_category: str = "") -> dict:
    """Complete a GC name from the chosen listing ("City, sqm Area" + its category): unknown
    type → the listing's category, "~" size → the listing's sqm, unknown place → its city. The
    filled parts no longer count as uncertain."""
    parts = dict(naming["parts"])
    uncertain = list(naming["uncertain"])
    city, _, rest = (listing_name or "").partition(",")
    if parts.get("type") in (None, "?"):
        cat = (listing_category or "").upper()
        code = next((c for k, c in _LISTING_TYPES if k in cat), None)
        if code:
            parts["type"] = code
            uncertain = [u for u in uncertain if not u.startswith("unknown property type")]
    if parts.get("type") == "VIRTUALOFFICE":
        return naming
    if parts.get("sqm") in (None, "~"):
        nums = re.findall(_NUM, rest)
        if nums:
            parts["sqm"] = nums[0].replace(",", "")
            uncertain = [u for u in uncertain if not u.startswith("SQM not numeric")]
    if parts.get("location") in (None, "?"):
        loc, issue = _location_part(city)
        if not issue:
            parts["location"] = loc
            uncertain = [u for u in uncertain if not u.startswith("location not")]
    sqm_text = f"{parts['sqm']}SQM" if parts.get("sqm") not in (None, "~") else "~"
    return {"gc_name": f"{parts['name']}-{parts['type']}-({sqm_text}, {parts['location']})",
            "uncertain": uncertain, "parts": parts}


def client_message(text: str) -> str:
    kept = []
    for line in clean(text).split("\n"):
        label = _label_of(line)
        if label and label[0] in _CLIENT_FIELDS:
            # Staff rule: a blank field is sent as "~", never left empty.
            kept.append(line.rstrip() + ("" if label[1] else " ~"))
    if not kept:
        # A free-form note with no labels: post the client's fields in the template format.
        f = infer_fields(text, parse_template(text))
        rows = [("🏢 Company Name", "company"), ("👤 Name", "name"),
                ("🏗️ Type of Property", "property_type"), ("📐 Preferred SQM", "sqm"),
                ("💰 Preferred Budget", "budget"), ("📍 Preferred Location", "location"),
                ("📱Viber", "viber")]
        kept = [f"{label}: {f.get(key) or '~'}" for label, key in rows]
    return "\n".join(kept)


_PLACEHOLDERS = {"", "~", "-", "n/a", "na", "none", "not provided", "not specified", "unknown"}


def _is_placeholder(value: str) -> bool:
    return re.sub(r"\s*\(.*\)\s*$", "", clean(value)).strip().lower() in _PLACEHOLDERS


def viber_e164(raw: str) -> str:
    """PH mobiles in any local format → +639XXXXXXXXX; any other number written with its
    country code ("+81 80…", "+60 11…") is accepted as international E.164."""
    text = clean(raw).strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and not digits.startswith("63"):
        if not 8 <= len(digits) <= 15:
            raise ValueError(f"not a valid international number: {raw!r}")
        return "+" + digits
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
    """Return (LOC, uncertain_reason_or_None). Takes the first real place from free text like
    "Pasig or along C5", "Makati / BGC", "Valenzuela, Bulacan", "Pasig City, preferably near Ortigas"."""
    original = (text or "").strip()

    def norm(p: str) -> str:
        if p in ("QC", "QUEZON CITY"):
            return "QC"
        return re.sub(r"\s+CITY$", "", p)

    def is_place(p: str) -> bool:
        return bool(p) and p not in _NO_PLACE and not _ROAD_WORDS.search(p) and not re.search(r"\d", p)

    raw = original.split("(")[0]
    raw = re.split(r"(?i)\bprefer(?:r)?ably\b", raw)[0]
    segments = re.split(r"(?i)\s+or\s+|\s*/\s*|\s+and\s+|\s*&\s*|\s*;\s*", raw)
    for segment in segments:
        segment = segment.strip()
        if not segment or re.match(r"(?i)(along|near|around|within|beside)\b", segment):
            continue  # a road or landmark, not the place itself
        segment = re.split(r"(?i)\bnear\b|\balong\b", segment)[0]
        parts = [re.sub(r"\s+", " ", p).strip().upper() for p in segment.split(",")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        chosen = norm(parts[-1]) if len(parts) > 1 and norm(parts[-1]) in PROVINCES else norm(parts[0])
        if is_place(chosen):
            return chosen, None
    return "?", f"location not a recognizable place: {original!r}"


def gc_name(fields: dict) -> dict:
    uncertain: list[str] = []

    raw_name = fields.get("name") or ""
    if _is_placeholder(raw_name):
        # "Name: Not provided" — use the company name instead (e.g. TPEHcare), never "NOT".
        raw_name = "" if _is_placeholder(fields.get("company") or "") else fields.get("company") or ""
    words = raw_name.split()
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


def _done_token(text: str) -> str:
    """First name part of a DONE line: "DONE-CLAUDIA-CS-(380SQM, PASIG)" → "CLAUDIA"."""
    rest = text.strip()[5:]
    return re.split(r"[-(\s]", rest, maxsplit=1)[0].upper()


def pending_templates(texts: list[str]) -> list[int]:
    """Indexes of templates (in a chat's messages, oldest first) that still need a GC.

    Each DONE line is matched to a template above it: first to the latest unmatched template
    whose GC name or first name it carries (DONE-CLAUDIA-… → Claudia's template); a DONE that
    names no template (staff sometimes use another name, e.g. Leah → DONE-JNT-WH-…) covers the
    nearest unmatched template above it. Templates left unmatched are pending. When unsure the
    result errs toward "handled" — a skipped template, never a duplicate GC.
    """
    templates = {i: gc_name(parse_template(t)) for i, t in enumerate(texts) if is_template(t)}
    handled: set[int] = set()
    unnamed_dones = []
    for d, text in enumerate(texts):
        if not is_done_line(text):
            continue
        token = _done_token(text)
        above = [i for i in templates if i < d and i not in handled]
        same = [i for i in above if norm(done_text(templates[i]["gc_name"])) == norm(text)
                or (token and templates[i]["parts"].get("name") == token)]
        if same:
            handled.add(max(same))
        else:
            unnamed_dones.append(d)
    for d in unnamed_dones:
        above = [i for i in templates if i < d and i not in handled]
        if above:
            handled.add(max(above))
    return sorted(i for i in templates if i not in handled)


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
    raw = template_fields.get("name") or ""
    if _is_placeholder(raw):
        raw = "" if _is_placeholder(template_fields.get("company") or "") else template_fields.get("company") or ""
    first = raw.split()
    name = first[0].capitalize() if first else ""
    text = (configured or DEFAULT_INTRO).replace("{name}", name)
    return " ".join(text.replace(" !", "!").split()) if not name else text


_SECTION_HEADERS = {"conversations", "groups", "contacts", "chats", "communities", "channels"}


def search_says_gc_exists(result_texts: list[str], gc_name_: str) -> str:
    """Read Viber's search results (OCR lines of the left column, search box excluded) for a GC
    name: "yes" (a readable result title starts like the name), "no" (no results), or "unknown"
    (results exist but the highlighted title couldn't be read — open the top one and compare
    the chat header). Titles are truncated and OCR confuses I/l/1 and O/0, so compare prefixes
    with those folded."""
    def fold(s: str) -> str:
        return norm(s).translate(str.maketrans({"l": "i", "1": "i", "0": "o"}))

    head = fold(gc_name_)[:12]
    lines = [t.strip() for t in result_texts if t.strip()]
    if any("no results" in t.lower() for t in lines):
        return "no"
    if head and any(fold(t).startswith(head) for t in lines):
        return "yes"
    if any(t.lower() in _SECTION_HEADERS for t in lines):
        return "unknown"
    return "no"
