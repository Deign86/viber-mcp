# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server (Windows and macOS) that sends and reads Viber Desktop messages by **automating the
running, signed-in Viber app** — Viber has no API and its message DB is encrypted, so everything goes
through the UI: the `viber://` URL scheme, Qt's accessibility tree (UIA on Windows, AX on macOS) to
find and read controls, input the OS lets a background app receive, and screen OCR.

## Commands

```bash
uv sync                                    # install deps (Python managed by uv)

# Standalone client = the way to test/run tools without Claude (spawns a fresh server each call,
# so it always uses the latest code). Subcommands map 1:1 to tools:
uv run client.py list                      # advertise tools
uv run client.py open|openread|search "<name-or-+E164>"
uv run client.py send  "<to>" "<text>" [--dry-run]
uv run client.py reply "<text>"            # into the currently-open chat
uv run client.py draft "<text>"            # paste into open chat, do NOT send
uv run client.py note  "<text>"            # save to My Notes
uv run client.py call  <tool> '<json>'     # any tool (images saved as tool_image_N.png)
uv run client.py chats | read | contacts

# Register with Claude Code (user scope):
claude mcp add --transport stdio --scope user viber -- uv --directory "$(pwd)" run server.py
```

**Tests:** `uv run --with pytest pytest tests -q` (no Viber needed; CI runs it on windows-latest via
`.github/workflows/tests.yml`). Pure decision logic lives in `viber_gc_logic.py` (template parsing,
naming, `pending_templates`, `choose_listing`) and pure helpers in `viber_ui.py` (`_messages`,
`_preview`, `find_number_row`, ...) so it can be tested with fixture data; UI-structure tests inject a
fake backend via `backends._backend` (no module mocking). **Every bug found on the live app gets a
regression test** in `tests/test_regressions.py` (or the matching test file) before/with the fix.
Flows that poll (`_poll`) are tested with stateful fakes (`ChatBackend`, `PickerBackend`,
`PanelBackend`, `GroupFlowBackend` in `test_regressions.py`) that change state after N reads; a
fake that must make a poll time out uses `_use(..., fast_clock=True)` so the deadline is virtual.
`tests/test_windows_backend.py` unit-tests the Windows backend (cached UIA walk, `wait_still`,
`observe(region)`, value caching, `open_url`) with fake COM elements; it skips off Windows.

Then **verify on the real app** with `client.py` (or `client.py call <tool> '<json>'`) and screenshots —
the returned `verified` flag is best-effort and gives false negatives. Only test writes in My Notes.

After editing server code, the **registered** MCP server keeps running the old code until reconnected
(`/mcp` → reconnect `viber`, or restart Claude Code); `client.py` always runs fresh code.

## Architecture

The code is split into **platform-neutral domain logic** and a **platform Backend** (so the app is
port-ready for Windows/Linux — see "Porting" below):

