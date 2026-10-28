"""Pure helpers for the macOS backend (no OS imports, so they're unit-tested on every platform).

Viber's Qt accessibility tree reaches macOS as AX elements whose AXIdentifier is the QML object
path ("…SplitView_QMLTYPE_480.StackView_QMLTYPE_732.SendToolbarButton"). These turn AX nodes into
the same element dicts the Windows backend builds from UIA (type / name / class / text / rect), so
viber_ui's structure matching works unchanged.
"""

from __future__ import annotations

import re

_QML_SUFFIX = re.compile(r"(_QMLTYPE_\d+|_QML_\d+)+$")

# AX role → the UIA control-type names the domain matches on ("Button", "Edit", "MenuItem", …).
_TYPES = {
    "AXButton": "Button", "AXTextField": "Edit", "AXTextArea": "Edit", "AXComboBox": "ComboBox",
    "AXCheckBox": "CheckBox", "AXRadioButton": "RadioButton", "AXStaticText": "Text",
    "AXGroup": "Group", "AXWindow": "Window", "AXSheet": "Window", "AXMenu": "Menu",
    "AXMenuItem": "MenuItem", "AXMenuBar": "MenuBar", "AXMenuBarItem": "MenuItem",
    "AXScrollBar": "ScrollBar", "AXList": "List", "AXImage": "Image", "AXPopUpButton": "ComboBox",
    "AXLink": "Hyperlink", "AXTabGroup": "Tab", "AXSlider": "Slider", "AXCell": "DataItem",
}

# macOS virtual key codes for press_key (same names as the Windows backend accepts).
KEYCODES = {
    "escape": 53, "esc": 53, "enter": 36, "return": 36, "backspace": 51, "delete": 117,
    "tab": 48, "space": 49, "left": 123, "right": 124, "down": 125, "up": 126,
    "pageup": 116, "pagedown": 121, "home": 115, "end": 119,
}


def short_class(identifier: str | None) -> str:
    """'…StackView_QMLTYPE_732.FeedDelegate_QMLTYPE_1472.TextEditItem_QMLTYPE_1552_QML_1559' ->
    'TextEditItem' (the last path segment; the numbers change between runs)."""
    if not identifier:
        return ""
    return _QML_SUFFIX.sub("", identifier.rsplit(".", 1)[-1])


def ax_type(role: str | None, cls: str = "") -> str:
    """UIA-style control type for an AX role. Qt popups (dialogs) are Windows there."""
    if cls == "QQuickPopupItem":
        return "Window"
    if not role:
        return ""
    return _TYPES.get(role, role.removeprefix("AX"))


def edit_text(value) -> str:
    """An Edit's AXValue as the Windows tree gives it: Qt reports line breaks inside a message as
    U+2028 / U+2029 on macOS, which made a multi-line client template parse as one line."""
    if value is None:
        return ""
    return str(value).replace("\u2028", "\n").replace("\u2029", "\n")


def to_rect(frame, origin) -> list[int]:
    """AX frame (x, y, w, h) in screen points -> [l, t, r, b] relative to the window's top-left
    (`origin` = (x, y)), i.e. screenshot coordinates."""
    x, y, w, h = frame
    ox, oy = origin
    return [round(x - ox), round(y - oy), round(x + w - ox), round(y + h - oy)]


def visible_center(frame, window) -> tuple[float, float] | None:
    """Centre of the part of `frame` inside `window` (both (x, y, w, h)), or None when it's
    entirely outside (scrolled out of view) or empty. Hit-testing there tells whether the
    element is actually drawn: Qt also exposes items of hidden pages and covered panels."""
    x, y, w, h = frame
    wx, wy, ww, wh = window
    l, t, r, b = max(x, wx), max(y, wy), min(x + w, wx + ww), min(y + h, wy + wh)
    if r - l < 1 or b - t < 1:
        return None
    return (l + r) / 2, (t + b) / 2


def utf16_units(ch: str) -> int:
    """Length of `ch` in UTF-16 code units (CGEventKeyboardSetUnicodeString counts those)."""
    return len(ch.encode("utf-16-le")) // 2


def is_compose(e: dict) -> bool:
    return e["type"] == "Edit" and e["class"].startswith("QQuickTextEdit")


