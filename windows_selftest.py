"""Windows self-test for viber-mcp.  Safe: sends, types and focuses NOTHING.

    uv run windows_selftest.py

Checks: Viber process, main window, UI Automation elements (search box, message box, send
button), and background capture + OCR. Saves viber_capture.png (what OCR sees).
Open a chat with some text in the message box to see the send button.
"""
import sys

import win32api
import win32gui

from backends import get_backend
from backends.base import OCRError


def main() -> int:
    if sys.platform != "win32":
        print("This self-test is for Windows only.")
        return 1
    fg, cursor = win32gui.GetForegroundWindow(), win32api.GetCursorPos()
    b = get_backend()
    ok = b.app_running()
    print(f"[{'ok' if ok else 'FAIL'}] Viber.exe running")
    if not ok:
        return 1
    try:
        info = b.debug_info()
    except OCRError as exc:
        print(f"[FAIL] window: {exc}")
        return 1
    print(f"[ok] window {info['rect']}  title={info['title']!r}")
    for name, found in info["uia"].items():
        required = name != "send_button"  # only shown while the message box has text
        print(f"[{'ok' if found else ('FAIL' if required else 'note')}] UIA {name}")
        ok &= found or not required

    try:
        obs = b.observe()
    except OCRError as exc:
        print(f"[FAIL] OCR: {exc}")
        return 1
    print(f"[{'ok' if obs else 'FAIL'}] background capture + OCR read {len(obs)} text items")
    for o in obs[:8]:
        print(f"      x={o['x']:.2f} y={o['y']:.2f}  {o['text'][:60]}")
    ok &= bool(obs)
    b.grab()[0].save("viber_capture.png")

    untouched = win32gui.GetForegroundWindow() == fg
    print(f"[{'ok' if untouched else 'FAIL'}] your foreground window was left alone")
    ok &= untouched
    if win32api.GetCursorPos() != cursor:
        print("[note] cursor moved during the test (the backend never moves it — likely you did)")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