- `server.py` — FastMCP stdio server; thin `@mcp.tool()` wrappers only. Tools: `send_message`,
  `reply`, `open_chat`, `open_and_read`, `read_open_chat`, `list_chats` (structured `chats` rows via
  `viber_ocr.chat_rows`, pure; `unread_only` clicks Viber's Unread/All filter pills at their OCR'd
  position), `current_chat` (`viber_send.current_chat`: title / subtitle / `is_group` / number from
  the header band only), `mark_as_read` (right-click the sidebar row → UIA `MenuItem` "Mark as
  read"; the menu also holds "Delete conversation", never clicked; a posted Escape does NOT close
  that menu — `_close_sidebar_menu` clicks the "Chats" label instead), `rename_group`
  (`_rename_open_chat` with `_name_matches`: exact after OCR folding, or prefix for 8+ chars so the
  truncated sidebar/header name works), `groups_in_common` (`_groups_in_common`; `existing_client`),
  `put_draft`,
  `search_and_open`, `search_messages` (message-text search: the 2nd `TabComponent` under the
  search box; `message_search_rows` parses the OCR'd rows), `save_to_my_notes`, `open_my_notes`, `list_contacts`, `scroll_chat`,
  `scroll_chat_list` (backend `scroll(area, notches)`; Windows posts `WM_MOUSEWHEEL`, macOS sends
  real wheel events during a brief front hand-off),
  `backread_chat` (`viber_ui.backread`: scrolls up 10 notches a screen from the newest, joins the
  UIA screens at their longest overlap (`_merge_older`, keeps repeated "ok"/photo rows), stops
  when the top stops moving twice; returns the whole chat oldest first, then jumps back down).
- `viber_send.py` — **platform-neutral** write/navigation logic: recipient resolution
  (`contacts.json` alias → E.164), the send pipeline, search-result scoring, panel detection, send
  verification. Holds a module-level `backend = get_backend()` and calls only its semantic methods.
  Raises `SendError`.
- `viber_ocr.py` — **platform-neutral** read side: `read_open_chat()` / `read_chat_list()` format
  `backend.observe()` output (`read_chat_list` keeps only left-column x<0.30). Raises `OCRError`.
- `backends/` — the platform layer.
  - `base.py` — `Backend` ABC + `OCRError`. Semantic interface: `app_running/focus/open_url`,
    `observe()→[{text,x,y}]`, clipboard, `type_text/select_all/delete/paste`, and element clicks
    `click_compose/send/search/result(norm_y)/panel_close`. The domain never sees pixels or key codes.
  - `macos.py` — `MacBackend`: AX (ApplicationServices), Quartz, Vision; `_maclogic.py` holds its
    pure helpers (AX node → element dict, popup nesting, unread-badge detection), tested everywhere.
  - `windows.py` — `WindowsBackend` (UIA, PrintWindow, posted messages); pure helpers in `_winlogic.py`.
  - `__init__.py` — `get_backend()` picks the backend by `sys.platform`.
- `client.py` — standalone MCP stdio client for testing.
- `contacts.json` — name→E.164 map, **git-ignored (real numbers)**; ship `contacts.example.json`.

Data flow for a send: resolve recipient → `backend.open_url("viber://chat?number=…")` → poll
`backend.observe()` until the chat is confirmed → `click_compose` → `select_all`+`delete` → `paste`
(clipboard, restored after) → `click_send` → OCR-verify the box cleared.

## Porting to another OS

Add `backends/<os>.py` implementing `Backend`, wire it into `get_backend()`, and re-tune that
platform's element locations. The domain logic, tool definitions, and `contacts.json` handling are
already shared and need no changes.

Status:

| Platform | Backend | State |
|----------|---------|-------|
| macOS | `backends/macos.py` (`MacBackend`) | ✅ implemented, mostly background (AX to locate/read/type/press, Vision OCR, posted characters; real input with a brief front hand-off for clicks on non-controls, scrolling and named keys) |
| Windows | `backends/windows.py` (`WindowsBackend`) | ✅ implemented, background (PrintWindow + Windows.Media.Ocr, UIA to locate, posted messages for input; `windows_selftest.py`) |
| Linux | — | ❌ not started |

**Windows (background mode) — constraints learned the hard way:**
- **Pick the window titled "Rakuten Viber".** Qt also owns hidden helper windows
  (`...ThemeChangeObserverWindow`, `QTrayIconMessageWindow`) that can be *larger*; never
  `ShowWindow` them — doing so leaves a blank white window on screen.
- **UIA is for locating/reading only.** UIA `SetValue`/`Invoke` make Qt activate Viber and steal
  keyboard focus. Clicks and text are *posted* (`WM_LBUTTONDOWN/UP`, `WM_CHAR`, `WM_KEYDOWN`) to
  the main hwnd at UIA-derived client coords — Qt handles them without activation.
- **Posted `WM_CHAR` drops non-BMP chars** (surrogate pairs, e.g. 😀; `WM_UNICHAR` is ignored
  too) and Enter may send, so those texts fall back to UIA `SetValue` + `_restore_foreground`
  (AttachThreadInput trick; activation arrives asynchronously, so wait for it before restoring).
- **Search-result UIA delegates are whole sections**, not rows — click the OCR'd row position.
- Capture with `PrintWindow(..., PW_RENDERFULLCONTENT)`; it works while Viber is covered.
- **Speed (profiled 2026-10-21 on the live app).** OCR takes the raw BGRA pixels straight into a
  `SoftwareBitmap` (the PNG round trip cost more than recognition; words are identical).
  `observe(region=(l, t, r, b))` crops before OCR — the sidebar is ~5x cheaper, the header band
  ~20x; it's a *hint*, so callers still filter by x/y. UIA is read through
  `_levels()` (one `FindAllBuildCache` per tree level, `_Node`s with cached properties) — never
  go back to per-property `Control` reads in loops. The message/search box located by
  `click_compose`/`click_search` is kept (`_target_node`) so `_value()` polls cost ~1 ms; anything
  that can navigate (`click_at`, `click_result`, `press_key`, `open_url`) drops it. Wheel notches
  are posted `_WHEEL_GAP` apart (5 ms; 30 ms made a 60-notch scroll take 2 s). `open_url`'s
  foreground guard runs in a thread so chat polling starts at once. Prefer `viber_send._poll`
  over fixed sleeps; `number_confirmed` accepts an unsaved number's chat from its "not in your
  contact list" banner instead of the 4 s settle (never from the header: its band overlaps the
  first message's). GC flow (profiled with a test group): `_ocr_px(region_px=...)` reads only
  the side panel / mention zone, `_SB_CACHE` keeps the sidebar edge (no UIA walk per read), the
  picker/rename/info-panel steps poll UIA state instead of sleeping, and the post-Save
  `hold_foreground` runs in a thread. A tick went 3.4 s → 1.7 s, the mention 6.6 s → 3.3 s.
  **Never walk the UIA tree while Qt animates** (a panel sliding in, a page fading): each
  round trip then waits about a frame, so a ~50 ms walk takes >1 s. After a click that opens
  or changes a panel, call `backend.wait_still()` (two identical PrintWindow captures) and
  then walk once; `_levels` also fetches two levels per call (children + cached grandchildren)
  to halve the round trips. A UIA call that doesn't return in 30 s leaves its thread stuck, so
  `_on_uia` abandons that thread, starts a fresh one and raises `OCRError` ("try again").
  **UIA has no text for the chat list rows, the search result rows or the chat header** (they
  are unnamed `ListViewDelegateLoader`s), so those stay OCR; `_await_results` hands over to the
  open-first-result fallback after two reads with the results header up and no readable row.
  Dialogs ARE in UIA: a `Window` of class `QQuickPopupItem` named by the title ("Pin note?",
  "Couldn't be added") with `NotificationButton` children — `_popup` / `_dismiss_privacy_dialog`
  poll for one instead of OCR-ing four times. GC coordination (`gc_exists`, `_find_message`,
  `_collect_templates_chat`, `client_name_from_chat`) polls too (`_scroll_up_a_screen` waits for
  the topmost message to change after a scroll).
- The "not in your contact list" banner appears on every unsaved-number chat — not an error.

**Linux:** hardest target — Wayland heavily restricts screen capture and synthetic input; X11
(via `xdotool`/`wmctrl` + Tesseract) is far easier.

## Critical, non-obvious constraints (most bugs here come from violating these)

- **macOS (Viber 29.1, macOS 15) — measured, not assumed:**
  - Qt's accessibility tree IS usable on macOS: every AX element's `AXIdentifier` is the QML object
    path, so `short_class` gives the same classes as Windows UIA (StackView, TextEditItem,
    SendToolbarButton, QQuickTextEdit, TextFieldItem, SideBarContent, …). AX skips role-less
    items, so the tree is flatter: messages are direct children of the StackView (`_messages`
    handles that layout) and popups lose their levels — `nest_popups` rebuilds them from the
    identifier (Popup → `Window`/`QQuickPopupItem`, then ScrollView …), only for popups. Dialogs
    ("Pin note?", "Couldn't be added") are `MainWindow.NotificationBox` with only their
    NotificationButtons in AX (no title text): the virtual window's rect is widened above the
    buttons so `_ocr_px(popup rect)` reads the message.
  - Qt exposes items it isn't drawing (the hidden ChatGPT panel's `TextArea`, pages under a side
    panel); `_walk` keeps only elements that `AXUIElementCopyElementAtPosition` at their visible
    centre returns (self, descendant or ancestor). Popup overlays don't take part in that hit test,
    so code looking for a popup's part must check structure (`_preview` picks the TextArea whose
    overlay has a send button).
  - Work in the background: `AXValue` set on the message/search box (search reacts to it),
    `AXPress` on buttons (Viber rebuilds the send button once text is in — look it up fresh), and
    characters posted with `CGEventPostToPid`. Ignored in the background: posted mouse clicks
    (even with the window-number fields), posted wheel events, posted Escape to the image viewer.
    Those go through `_with_viber_front`: Viber made frontmost via `AXFrontmost`, real HID events,
    then the cursor and the previous front app are restored (~0.6 s). It holds `_handoff`, and it
    sends nothing unless Viber really is in front (the input would land in the user's app);
    `hold_foreground` only acts while it can take that lock.
  - `NSWorkspace.frontmostApplication()` goes stale in this run-loop-less process and the
    system-wide AX focused app is empty for Electron apps: `foreground()` reads the owner of the
    topmost layer-0 window.
  - All synthetic input comes from a HID event source with zeroed modifier flags (a leftover Cmd
    turns "M" into Cmd+M = minimize).
  - Vision misses a lone white digit in the purple unread badge: `badge_boxes` finds the badges by
    colour, `_read_badges` reads them as one line of black-on-white digits and maps each digit by
    its character box; they're added to `observe()` as bare-number observations for `chat_rows`.
    Language correction is off (it rewrites names/numbers and costs ~25%).
  - Coordinates are points relative to the window's top-left (title bar included);
    `screenshot()` is downscaled to points on Retina so viber_ui's pixel offsets hold.
  - The red ✕ hides Viber's window without quitting; `focus()` reopens it with `open -g -b`.
    Viber activates itself on a `viber://` link even with `open -g`, so `open_url` hands the
    front back for 4 s in a thread (like the Windows backend).
  - The file picker is an `AXSheet` ("send a file"). Cmd+Shift+G (real keys) opens a nested sheet
    with the path field; set `AXValue` AND `AXFocused` before Return, or Return goes nowhere and
    Open stays disabled. Then `AXPress` Open.
  - The reaction hearts aren't in the macOS AX tree, so `like_message` can't find them there.
- **The send button is found by identity** (`SendToolbarButton`) on both platforms, so side panels
  that shift it don't matter (`panel_shifts_send = False`); `_ensure_panel_closed` only runs for a
  backend that clicks by position.
- **Click the send button; do not press Return.** "Send message on Enter" may be off, in which case
  Return just inserts a newline.
- **`viber://chat?number=` navigation is slow (~seconds), mobile-first, and sometimes doesn't
  navigate at all.** Never assume the chat opened — poll via OCR (`_wait_for_sendable_chat`), and
  confirm it ONLY from the chat pane's header band (`chat_confirmed`: the number under the name,
  the unsaved-number banner, or the title matching the name) — never from the sidebar, the compose
  placeholder or message text, which all belong to whatever chat was open before (that sent
  messages to the wrong person). Without strong proof a chat is accepted after a 4 s settle only
  if its header differs from the one captured before `open_url`. It also errors "isn't on Viber"
  for non-users and "cannot add yourself" for the user's own number (My Notes is separate).