def is_search(e: dict) -> bool:
    return e["type"] == "Edit" and e["class"].startswith("TextFieldItem")


def is_send(e: dict) -> bool:
    return e["class"] == "SendToolbarButton"


def sidebar_search_box(elements: list[dict], width: float) -> dict | None:
    """The sidebar's search box among elements that skipped the hit-test filter, or None. With
    Viber's search already open (its "Recent chats" list, or the Sponsored ad) the hit test at
    the box's centre can miss it although it's on screen, and search_and_open failed with "search
    box was not found". Only a search field in the sidebar's top bar counts: putting text into
    one through AXValue can't send anything, unlike a message box."""
    return next((e for e in elements if is_search(e) and e["rect"][1] < 100
                 and e["rect"][2] <= width * 0.45), None)


def field_kind(cls: str) -> str:
    """What a focused element is, for error messages."""
    if cls.startswith("QQuickTextEdit"):
        return "the message box"
    if cls.startswith("TextFieldItem"):
        return "a search box"
    return f"'{cls}'" if cls else "nothing"


def typing_target_ok(focused_ident: str | None, wanted_ident: str | None) -> bool:
    """May characters be posted? Posted keys go to whatever has Viber's keyboard focus, and that
    is often still the message box after AXFocused on another field (Viber hands focus back to
    the message box when a search closes), so typing meant for a search box landed in a client's
    chat. Only when the field we focused still has the focus (same QML object path), or when
    nothing was focused on purpose (`wanted_ident` None: the type_text tool after a real click)."""
    if wanted_ident is None:
        return True
    return bool(focused_ident) and focused_ident == wanted_ident


BADGE_RGB = (115, 96, 242)  # Viber's unread-badge purple (light and dark theme)


def badge_boxes(img, tol: int = 40) -> list[tuple[int, int, int, int]]:
    """Boxes (l, t, r, b) of Viber's purple unread badges in `img` (a PIL RGB crop of the chat
    list's right edge), top to bottom. Vision drops a lone white digit in a small circle, so the
    badges are found by colour and read separately. Unread time stamps share the purple but are
    wide, thin text; a badge is a compact blob (a circle, or a pill for 2-3 digits) that is
    mostly filled."""
    from PIL import ImageChops

    r, g, b = img.split()
    mask = None
    for channel, want in zip((r, g, b), BADGE_RGB):
        m = channel.point(lambda v, want=want: 255 if abs(v - want) <= tol else 0)
        mask = m if mask is None else ImageChops.multiply(mask, m)
    w, h = mask.size
    boxes, y = [], 0
    while y < h:
        if mask.crop((0, y, w, y + 1)).getbbox() is None:
            y += 1
            continue
        top = y
        while y < h and mask.crop((0, y, w, y + 1)).getbbox() is not None:
            y += 1
        band = mask.crop((0, top, w, y))
        l, t, rr, bb = band.getbbox()
        bw, bh = rr - l, bb - t
        filled = band.crop((l, t, rr, bb)).histogram()[255] / float(bw * bh)
        if 12 <= bh <= 40 and bh * 0.8 <= bw <= bh * 2.6 and filled >= 0.5:
            boxes.append((l, top + t, rr, top + bb))
    return boxes


# QML items that are popups: the file-send preview ("Popup") and dialogs ("NotificationBox":
# "Pin note?", "Couldn't be added", …). Both are QQuickPopupItem windows in the Windows tree.
POPUP_ROOTS = ("Popup", "NotificationBox")


