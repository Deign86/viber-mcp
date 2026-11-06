"""macOS self-test for viber-mcp (the counterpart of windows_selftest.py).  Safe: sends, types,
clicks and brings forward NOTHING.

    uv run macos_selftest.py

Checks: the Accessibility and Screen Recording permissions, where Viber.app is (for
start_viber), Viber running and responding, its window, the accessibility elements (search box,
message box, send button), and background capture + OCR. Saves viber_capture.png (what OCR
sees). Open a chat with some text in the message box to see the send button.

Run it from the app that has the permissions (Terminal granted them, or the "Viber MCP" app).
"""
import sys

from backends import get_backend
from backends.base import OCRError


def main() -> int:
    if sys.platform != "darwin":
        print("This self-test is for macOS only.")
        return 1
    b = get_backend()
    ok = True
    for name, granted in b.access().items():
        print(f"[{'ok' if granted else 'FAIL'}] {name.replace('_', ' ')} permission")
        ok &= granted
    try:
        print(f"[ok] Viber.app found (for start_viber/restart_viber): {b._viber_app_path()}")
    except OCRError as exc:
        print(f"[FAIL] {exc}")
        return 1
    running = b.app_running()
    print(f"[{'ok' if running else 'FAIL'}] Viber running")
    if not running or not ok:
        print("RESULT: FAIL")
        return 1
    fg = b.foreground()
    responding = b.app_responding()
    print(f"[{'ok' if responding else 'FAIL'}] Viber is responding")
    ok &= responding
    updater = b.updater_running()
    if updater:
        print(f"[note] an update is in progress: {updater}")
    try:
        info = b.debug_info()
    except OCRError as exc:
        print(f"[FAIL] window: {exc}")
        return 1
    print(f"[ok] window {info['rect']}  title={info['title']!r}")
    for name, found in info["ax"].items():
        required = name != "send_button"  # only shown while the message box has text
        print(f"[{'ok' if found else ('FAIL' if required else 'note')}] AX {name}")
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
    b.screenshot().save("viber_capture.png")

    untouched = b.foreground() == fg
    print(f"[{'ok' if untouched else 'FAIL'}] your front app was left alone")
    ok &= untouched
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
