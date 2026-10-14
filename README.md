# Viber MCP

Let Claude (or any [MCP](https://modelcontextprotocol.io) client) **read your Viber inbox, open
chats, draft replies and send messages** through the Viber Desktop app you're already signed in to.

On **Windows it runs in the background**: no cursor movement, no keyboard input, no clipboard use,
and Viber is never pulled to the front. You keep working while Claude checks your chats.

```
You:     check my Viber inbox and tell me who needs a reply
Claude:  (list_chats) 4 chats have unread messages: Mitch Kim asked for an ETA, ...
You:     draft a reply to Mitch saying we'll be there by 10
Claude:  (search_and_open → put_draft) The draft is in Mitch's chat. Review it and press send.
```

> ⚠️ **Unofficial.** Not affiliated with or endorsed by Viber / Rakuten. It automates the desktop
> app on your own machine and account, and a Viber update can break it. Use at your own risk.

---

## Quick start (Windows)

You need Windows 10/11, [Viber Desktop](https://www.viber.com/download/) signed in, and
[`uv`](https://docs.astral.sh/uv/) (`winget install --id astral-sh.uv`).

```powershell
git clone https://github.com/Deign86/viber-mcp.git
cd viber-mcp
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` installs the dependencies, creates `contacts.json`, runs the self-test, and registers
the server with Claude Code and Claude Desktop (backing up any existing config). **Fully quit and
reopen Claude** afterwards so it loads the server.

### Manual setup

```powershell
uv sync
copy contacts.example.json contacts.json      # optional: name → number aliases
uv run windows_selftest.py                    # safe: sends, types and focuses nothing
```

Then add the server to your client, pointing at the project's own Python:

```json
{
  "mcpServers": {
    "viber": {
      "command": "C:\\path\\to\\viber-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\viber-mcp\\server.py"]
    }
  }
}
```

| Client | Config file |
|--------|-------------|
| Claude Code | `~/.claude.json`, or `claude mcp add --scope user viber -- "<python.exe>" "<server.py>"` |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (Microsoft Store) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json` |

## Tools

| Tool | What it does |
|------|--------------|
| `list_chats()` | Inbox overview: the sidebar's chat names, previews, times and unread counts. |
| `read_open_chat()` | Text of the conversation currently open in Viber. |
| `search_and_open(name)` | Find any contact or group by name and open it. Exact and prefix matches beat substrings; returns `low_confidence` when only a weak match was found. |
| `open_and_read(to)` | Open a chat by number or `contacts.json` name and return its text. |
| `open_chat(to)` | Open a chat by number or `contacts.json` name. |
| `put_draft(text)` | Type `text` into the open chat's message box **without sending**, for you to review. |
| `reply(text)` | Send `text` to the open chat. |
| `send_message(to, text, dry_run=False)` | Open a chat by number or name, send, and verify it was sent. `dry_run` fills the box only. |
| `save_to_my_notes(text)` / `open_my_notes()` | Use Viber's private "My Notes" chat. |
| `list_contacts()` | The aliases in `contacts.json`. |

`to` is an E.164 number (`+639171234567`) or a name from `contacts.json`. Unknown names are
rejected instead of guessed. For anyone not in `contacts.json`, use `search_and_open(name)`.

**Tip:** have Claude `put_draft` first and press send yourself until you trust it. The optional
[`/viber-reply`](commands/viber-reply.md) slash command does exactly that: copy it to
`~/.claude/commands/`, then run `/viber-reply` or `/viber-reply keep it short, say I'll call tonight`.

## How the Windows backend stays out of your way

Viber has no API, and its local message database is encrypted, so everything goes through the app's UI:

| Step | How | Your PC |
|------|-----|---------|
| See | The Viber window is rendered off-screen with `PrintWindow` and read with Windows' built-in OCR (`Windows.Media.Ocr`). | Works while Viber is covered or on another monitor. |
| Find controls | Viber exposes a UI Automation tree, so the search box, message box and send button are found by identity. | No pixel calibration; side panels don't matter. |
| Click and type | Clicks and characters are *posted* to Viber's window (`WM_LBUTTONDOWN`, `WM_CHAR`, ...). Qt handles them without activating the window. | Cursor, keyboard focus and clipboard are untouched. |
| Verify | Text is read back through UI Automation; sends are confirmed by OCR. | |

Two cases briefly hand Viber the foreground and then give it straight back (under 100 ms):

- Text with emoji outside the basic plane (e.g. 😀) or line breaks, which posted typing can't produce.
- Opening a chat **by phone number** (`viber://` link), which Windows may raise. Opening by name
  (`search_and_open`) never does.

## Good to know

- **Keep Viber open**, not closed to the tray. Behind other windows is fine; a minimized window is
  restored without taking focus.
- **Don't use Viber at the same moment.** The tools switch chats inside Viber's window, so your own
  clicks there can collide with them.
- **It only sees what's on screen.** `list_chats` covers the visible part of the sidebar and
  `read_open_chat` the visible messages; scroll Viber for more. OCR can garble small text and emoji.
- **`verified` is best-effort.** `true` = seen on screen, `false` = not seen, `null` = couldn't check.
- If Viber runs **as Administrator**, run the server elevated too.
- After a Viber update, run `uv run windows_selftest.py`. It reports which controls it can't find
  and saves `viber_capture.png`, showing what the server sees.

## Test without Claude

```powershell
uv run client.py list                          # list tools
uv run client.py chats                         # inbox overview
uv run client.py read                          # text of the open chat
uv run client.py search "Mitch"                # open a chat by name
uv run client.py draft "a reply to review"     # fill the message box, don't send
uv run client.py send "+639171234567" "hello" --dry-run
uv run client.py note "remember this"          # send to My Notes
```

## macOS

The original macOS backend is included. It works differently from Windows: it takes over the
mouse and keyboard (HID-sourced events, which Qt requires) and reads the window with Apple Vision OCR.

```bash
uv sync
cp contacts.example.json contacts.json
claude mcp add --transport stdio --scope user viber -- uv --directory "$(pwd)" run server.py
```

Grant **Accessibility** and **Screen Recording** to the app that launches the server
(System Settings → Privacy & Security), then fully quit and reopen it. Keep Viber in a normal
window, not fullscreen. If the toggle is on but you still get error 1002, run
`tccutil reset Accessibility <bundle-id>` and relaunch.

## Project layout

| File | Purpose |
|------|---------|
| `server.py` | MCP stdio server and tool definitions |
| `viber_send.py` | Platform-neutral logic: recipient resolution, send pipeline, search scoring, verification |
| `viber_ocr.py` | Formats OCR output into tool results |
| `backends/windows.py` | Windows backend: PrintWindow + OCR, UI Automation, posted input |
| `backends/macos.py` | macOS backend: Quartz/Vision/AppKit |
| `backends/base.py` | The `Backend` interface each platform implements |
| `windows_selftest.py` | Safe Windows health check |
| `client.py` | Standalone test client |
| `setup.ps1` | One-shot Windows setup and Claude registration |
| `contacts.example.json` | Template for `contacts.json` (git-ignored; holds real numbers) |

## Privacy

Everything runs locally. Messages and contacts never leave your machine except through Viber
itself. `contacts.json` and screenshots are git-ignored.

## Credits & license

Built and maintained by [Deign Lazaro](https://github.com/Deign86), including the background
Windows backend. Based on the original macOS viber-mcp by Wawi del Mundo.
MIT — see [LICENSE](LICENSE).