def nest_popups(els: list[dict], refs: list) -> tuple[list[dict], list]:
    """Rebuild the levels macOS drops inside Qt popups (the file-send preview, dialogs).

    AX skips QML items that have no accessible role, so a popup's caption box hangs straight
    off the window although its AXIdentifier says "…MainWindow.Popup.ScrollView.TextArea". The
    Windows tree keeps those levels and viber_ui navigates them (caption → ScrollView → overlay;
    dialogs are QQuickPopupItem windows), so the missing segments from the "Popup" on become
    virtual elements (ref None) shared by the popup's children. Elements carry their identifier
    as "_ident"; it is dropped here. Only popups: they're one at a time, while a page's repeated
    items (FeedDelegate, …) share one path and would be merged."""
    out: list[dict] = []
    out_refs: list = []
    new_idx: dict[int, int] = {}
    virtual: dict[str, int] = {}
    for i, e in enumerate(els):
        segs = e["_ident"].split(".") if e.get("_ident") else []
        p = e["parent"]
        parent = new_idx.get(p) if p is not None else None
        ppath = els[p]["_ident"].split(".") if p is not None and els[p].get("_ident") else []
        missing = segs[len(ppath):-1] if segs[:len(ppath)] == ppath else []
        start = next((k for k, seg in enumerate(missing) if short_class(seg) in POPUP_ROOTS), None)
        if start is not None:
            prefix = ppath + missing[:start]
            for seg in missing[start:]:
                prefix = prefix + [seg]
                key = ".".join(prefix)
                if key not in virtual:
                    cls = short_class(seg)
                    root = cls in POPUP_ROOTS
                    virtual[key] = len(out)
                    out.append({"type": "Window" if root else "Group", "name": "",
                                "class": "QQuickPopupItem" if root else cls,
                                "text": "", "parent": parent, "rect": list(e["rect"]),
                                "_dialog": cls == "NotificationBox"})
                    out_refs.append(None)
                else:  # grow the virtual box to cover this child too
                    r = out[virtual[key]]["rect"]
                    r[:] = [min(r[0], e["rect"][0]), min(r[1], e["rect"][1]),
                            max(r[2], e["rect"][2]), max(r[3], e["rect"][3])]
                parent = virtual[key]
        new_idx[i] = len(out)
        out.append({k: v for k, v in e.items() if k != "_ident"} | {"parent": parent})
        out_refs.append(refs[i])
    for i, e in enumerate(out):
        if e["type"] == "Window" and e["class"] == "QQuickPopupItem" and not e["name"]:
            # Named by its title, as in the Windows tree (when the title is in the AX tree).
            e["name"] = next((c["name"] for c in out if c["parent"] == i and c["type"] == "Text"
                              and c["name"]), "")
        if e.pop("_dialog", False):
            # Only a dialog's buttons are in the AX tree; its title and message sit above them.
            # Callers OCR inside the popup's rect, so cover where the text is.
            l, t, r, b = e["rect"]
            e["rect"] = [max(0, l - 150), max(0, t - 200), r + 150, b + 20]
    return out, out_refs


def vertical_shift(before, after, direction: int = 0, max_shift: int | None = None) -> int:
    """How many pixels the content of `before` moved down to give `after` (negative = up), two
    same-size grayscale PIL crops. Used to calibrate scrolling: the scrollbar's AXValue is a
    percentage of a height macOS doesn't expose. `direction` (+1 down, -1 up, 0 either) is the
    known scroll direction: repeating content (chat-list rows) also matches a whole row away,
    so only shifts that way count and the smallest near-best one wins. 0 if nothing moved."""
    from PIL import ImageChops, ImageStat

    w, h = before.size
    if after.size != before.size or h < 8:
        return 0
    if h > 480:  # compare at half size (once): ~4x faster, still accurate to 2 px
        half = (max(1, w // 2), h // 2)
        return 2 * vertical_shift(before.resize(half), after.resize(half), direction,
                                  max_shift // 2 if max_shift else None)
    max_shift = max_shift or h // 2
    lo = 1 if direction > 0 else -max_shift
    hi = -1 if direction < 0 else max_shift
    scores = []
    for d in range(lo, hi + 1):
        if d >= 0:
            a, b = before.crop((0, 0, w, h - d)), after.crop((0, d, w, h))
        else:
            a, b = before.crop((0, -d, w, h)), after.crop((0, 0, w, h + d))
        scores.append((ImageStat.Stat(ImageChops.difference(a, b)).mean[0], d))
    if not scores:
        return 0
    best = min(score for score, _ in scores)
    if ImageStat.Stat(ImageChops.difference(before, after)).mean[0] <= best + 0.5:
        return 0  # no shift explains the change better than none
    return min((d for score, d in scores if score <= best * 1.05 + 0.5), key=abs)