- **Every open-chat writer takes `expect_chat`** (`reply`, `paste_into_open_chat`,
  `reply_to_message`, `send_files`, `send_listing` — passed through to each step):
  `viber_send.require_open_chat` reads the header band (`_HEADER_REGION`, ~20x cheaper than a full
  OCR) and raises `SendError` naming the chat that IS open before anything is staged or clicked.
  `chat_confirmed` does the matching (name prefix-tolerant and OCR-folded, or number digits).
  Guidelines tell the model to always pass it. On 2026-10-24 a test send with the wrong
  `expect_chat` reached a staff chat because My Notes hadn't actually been opened — the guard was
  right, the caller wasn't; `current_chat` first when unsure.
- **Send verification is UIA-first.** `sent_confirmed(text, before)` compares `own_message_texts()`
  (my `TextEditItem` texts, isolate marks stripped, long texts by first line) before and after the
  send; a new matching message = verified. Only without a UI tree or when no new message is
  seen does it fall back to the OCR check (`_verify_sent`: text seen AND placeholder back). The
  OCR check passes texts under 6 alphanumerics unverified-as-true ("ok"), so short texts still
  can't fail it. Tests that fake the backend must patch BOTH `backends._backend` and
  `viber_send.backend` (viber_send keeps its own module-level reference).
