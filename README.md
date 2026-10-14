# Viber MCP (macOS + Windows)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (e.g. Claude) **send and
read Viber messages** by driving your already-running, signed-in **Viber Desktop** app on
Windows (in the background — no cursor, focus or clipboard takeover) or macOS.

Maintained by [Deign Lazaro](https://github.com/Deign86), who built the background Windows backend.
Based on the original macOS viber-mcp by Wawi del Mundo (MIT).

Viber has no local/public API and its message database is encrypted, so this works entirely through
UI automation:

- **Send** — open the chat via the `viber://` URL scheme, paste the text, and click the send button.
- **Receive** — capture the visible Viber window and OCR it with Apple's Vision framework.

> ⚠️ **Unofficial.** Not affiliated with, endorsed by, or supported by Viber / Rakuten. It automates
> the desktop UI on your own machine and account. UI automation is inherently version-sensitive and
> may break when Viber updates. Use at your own risk and review your account's terms of service.

---

## Why it works this way

Three hard constraints shaped the design (all verified on Viber v28.2.0):

- **The message DB is unreadable.** `~/Library/Application Support/ViberPC/<number>/viber.db` is
  encrypted with a Botan page codec (random header, not `SQLite format 3`), with no public
  key-derivation recipe. So history can't be read from disk.
- **The accessibility tree is unusable.** Viber is a Qt app; AX queries hang, so there's no
  element tree to read or click.
- **Qt ignores ordinary synthetic input.** Every click/keystroke must be generated from a real HID
  event source (`CGEventSourceCreate(kCGEventSourceStateHIDSystemState)`) — plain `CGEvent`s and
  System Events keystrokes are silently dropped by Qt's input widgets.

What's left — and what this uses — is the `viber://` URL scheme, HID-sourced input, and screen OCR.

## Windows (runs in the background)

On Windows the server works **without taking over your PC**: it never moves the cursor, uses
the real keyboard, touches the clipboard, or brings Viber to the front — keep working while it runs.

- **Reading:** the Viber window is rendered off-screen (`PrintWindow`, works while covered or on
  another monitor) and read with Windows' built-in OCR (`Windows.Media.Ocr`).
- **Finding controls:** Viber exposes a UI Automation tree on Windows, so the search box, message
  box and send button are located by identity — no pixel calibration, side panels don't matter.
- **Input:** clicks and text are *posted* to Viber's window (`WM_LBUTTON*` / `WM_CHAR`), which
  Qt handles without activating the window. Emoji outside the basic plane (e.g. 😀) and
  multi-line text fall back to a UIA write, after which focus is handed straight back
  (Viber holds the foreground for well under 100 ms).
- `viber://` number navigation (`send_message`/`open_chat` by number) may make Windows raise Viber
  briefly; focus is handed back the same way. Name search (`search_and_open`) never does.

```powershell
# install uv if needed:  winget install --id astral-sh.uv
cd viber-mcp
uv sync
copy contacts.example.json contacts.json     # then edit with real numbers
uv run windows_selftest.py                   # safe check: types/sends/focuses nothing
```

The self-test checks the window, the UI Automation controls and background OCR, and saves
`viber_capture.png` (what the server sees). Keep Viber open (not minimized to the tray);
it may sit behind other windows or on another monitor.

Register with Claude Code / Claude Desktop — point at the project's venv Python:

```json
{ "mcpServers": { "viber": {
  "command": "C:\\path\\to\\viber-mcp\\.venv\\Scripts\\python.exe",
  "args": ["C:\\path\\to\\viber-mcp\\server.py"] } } }
```

(Claude Code: `mcpServers` in `~/.claude.json`, or
`claude mcp add --scope user viber -- "C:\path\to\viber-mcp\.venv\Scripts\python.exe" "C:\path\to\viber-mcp\server.py"`.
Claude Desktop: `%APPDATA%\Claude\claude_desktop_config.json`, or for the Microsoft Store app
`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json`.)

Notes: OCR reads only what Viber has rendered (scroll for older messages). If Viber runs as
Administrator, run the server elevated too (Windows blocks messages into elevated windows).
Viber's UI changes can break element lookups — `uv run windows_selftest.py` shows what's missing.

## Tools

| Tool | Description |
|------|-------------|
| `send_message(to, text, dry_run=False)` | Open the chat for `to`, confirm it, paste `text`, click send, and OCR-verify it landed. `dry_run` pastes into the box but doesn't send. |
| `reply(text)` | Send `text` into the **currently-open** chat — no navigation. Pair with `read_open_chat`. |
| `open_chat(to)` | Bring a conversation to the foreground without sending. |
| `open_and_read(to)` | Open a conversation and return its visible text in one step. |
| `read_open_chat()` | OCR the currently-visible Viber window and return its text. |
| `save_to_my_notes(text)` | Save a note to Viber's private "My Notes" self-chat (opens it via search, then sends). |
| `open_my_notes()` | Open the "My Notes" self-chat without saving. |
| `list_chats()` | List the sidebar chats (names, previews, times, unread counts) via OCR — a quick inbox overview. |
| `put_draft(text)` | Paste `text` into the currently-open chat's compose box — no navigation, no send. For drafting a reply to review. |
| `search_and_open(name)` | Search Viber by name and open the **best-matching** contact/chat (exact/prefix beats substring). Returns `matched` + `low_confidence`; verify before sending if `low_confidence`. |
| `list_contacts()` | Return the name → number aliases from `contacts.json`. |

`to` is either an **E.164 number** (`+639171234567`) or a **name defined in `contacts.json`**.
Unknown names error out rather than guess a recipient. `send_message` returns `verified: true` when
the sent message is confirmed on screen (`false` = not seen, `null` = couldn't check).

## Requirements

- macOS (tested on macOS 15/26, Apple Silicon)
- Viber Desktop installed, running, and signed in
- [`uv`](https://docs.astral.sh/uv/) (Python is managed by `uv`)

## Install

```bash
git clone <your-fork-url> viber-mcp
cd viber-mcp
uv sync
cp contacts.example.json contacts.json   # then edit with your real contacts
```

### macOS permissions (required)

Grant these to the app that launches the server (your terminal — iTerm/Terminal — or Claude),
in **System Settings → Privacy & Security**:

- **Accessibility** — synthetic clicks/keystrokes (sending).
- **Screen Recording** — window capture (reading + send verification).

The first send/read fails with a clear message until these are granted. After granting, **fully quit
and reopen** the host app so the permission binds.

> If you get `not allowed to send keystrokes` (error 1002) while the toggle already looks on, reset
> the stuck TCC entry and relaunch:
> `tccutil reset Accessibility <your-terminal-bundle-id>` (e.g. `com.googlecode.iterm2`,
> `com.apple.Terminal`).

## Configure contacts

`contacts.json` maps friendly names to E.164 numbers (it is **git-ignored** — it holds real numbers):

```json
{
  "Me": "+639171234567",
  "Mom": "+639181234567"
}
```

Names that aren't in the file must be given as full E.164 numbers.

## Use it from Claude Code

Register the server (user scope):

```bash
claude mcp add --transport stdio --scope user viber -- \
  uv --directory "$(pwd)" run server.py
```

Check with `claude mcp list` (should show `viber … ✓ Connected`). After editing the server code,
reconnect so Claude reloads it (`/mcp` → reconnect `viber`, or restart Claude Code). Remove with
`claude mcp remove viber`.

### `/viber-reply` slash command (optional convenience)

Copy `commands/viber-reply.md` into `~/.claude/commands/` (user scope) or your project's
`.claude/commands/`. It reads the open chat, drafts a reply in your voice, and pastes it into the
compose box **without sending** — you review and send.

```
/viber-reply
/viber-reply keep it short and warm; say I'll call tonight
```

## Use it standalone (no Claude)

A small MCP client is included for testing:

```bash
uv run client.py list                              # advertise tools
uv run client.py contacts                          # show alias map
uv run client.py open "Mom"                        # open a chat
uv run client.py send "Mom" "hello" --dry-run      # fill box, don't send
uv run client.py send "Mom" "hello"                # actually send
uv run client.py draft "a reply to review"         # drop draft in open chat (not sent)
uv run client.py read                              # OCR the open chat
```

## How it works (details)

- **Send**: `open viber://chat?number=<E164>` opens the chat (desktop navigation takes a few
  seconds, so the window is polled via OCR until the recipient's chat is confirmed — and the send is
  refused if the number "isn't on Viber"). Then: click the compose box, clear any draft, paste the
  clipboard text (emoji/unicode/newlines survive), click the send button, and OCR-verify the bubble
  appeared. The clipboard is saved and restored around the paste.
- **Receive**: the largest on-screen window owned by "Rakuten Viber" is captured with
  `screencapture -l <id>` and OCR'd by `VNRecognizeTextRequest`. It only sees what's on screen.

## Automation constraints

Because there's no API — only synthetic input + OCR against a Qt app — a few non-obvious rules make
or break reliability. Keep these in mind when running the tools or editing `viber_send.py`:

- **Synthetic input must use a real HID event source.** Qt silently drops plain `CGEvent`s and
  System Events keystrokes; the code posts every click/keystroke from
  `CGEventSourceCreate(kCGEventSourceStateHIDSystemState)`.
- **Sidebar, search box, send button, and panel-close ✕ are targeted by fixed PIXEL offsets from the
  window edges — not fractions.** The sidebar and side panels have fixed pixel widths, so
  fraction-based clicks miss on wide/maximized windows.
- **Any right-side panel (chat-info or sticker "Settings") must be closed before sending** — it
  shifts the send button. The code detects and closes it automatically.
- **Sending clicks the send button; it does not press Return** ("Send message on Enter" may be off).
- **`viber://` chat navigation is slow and mobile-first** — the code polls via OCR until the target
  chat is confirmed, and refuses to send if the number "isn't on Viber". You cannot message your own
  number ("cannot add yourself"); My Notes is a separate chat.
- **Keep Viber in a normal window on the main desktop — not macOS fullscreen.** Fullscreen puts it on
  a separate Space, causing window-ID churn and blank captures. Don't resize it mid-operation, and
  prefer keeping side panels closed.
- **OCR sees only on-screen text** and can garble small/emoji text or interleave the sidebar with the
  open conversation; the returned `verified` flag is best-effort (false negatives are common).

## Project layout

| File | Purpose |
|------|---------|
| `server.py` | FastMCP stdio server; defines the tools |
| `viber_send.py` | **platform-neutral** recipient resolution, send pipeline, search scoring |
| `viber_ocr.py` | **platform-neutral** formatting of OCR observations into read results |
| `backends/base.py` | `Backend` interface (semantic: observe, clicks, keys, clipboard) + `OCRError` |
| `backends/macos.py` | macOS backend — the only file with OS APIs (Quartz/Vision/AppKit) + Viber layout |
| `backends/__init__.py` | `get_backend()` selects a backend by platform |
| `client.py` | standalone MCP client for testing |
| `contacts.json` | your name → number map (git-ignored) |
| `contacts.example.json` | template to copy |
| `commands/viber-reply.md` | optional Claude Code slash command |

**Porting:** add `backends/<os>.py` implementing `Backend`, wire it into `get_backend()`, and
re-tune that platform's UI-element locations. All domain logic and tools are shared. (Windows can
likely target elements via UI Automation instead of coordinates; Linux/Wayland restricts capture and
synthetic input, so X11 is easier.)

## Limitations & caveats

- **OCR sees only on-screen text** — scroll to bring older messages into view; it can garble text
  or interleave the sidebar's chat previews with the conversation.
- **No message history from disk** (encrypted DB).
- **Names** resolve from `contacts.json` — Viber's own contact list can't be read.
- **Version-sensitive**: a Viber UI/layout change can require small coordinate tweaks.
- Messaging your **own number** is blocked by Viber ("cannot add yourself"); "My Notes" is separate.

## Privacy & security

- Runs **entirely locally**; it does not send your messages or contacts anywhere except into Viber.
- `contacts.json` is git-ignored so real numbers don't get committed.
- Requires powerful macOS permissions (Accessibility, Screen Recording) for the host app — grant
  only if you understand and trust that.

## License

MIT — see [LICENSE](LICENSE).