- **Search results are substring-matched and alphabetically sorted**, so the first row is often wrong
  (searching "Ivy" lists "Divya" first). `_find_result_row` scores exact/prefix over substring and
  clicks the matched row's actual OCR position; excludes the search-bar text (y<0.12), section
  headers, phone numbers, and action rows.
- **`MacBackend._onscreen_window` ignores the tiny menu-bar window** (>200px filter) so captures
  aren't blank.
- **OCR interleaves the sidebar and the open conversation.** Use x-position filtering
  (`read_chat_list` keeps x<0.30) or headers to disambiguate; OCR also garbles small/emoji text.

## Environment requirements

- Viber running, signed in, and in a **normal window on the main desktop — not macOS fullscreen**
  (fullscreen puts it on a separate Space, causing window-ID churn and blank captures). Don't resize
  it mid-operation.
- macOS **Accessibility** (AX tree and input) and **Screen Recording** (capture) granted to the
  server's *responsible app*. Claude spawns MCP servers with responsibility disclaimed, so the
  first binary of the command is what macOS asks about: register `~/Applications/Viber MCP.app`
  (`setup_macos.sh`, `macos/launcher.c`, which spawns the server as its child) so the grant is
  "Viber MCP" and not `uv`. `client.py`/tests run from a terminal or Claude Code's shell use that
  app's grants instead. `MacBackend` calls `AXIsProcessTrustedWithOptions(prompt)` /
  `CGRequestScreenCaptureAccess()` once per run when missing, so the app shows up in Settings. A stuck grant shows error 1002 ("not allowed
  to send keystrokes") despite the toggle being on — fix with
  `tccutil reset Accessibility <bundle-id>` (e.g. `com.googlecode.iterm2`) and relaunch.

## Staff rules, prompts, computer use

- `guidelines.md` is the staff operating rulebook; `server.py` serves it as MCP server
  `instructions`, so every client gets it automatically. Change rules there, not in tool docs.
- MCP prompts (`inbox_triage`, `draft_reply`, `unsend`, `property_details`, `client_gc`) are the
  slash-command workflows.
- Client GCs: `viber_gc_logic.py` (pure: template parsing, client-field whitelist, naming rules,
  tested) + `viber_gc.py` (plan/run) + `viber_ui.create_group_from_chat` / `mention`.
  **GCs are built from the client's 1:1 chat** (staff: "the most reliable route"): the intro
  message is always sent first, then header `AddParticipantsButton` → the side-panel picker (client
  pre-ticked) → tick the staff → `Save` → rename via the info panel's ✎. The old New group screen
  flow (`create_group`, `number_on_viber`) failed with "GC NOT CREATED — name missing" and was
  removed; `find_number_row` / `select_circle_point` / `privacy_blocked_from_dialog` stay (used by
  the picker and the privacy check). Guards: `is_one_to_one_with` (banner/header number, not "N participants" —
  `open_chat`'s viber:// link sometimes doesn't navigate and the client's GC also shows the number
  in its template); "Groups in common" non-empty → EXISTING CLIENT (hard rule: one GC per client; `viber_gc` checks
  it via `viber_ui.existing_group_with` BEFORE the intro, and `create_group_from_chat` again after); picker search text that lands
  in the message box is backspaced. Side-panel ← must be INSIDE the panel (`side_panel_back_point`)
  — the chat pane's own ← next to it jumps to the previous chat.
  The pickers and the mention list aren't in the UIA tree — rows are found by OCR; OCR confuses
  I/l/1 and O/0, so names are compared after folding those (`_norm`, `viber_send._ocr_fold`).
  **Sponsored ad:** while Viber's ad box shows (bottom of the chat list), Qt's UIA tree can loop and
  drop the chat pane → "No chat is open" / "message box was not found" from send_files, reply,
  send_listing. `WindowsBackend.elements()`/`_rect` close the ad (`_winlogic.ad_close_point`), and
  the Viber Plus upsell that pops up after it (`promo_close_point`).
  The picker labels unsaved numbers "Unknown"; their Viber name comes from the 1:1 chat's
  "<Name> +63… is not in your contact list" banner (`viber_gc.client_name_from_chat`). When no
  name can be read (brand-new numbers are "Unknown" in the mention list too), the client is
  mentioned as `@Unknown` — staff expect that: "Hello po, @Unk" → pick the row → "Hello po,
  @Unknown! Please check the listing above po." → DONE. `send_with_mention` refuses (nothing sent,
  flow stops before DONE) when more than one mention-list row is exactly the name (two "Unknown"s).
  Mentions are picked with Down×k + Tab and accepted only if the box holds exactly `@<name>`
  (`mention_inserted` — "Anthony" is inside "Mark Anthony Santos"); then a space closes the list
  before send (sending with it open sent the highlighted, wrong person). Punctuation right after
  the name ("@Unknown!") is typed after that space is backspaced away; if the backspace takes the
  mention with it, it starts over keeping the space. Clicking a sent mention
  opens that person's 1:1 chat (number under the name, or in the banner) — `mention_opens_number`.
  A client whose privacy settings block group invites gets a "Couldn't be added … due to their
  privacy settings" dialog after Save; the GC exists with the staff only.
  `privacy_blocked_from_dialog` detects it, `_dismiss_privacy_dialog` closes it, and `create_client_gc`
  stops with CLIENT NOT ADDED before posting anything (ask the client to allow group invites).
  Only the client's name and Viber number are required: `infer_fields` reads free-form notes
  (first digit-free line = name, a line naming a type, a line with "sqm"), and
  `fill_from_listing` takes a missing type/size/place from the listing folder ("City, sqm Area"
  + category). `client_message` rebuilds the template format when the note has no labels.
- `viber_ui.py` holds the computer-use primitives (screenshot/click/type/key/scroll/elements)
  and the actions built on them (`list_messages`, `open_image`, `unsend_message`). On Windows,
  messages are `FeedDelegate` elements; text is the child `TextEditItem` edit's value, images
  have a `RemixImageButton` child just under the picture; own messages are right of the chat
  pane's (`StackView`) centre. The context menu, image viewer and dialogs render inside the main
  window. Never click a generic "Delete" *button* — the info panel's "Delete" deletes the chat;
  unsend only uses the context-menu `MenuItem` "Delete".
- `send_files` (viber_ui.py, Windows) drives Viber's attach flow with window messages only. The
  attach button is the leftmost `IconButton` on the message-box row; the first click on it is
  sometimes swallowed, so it retries. The picker is a `#32770` dialog: fill the filename via
  `WM_SETTEXT` on `ComboBoxEx32 > ComboBox > Edit`, then `WM_COMMAND` `IDOK`. After it closes Windows
  re-activates Viber, so focus is handed back immediately. The preview overlay: the caption `Edit`
  (class `TextArea`); the overlay container is the caption's grandparent; send is a `RoundIconButton`
  child of the overlay; add-more is the `IconButton` child of the overlay just above the caption
  (NOT the overlay's close X `IconButton` at the top right). `dry_run` stops before send. After
  the send, `_settle_and_resend` waits for the feed to stop changing (`_feed_still`: Viber
  animates a progress ring on the bubbles while uploading; nothing of it is in UIA, and the
  delivered ticks show once it's still) instead of a fixed 4 s + 1.5 s/file, which is now only
  the cap; then it presses the not-sent banner's Resend if it shows.
- `viber_drive.py` — Google Drive listings (read-only OAuth desktop flow). Client file
  `google_oauth_client.json` next to `server.py` (git-ignored) or env `VIBER_MCP_GOOGLE_CLIENT`;
  token at `%LOCALAPPDATA%\viber-mcp\google_token.json`; downloads go to
  `%LOCALAPPDATA%\viber-mcp\drive-cache` (fetched in parallel, `DOWNLOAD_THREADS`, each thread
  with its own Drive service — httplib2 isn't thread-safe; written as `.part` then renamed, so a
  file under its final name is whole and is reused without a metadata call) and are kept ~3 days, then pruned by `prune_downloads`
  (`VIBER_MCP_KEEP_DOWNLOADS_DAYS`; never delete right after sending — that raced Viber's upload and
  left photos "not sent"). `remove_downloads` /
  `clear_downloads`, which never delete outside that folder); root folder name from `VIBER_MCP_LISTINGS_FOLDER`
  (default `APR LISTING`). Layout: root / CATEGORY / `FOR LEASE/RENT` or `FOR SALE` / [group, e.g.
  `LOT` — a comma-less folder with subfolders, `_is_subfolder_group`] / `City, sqm Area`
  folder with one Google Doc (title starts with `*`) plus JPGs. Drive calls use
  `execute(num_retries=RETRIES)` — 500s and dropped connections (WinError 10053) are common. Doc
  text is read via `_plain_export`: Drive's text/plain export uses \r\n and two breaks per empty
  paragraph, so without it a get_listing → update_listing_doc round trip doubled every gap. The doc is split at the typed dashed
  line: text above is client-facing, below is INTERNAL (address, owner/agent numbers) and withheld in
  code unless `get_listing(include_internal=True)`. Docs with no dashed line are split by inference
  (`split_listing`: the client part ends with our company contact block, else before the first
  internal-looking line; it refuses if a non-company phone number would be sent).
  `post_new_listing` still requires the line (`strict=True`). `send_listing` sends the text, then the photos via
  `send_files`, then the folder's videos as a second batch (`include_videos` defaults to True —
  some folders only have a video); never the internal part. Tools: `drive_sign_in`, `find_listings`, `get_listing`,
  `send_listing`, `drive_search`, `drive_download`, `post_new_listing`, `confirm_listing_in_drive`,
  `add_listing_to_drive`, `drive_upload_files`, `update_listing_doc`, `move_listing_to_sold`
  (`sold_target` picks SOLD / SOLD <CATEGORY>; refuses folders already under SOLD — `_property_folders`
  walks SOLD too, as category "SOLD"), `view_listing_photos` (`listing_photos`: downloads through
  the cache, shrinks to 1024 px JPEG with Pillow, EXIF-upright; staff rule: look at the photos and
  know the client's line of business before recommending), and `record_availability` (Deign's
  standing rule: every owner availability answer is recorded). It writes
  `Last availability check (Oct 22, 2026): STATUS. note` as the first line under the separator,
  replacing an earlier check line (`availability_requests`, pure, tested with fake Docs bodies), via
  the **Docs API** (`_docs()`, company account, `drive` scope; docs.googleapis.com was enabled in
  `apg-viber-assistant` on 2026-10-24) with `requiredRevisionId`, so formatting and tabs survive —
  unlike `update_listing_doc`'s whole-text rewrite.
- **Two Google identities.** Reads use the read-only service account
  (`viber-drive-reader@apg-viber-assistant.iam.gserviceaccount.com`, gcloud project
  `apg-viber-assistant`). Service accounts have 0 bytes of storage (uploads failed with
  `storageQuotaExceeded`), so **writes never use it**: `_writer()` signs in the company account
  (thealphapremiergroup@gmail.com) through the OAuth desktop client `google_oauth_client.json`
  (git-ignored; client "Viber MCP" in that project) with the full `drive` scope (`drive.file` can't
  write into the existing APR LISTING folders); token in `%LOCALAPPDATA%\viber-mcp\google_token_write.json`.
  `drive_sign_in(write=True)` does the one-time browser consent. While the consent screen is in
  "Testing" the refresh token expires after 7 days and the next upload reopens the browser.
  `upload_listing` reuses an existing `*<name>` folder/doc and numbers photos after the ones
  already there. Without the client file, `post_new_listing` falls back to returning the plan for
  the Drive connector. Tests patch `CLIENT_FILE`/`WRITE_TOKEN_FILE` away (conftest autouse) — a
  test once opened the real consent page and hung.
- `post_new_listing` (viber_drive.py) validates everything first (separator, photos,
  category/deal[/subfolder] via `pick_folder`, no existing folder of that name — compared by `listing_key`, which ignores `*`, "Brgy." and
  punctuation), opens
  `VIBER_MCP_LISTINGS_GC` (default `APR LISTING ONLY`; `is_listings_gc` tolerates OCR junk like a
  trailing "w"), refuses if the listing is already the latest post, posts the text, confirms it is
  on screen (`find_posted_message`, which strips Viber's U+2068/U+2069 isolate marks — `reply`'s
  `verified` gives false negatives on long texts), then photos → dashed separator, then
  `upload_listing(drive_upload)` (parent folder id, folder name, doc title/text with
  `Added to Drive: <date>`, `AlphaPremier_Photo_NN` names) and `confirm_listing_in_drive`. `confirm_listing_in_drive` checks folder/doc/photo count in Drive,
  then quote-replies `Added to Drive [File name:*<name>]`. A failure after the first GC send returns
  `STOP` with what's done instead of raising — never re-run it.
- Test any tool with `uv run client.py call <tool> '<json>'`; element ids from `list_elements`
  only live within one server process (each `client.py` call starts a new one).
