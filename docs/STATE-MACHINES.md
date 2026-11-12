# State machines

The explicit and implicit state machines of viber-mcp, what must always hold for each, and the
invalid states the code could reach. Written by a state-machine bug hunt on 2026-11-06; keep it
in step with the code: when a fix changes a transition, change its row here and the issue's status.

**Concurrency, measured:** with mcp 1.28 every tool is a sync function that FastMCP calls on the
event-loop thread (`func_metadata.py`: `return fn(**…)`), so **two tool calls never run at once**
and a tool that blocks blocks the whole server. The only concurrency is the server's own threads:
start-up `prune_downloads` and `request_access` (`server.py:797-799`), the download pool
(`viber_drive.py` `download`), foreground guards (`hold_foreground`, `open_url`'s hand-back,
`_keep_viber_behind`) and the Windows UIA and OCR workers (`backends/windows.py` `_Worker` 56).

Impact scale: **1** data to the wrong person, data loss, internal info leaked · **2** stuck, or
wrong data · **3** recoverable glitch · **4** cosmetic.

---

## 1. Which chat is open (navigation and confirmation)

`viber_send.py`: `open_chat` 332, `_wait_for_sendable_chat` 252, `search_and_open` 816,
`header_title_key` 197, `_chat_before_navigating` 212, `chat_confirmed` 221, `_title_is` 241.

The open chat is state Viber owns; we only observe it through the header band OCR. Implicit
variables per navigation: `previous` (title of the chat open before), strong proof (number in
the header, unsaved-number banner, title = name), `changed` (title differs from `previous`),
elapsed time.

**Invariant:** a chat is accepted only on strong proof, or after the 4 s settle when the title
*known* before navigating has changed to another readable title.

| From | Event | Guard | To | Code |
| --- | --- | --- | --- | --- |
| any | `open_chat(to)` | | Navigating (`previous` = title row, or None if unreadable/blank) | 332-347 |
| Navigating | OCR read | "isn't on Viber" / "cannot add yourself" | Error | 279-282 |
| Navigating | OCR read | strong proof | Confirmed | 283-284 |
| Navigating | OCR read, ≥4 s | `previous` known ∧ title readable ∧ ≠ `previous` | Confirmed (settle) | 285-288 |
| Navigating | 12 s | `previous` None | Refused "couldn't confirm…" | 290-295 |
| Navigating | 12 s | not changed | Refused "didn't change" | 296-301 |

**Invalid states found:** *Confirmed while the link never navigated.* Fixed in issue I1.

## 2. Open-chat writers (the `expect_chat` guard)

Every writer that acts on "the open chat" (`reply`, `paste_into_open_chat`, `reply_to_message`,
`send_files`, `send_listing`) takes `expect_chat`; `require_open_chat` (154) refuses unless the
header is that chat. **Invariant:** a code path that knows which chat it means always passes it.
Before I2, two code paths didn't: the client-GC flow and `post_new_listing`.

## 3. Client GC run

`viber_gc.py`: `create_client_gc` 201, `_create_client_gc` 223, `_mark` 67, `_in_gc` 386,
`_reopen_gc` 397; `viber_ui.create_group_from_chat` 1243, `_rename_open_chat` 1001,
`send_with_mention` 1358.

Persisted per template (key = sha1 of the normalised text) in `DATA_DIR/gc_progress.json`:
`steps ⊆ {intro, group=<name>, template, listing, mention}`, kept 7 days, deleted after DONE.
In memory: `_RUN_LOCK` (one run at a time).

**Invariants:** the intro is sent at most once; one GC per client; every post after Save goes
into that GC and nowhere else; a rerun continues, never repeats.

| From | Event | Guard | To | Side effects | Code |
| --- | --- | --- | --- | --- | --- |
| Planned | run | client shares a group ("Groups in common") | EXISTING CLIENT, nothing sent | | 258-270 |
| Planned | run | intro not done ∧ not in the 1:1 chat | IntroSent | `send_message`, mark intro | 271-282 |
| IntroSent | Save in Add participants | group appears | GroupMade | mark group=name | 293-310 |
| GroupMade | rename fails / privacy blocked | | Stopped (GCError) | | 311-326 |
| GroupMade | post template | header is the GC | TemplatePosted | mark template | 333-335 |
| GroupMade… | post template / listing / mention | header is another chat | Stopped (GCError), nothing posted | | `_in_gc` 386 |
| TemplatePosted | send_listing_link(expect_chat=GC): website page exists → one "click the link" message; else (404 / unreachable) → send_listing (Drive text + photos), `way` says which | | ListingSent | mark listing | 336-344 |
| ListingSent | mention | header is the GC | Mentioned | mark mention | 351-362 |
| Mentioned | DONE quote-reply | | Done | forget progress | 365-374 |
| any Stopped | rerun | group marked | resumes in `_reopen_gc(name)` | skips marked steps | 228-231 |

**Invalid states found:** *client details posted into another chat* (I2, fixed).

## 4. Batch processing of templates

`viber_gc.process_client_templates` 510. A template's identity is its **text**; `n` (1 = newest
visible message) is only a screen position and moves with every new post and every reopen of the
chat. **Invariant:** the GC made is the template's that was found. Broken before I3.

## 5. New listing post (listings GC + Drive)

`viber_drive.post_new_listing` 1051, `upload_listing` 776, `confirm_listing_in_drive` 1109,
`add_listing_to_drive` 1004, `_check_new_listing` 655, `_create_once` 753, `_listing_doc` 533. No progress file: the GC itself and the
folder/doc/photo count in Drive are the record.

| From | Event | Guard | To | Code |
| --- | --- | --- | --- | --- |
| Validated | open listings GC | header is the GC, not the latest post already | GCOpen | 1071-1074 |
| GCOpen | post text (expect_chat=GC) | shows in the GC | TextPosted | 1079-1084 |
| TextPosted | photos, separator (expect_chat=GC) | | Posted | 1085-1089 |
| any of these | failure | | STOP "don't re-run" | 1090-1093 |
| Posted | upload_listing | | Uploaded | 1101 |
| Uploaded | confirm (≥ N photos, doc, one folder) | | Confirmed + "Added to Drive" quote-reply | 1102 |
| Posted/Uploaded | failure | | STOP "re-run add_listing_to_drive" | 1103-1105 |
| STOP (Drive) | `add_listing_to_drive` | the listing's folder (same `listing_key`) is in the parent | reuses it by id, the `*<name>` doc if there, uploads only photos not there (md5) | 776-816 |

**Invariants:** a listing is posted once; the internal part reaches only the listings GC; a
Drive re-run converges (no duplicate folder, doc or photo). Broken before I2 (internal text to
another chat), I5 (re-run duplicated photos) and I11 (a second folder).

## 6. Extension auto-update

`viber_update.py`: `update_and_restart` 278, `latest_version` 107, `install` 202, `_put_back`
237, `_install_latest` 251, `_restart` 261. On disk: `manifest.json` version = the installed
version; `DATA_DIR/update_check.json` caches the check for 1 h. In memory: modules imported
*after* `update_and_restart` returns (`server.py:15-27`) are read from the folder then.

| From | Event | Guard | To | Code |
| --- | --- | --- | --- | --- |
| Start | check | git checkout / off / `VIBER_MCP_UPDATED` | Run current | 281 |
| Start | check | newer release | Downloading | 283-284 |
| Downloading | bad bundle / network / CRC | | Run current, nothing changed | 251-258 |
| Staging | every file written as `.updating` | | Swapping | 211-218 |
| Swapping | rename fails | | rolled back, Run current | 219-226, 237 |
| Swapping | all renamed, manifest last | | Installed → child server on the same stdio | 284-285, 261 |
| Swapping | process killed | | old manifest: next start installs again | |

**Invariant:** the folder the server imports from is one version, never a mix. Broken before I4.

## 7. Viber app lifecycle

`viber_app.py`: `ensure_running` 184, `_bring_up` 75, `_quit` 106, `start` 157, `restart` 175.
States: NotRunning · Launching · Up (main screen) · NotUp (splash/sign-in/update) · Hung ·
Updating (installer running, ≤ `UPDATER_GRACE`). `_LOCK` serialises start/quit with the
before-tool check. No invalid reachable state found in the domain layer; the platform side had I9.

## 8. Google sign-in and service caches

`viber_drive.py`: `_save` 80 (temp file + `os.replace`), `_saved_creds` 93, `_browser_sign_in`
117 (`_SIGN_IN` lock), `_creds` 149, `_still_signed_in` 166, `_drive` 189 (per thread), `_writer`
200, `_docs` 214, `sign_in_at_start` 227. Per token file: absent · valid · expired-refreshable · dead · corrupt.

| From | Event | To | Code |
| --- | --- | --- | --- |
| cached service, token expired | next call | refreshed in place, same service | 166-180 |
| cached service, refresh fails (dead) | next call | service rebuilt → `_creds` → browser sign-in | 189-221 |
| no token | read tool, no service-account key | browser sign-in (readonly) | 159-163 |
| waiting on the start-up upload sign-in | lock free | that sign-in serves the read, no second page | 131-137 |

**Invariant:** a dead sign-in leads to a new sign-in, not a stuck error (I6); one consent page at
a time, and a token file is whole (I13).

## 9. Listing photo cache

`viber_drive._fetch` 319, `prune_downloads` 1163 (`_CACHE_LOCK`): per Drive file id, absent → in memory →
`.part` → final name (whole, reused without a metadata call, mtime refreshed on reuse) → pruned
after `VIBER_MCP_KEEP_DOWNLOADS_DAYS`. A file a send is using is never pruned (reuse and prune
share `_CACHE_LOCK`), a crash never leaves a half file under its final name, each id is fetched
once per download, and listing docs aren't cached (I14).

## 10. macOS backend: front hand-off and typing target

`backends/macos.py`: `_with_viber_front` 994 (`_handoff` lock), `_keep_viber_behind` 234 (also
`hold_foreground` 1231), `_live_pid` 230, `open_url` 422, `click_at` 949, `type_into_focus` 1176,
`paste_files` 1237, `focus` 399.
Typing target `_focus_el`: None · the field Viber really focuses · a field Viber no longer
focuses · gone. **Invariants:** real input only while Viber is in front; the user's app gets the
front back, and only Viber's activation is undone (I15); characters go only to the field last
clicked, never to one that's gone (I7, I16); Viber is started only by `viber_app`, and the
user's clipboard is theirs (I10).

## 11. Windows backend: UIA worker, caches, foreground guards

`backends/windows.py`: `_Worker` 56 (one daemon thread), `_ocr_lines` 126 and `_on_uia` 168
(abandoned after 40 / 30 s, `_abandon_uia_thread` 155), `_uia_write_keeping_focus` 312, `_value`
633 (`_target_node` cache), `type_into_focus` 842 (`_last_click`), `_restore_foreground` 294
(`_is_viber_window` 286), `_viber_pids` 362. **Invariants:** a cached node is the field it was
taken from; a write never touches another app; nothing blocks forever, or holds up the exit;
only Viber's own activation is undone (I7-I9, I17).

---

## Issues

Each groups bugs by root cause. Evidence = the trace, plus the regression test that reached the
invalid state before the fix.

### I1. Chat confirmation trusted an unknown or volatile "previous chat" (impact 1). Fixed.

- *Unknown counted as changed:* an `OCRError` on the header read before `open_url` set
  `previous=None`, and `_wait_for_sendable_chat` started with `changed = previous is None`. When
  the `viber://` link didn't navigate, the chat still open was accepted after 4 s and sent to.
- *Blank read:* an empty read before (window not drawn) or after made `changed` true the same way.
- *Volatile key:* the key was the whole header band, subtitle included. "Active" → "Last seen
  today at 3:41 PM" (live fixtures in `tests/test_right_chat.py`) made the same chat count as
  a new one.
- **Fix:** identify the chat by its title row only (`header_title_key`); unreadable or blank =
  unknown = strong proof required; a blank read after never counts as changed.
- **Tests:** `test_open_chat_needs_proof_when_the_chat_it_came_from_couldnt_be_read`,
  `test_open_chat_refuses_the_same_chat_when_only_its_last_seen_line_changed`,
  `test_header_title_key_is_the_title_row_only`.
- Trade-off: right after Viber starts with no chat open, a saved contact opened by number (no
  number in its header) is refused once; the retry works.

### I2. Code paths posted to "the open chat" without naming it (impact 1). Fixed.

- **GC flow:** after Save and the rename, the client's template, the listing and the @mention
  went to the open chat unchecked (`viber_gc.py`). A new post switching the open chat is on
  record (2026-10-22, APR LISTING ONLY mid-rename).
- **post_new_listing:** the listings GC was checked once. The full text, including the internal
  part (owner/agent numbers), then the photos, the separator and the "Added to Drive" reply all
  went to the open chat.
- **Fix:** every post passes `expect_chat` (the GC's name / `LISTINGS_GC`). In the GC flow a
  refusal becomes a GCError, and the rerun resumes. `_title_is` lets "~" in a placeholder GC
  name match the 0-2 characters OCR makes of it ("-", "e").
- **Tests:** `test_gc_posts_go_only_into_the_gc_it_made`,
  `test_a_placeholder_gc_name_matches_its_header_however_ocr_reads_the_tilde`,
  `test_new_listing_posts_nothing_more_once_another_chat_is_open`.

### I3. The batch GC run re-read its template by position (impact 1). Fixed.

- `process_client_templates` found the template by text (n=2 when scrolled up), planned it, then
  called `create_client_gc(n=2)`, which read message 2 again.
- Planning's `gc_exists` can open a search result, after which the templates chat is reopened
  at its newest messages; a new post shifts `n` too.
- The other template's client got the intro and a GC. Later the right template was answered
  "existing client".
- **Fix:** the template's text is passed through (`template_text`); `n` is not read again.
- **Test:** `test_batch_run_makes_the_gc_for_the_template_it_found_not_whatever_is_at_n_now`.

### I4. A failed update install left a half-old, half-new server running (impact 2). Fixed.

- `install` replaced files one by one. A failure part-way (a file held open by an antivirus
  scan, a full disk) returned to `update_and_restart`, and `server.py` then imported the rest
  of its modules from that mix.
- A corrupt member raised `zipfile.BadZipFile`, which wasn't caught, so the server didn't start.
- **Fix:** stage every file first, then swap with the old bytes kept and roll back on failure;
  catch `BadZipFile`.
- **Tests:** `test_an_install_cut_short_puts_the_old_version_back`,
  `test_a_corrupt_file_in_the_download_keeps_the_running_version`.

### I5. The prescribed Drive re-run duplicated the listing's photos (impact 2). Fixed.

- After a Drive-side STOP, `add_listing_to_drive` reused the folder and uploaded every photo
  again, numbered after the existing ones: 2N photos when only the confirm had failed.
- `confirm` checks `>= N`, so it passed, and `send_listing` sent the duplicates to clients.
- **Fix:** skip local photos whose md5 is already in the folder.
- **Test:** `test_rerun_upload_skips_the_photos_already_in_the_folder`.

### I6. A dead Google sign-in stayed cached until restart (impact 2). Fixed.

- `_writer_service`, `_docs_service` and the event-loop thread's read service were built once.
- After the refresh token died (revoked, or 7 days while the consent screen is "Testing"),
  every call failed with RefreshError. That included `drive_sign_in(write=True)`, which
  `check_setup` prescribes.
- **Fix:** each cached service keeps its credentials and is rebuilt (signing in again) when a
  refresh fails.
- **Test:** `test_a_dead_sign_in_is_asked_again_instead_of_failing_every_call`.

### I7. Typing went to a field other than the one last clicked (impact 1). Fixed.

- **Windows:** `type_into_focus` with emoji or a newline wrote through
  `ControlFromPoint(last click)`. That point is on the desktop, so with Viber covered (the
  usual case) `SetValue(Value + text)` rewrote a field in the user's own window.
  `click_compose`/`click_search` didn't update the point, so a stale one could pick another field.
- **macOS:** a real click on a field the hit test misses (the search box while a search is
  open) left `_focus_el` on the field clicked before. `type_into_focus` re-focused that field
  and typed the query into the open chat's message box, where an Enter sends it.
- **Fix:** Windows takes the field under the click from Viber's own UIA tree, and `_post_click`
  records its point too. macOS adopts the text field that has Viber's focus after a real click
  (a mention row or picker circle leaves the focus where it was, so that case is unchanged).
- **Tests:** `test_emoji_typing_writes_into_vibers_field_not_whatever_window_covers_it`
  (`tests/test_windows_backend.py`), `test_a_real_click_on_another_field_moves_typing_there`
  (`tests/test_macos_input.py`). They run on the Windows and macOS CI jobs.

### I8. The Windows UIA worker could hang the server or write stale state (impact 2). Fixed.

- `_uia_write_keeping_focus` waited for its write with no deadline. A frozen Viber kept the
  tool, and so the whole server, stuck, and snapped the foreground back every 20 ms.
- The worker itself stored `self._target_node`, so a worker abandoned after 30 s could finish
  later and replace the field a newer call had located.
- **Fix:** the same 30 s deadline and abandonment as `_on_uia` (`_abandon_uia_thread`), and
  the node is stored on the caller's side.
- **Tests:** `test_a_uia_write_viber_never_answers_gives_up_after_30_s`,
  `test_an_abandoned_uia_read_never_replaces_the_field_a_later_call_located`.

### I9. Another Windows user's Viber counted as running (impact 2). Fixed.

- `_viber_pids` counted every user's Viber.exe, so `ensure_running`/`start_viber` waited for a
  window this desktop can't see, and `restart_viber` failed "still running".
- **Fix:** this user's processes only (`_own_viber_processes`).
- **Test:** `test_another_windows_users_viber_doesnt_count_as_running`.

### I10. The macOS backend overrode the user's own choices (impact 2). Fixed.

- With "Start Viber automatically" off, `focus()`'s `open -g -b` / `open -b` fallback launched
  and activated a Viber that wasn't running. That bypassed `viber_app`, the setting and the
  wait for an update to finish.
- `paste_files` put its clipboard snapshot back unconditionally, over anything the user copied
  during the up-to-10 s wait.
- **Fix:** `focus()` raises "not running" instead of launching. The snapshot is restored only
  while the pasteboard's `changeCount` is still ours.
- **Tests:** `test_focus_doesnt_start_viber_when_it_isnt_running`,
  `test_paste_files_keeps_what_the_user_copied_while_viber_took_the_files`,
  `test_paste_files_gives_the_user_their_clipboard_back`.

### I11. add_listing_to_drive made a second folder beside the listing's own (impact 2). Fixed.

- The clash check matched the listing by `listing_key` (which ignores `*`, "Brgy." and
  punctuation) and let a same-parent folder through as "it gets reused". The upload then looked
  for the exact `*<name>`, so a hand-made "X" got a second folder "*X" beside it, and
  `confirm_listing_in_drive` found 2 on every try.
- **Fix (decided: reuse the folder):** `_check_new_listing` returns that folder
  (`existing_folder`; a `*<name>` one wins if several, refused if still ambiguous).
  `upload_listing` uploads into it by id, under its own name. `confirm_listing_in_drive` falls
  back to `listing_key` when no folder has the exact name.
- **Test:** `test_add_listing_to_drive_reuses_the_hand_made_folder_of_that_listing`.
- A reused (or any existing) folder keeps its one doc, whatever it's named; see I18.

### I12. update_listing_doc overwrote edits made since the doc was read (impact 2). Fixed.

- It rewrote the whole doc through Drive with no precondition. A coworker's edit, or
  `record_availability`'s line, made after `get_listing` read the doc was lost.
- **Fix (decided: edit through the Docs API):** `get_listing(include_internal=True)` returns
  `doc_revision`, read as the upload account before the text (a revision id only holds for the
  account that read it, and that's the account `update_listing_doc` writes with). Without that
  sign-in it's None, and no sign-in page opens for a read.
- `update_listing_doc(folder_id, text, doc_revision)` refuses when the doc is no longer at that
  revision. Otherwise it replaces the text with `batchUpdate` and `requiredRevisionId`, so an
  edit between its own read and write is refused too.
- **Tests:** `test_update_listing_doc_replaces_the_text_only_if_the_doc_is_as_it_was_read`,
  `test_a_revision_is_only_read_for_a_signed_in_upload_account`.

### I13. Google sign-in: a second consent page, a token file that could break (impact 3). Fixed.

- **D7:** at start, `sign_in_at_start` holds `_SIGN_IN` while the upload consent page is open.
  A read tool (no service-account key) waited behind it, then re-checked only the read token
  and opened a second, read-only page. The re-check in the lock now accepts the upload sign-in
  for reads (it covers them).
- **D8:** `_save` rewrote the token in place. A write cut short, or two of the parallel
  download threads saving a refresh at once, left a file that no longer parses ("not signed
  in"). It now writes a temp file per thread and `os.replace`s it.
- A dead token with no OAuth client file said "ask for the service account key" even when the
  key was there. It now says the saved sign-in expired and needs the client file.
- **Tests:** `test_a_read_waiting_on_the_start_up_upload_sign_in_uses_it`,
  `test_a_token_save_cut_short_keeps_the_sign_in_there_was`,
  `test_an_expired_sign_in_without_the_client_file_says_so`.

### I14. The Drive cache, folder tree and listing doc could serve the wrong thing (impact 1-3). Fixed.

- **Duplicate ids** in one `download` made two threads write the same `.part` (one rename
  failed, or mixed writes were renamed into place as "whole"). Each id is fetched once now.
- **Prune vs. reuse:** a prune that read a photo's age just before a send reused it deleted it
  under that send, and a `.part` renamed mid-prune made it raise. Reuse (`_fetch`) and prune
  (per file: age check and delete) now share `_CACHE_LOCK`, and a vanished file is skipped.
- **Stale .docx:** a `.docx` listing went through the photo cache, keyed by file id and
  refreshed on every reuse, so an edited one (same id) was read old forever. It's now read
  straight from Drive (`_media_bytes`).
- **Tree cache:** the duplicate check used a tree up to 5 minutes old, so a listing another PC
  just added could be posted twice. It now reads the tree fresh. A move (or an upload) that
  failed after Drive had done it kept the old tree; it's now forgotten whatever happens.
- **Listing doc, impact 1:** with several docs, `get_listing` took the first by name, so an
  internal-notes doc could be split and its "client part" sent. It now refuses (I18: one doc
  per listing folder).
- **Retried creates:** `execute(num_retries=3)` re-sent a create blind after a 500 or a
  dropped connection, and Drive may already have made the file, so a second folder, doc or
  photo appeared. `_create_once` looks for the file before sending again.
- **Tests:** `test_the_same_file_twice_in_one_download_is_fetched_once`,
  `test_prune_waits_for_a_reuse_in_progress_and_keeps_the_file`,
  `test_prune_skips_a_file_that_goes_while_it_looks`,
  `test_an_edited_docx_listing_is_read_fresh_not_from_the_photo_cache`,
  `test_the_duplicate_check_reads_the_folder_tree_afresh`,
  `test_a_move_that_fails_forgets_the_folder_tree`,
  `test_get_listing_refuses_to_guess_which_doc_is_the_listing`,
  `test_a_reused_folder_keeps_its_own_listing_doc`,
  `test_a_create_whose_answer_was_lost_isnt_made_twice`.

### I15. macOS hand-offs could leave Viber in front or pull the user back (impact 2). Fixed.

- `_with_viber_front` asked Viber forward and, when that didn't land in time, raised before
  its `try`. The late activation then kept the front, and the user's typing went to Viber. The
  hand-back now runs on every path.
- The hand-back lands asynchronously. A second hand-off right after (Down, then Tab, in a
  mention) read Viber as the front app, skipped bringing it forward, and sent its key once the
  user's app was back. A hand-off now returns only when the user's app is in front again.
- `hold_foreground` handed the front back whenever anything but `prev` had it, so an app the
  user switched to within 2-4 s was taken away. It now acts only when Viber has the front
  (`_keep_viber_behind`). `open_url` compares with the live pid, not a stale `_pid`.
- **Tests:** `test_a_hand_off_that_fails_still_gives_the_front_back`,
  `test_a_hand_off_returns_only_once_the_user_app_is_back`,
  `test_the_hand_back_guard_leaves_an_app_the_user_switched_to`.

### I16. macOS: typing into a gone field, a revoked grant (impact 2). Fixed.

- A `_focus_el` that no longer answers AX (the page was rebuilt) switched the focus check off:
  characters, or real edit keys, went to whatever Viber had focused. `type_into_focus` and the
  edit keys now refuse ("click it again").
- Accessibility was asked once and cached as trusted. Revoked mid-run, every AX read then
  failed as "not found (is a chat open?)". `_app()` now asks every time (it's cheap).
- **Tests:** `test_typing_into_a_field_that_is_gone_is_refused`,
  `test_a_revoked_accessibility_grant_is_noticed`.

### I17. Windows: guards fought the user; stuck workers lingered (impact 2-3). Fixed.

- `_restore_foreground` (open_url's guard, `hold_foreground`, the guarded UIA write) took the
  foreground back from whatever had it, including a window the user Alt-Tabbed to. It now
  acts only when a Viber.exe window has it (`_is_viber_window`).
- A hung OCR call kept the single OCR thread: every later `observe` queued behind it and
  timed out too. It's abandoned like the UIA thread (`_ocr_lines`), with the engine it owns.
- `ThreadPoolExecutor` threads are joined at interpreter exit, so an abandoned stuck worker
  kept the server process from ending. Both workers are now one daemon thread each
  (`_Worker`). An abandoned UIA thread's cache request is dropped too, so the fresh thread
  never calls a COM object bound to the stuck one.
- **Tests:** `test_the_foreground_guards_only_take_it_back_from_viber`,
  `test_a_hung_ocr_read_is_abandoned_for_a_fresh_thread`,
  `test_an_abandoned_worker_never_holds_up_the_servers_exit`.

### I18. One doc per listing folder, with the latest info (staff rule). Fixed.

- A listing folder holds exactly one doc, and that doc carries the latest info. The code didn't
  follow that rule:
  - `get_listing` chose among several docs by a `*` (which may well be an old copy).
  - `upload_listing` added a `*<name>` doc beside an existing folder's doc when that doc wasn't
    starred, so the folder then had two.
  - `update_listing_doc` and `record_availability` counted Google Docs only, so a Google Doc
    plus a `.docx` passed and the Google Doc got edited.
- **Fix:** `_listing_doc` is the one rule: exactly one doc (any name); with several, nothing is
  picked, and the message asks staff to merge what's current into one. `upload_listing` keeps an
  existing folder's doc, whatever it's named, and says so (`note`: update it if the post is
  newer). It adds nothing to a folder that already has several docs. `confirm_listing_in_drive`
  checks for that one doc. In-place edits (`_editable_doc`) need it to be a Google Doc.
- **Tests:** `test_a_listing_folder_has_one_doc_and_more_are_refused`,
  `test_doc_edits_follow_the_one_doc_rule`,
  `test_an_existing_folder_keeps_its_one_doc_whatever_its_name`,
  `test_an_existing_folder_with_several_docs_gets_nothing_added`.

### I19. macOS: a real key went to Finder after a click on the desktop (impact 2). Fixed.

- `foreground()` read window order only. A click on the desktop makes Finder active while
  Viber's window stays topmost, so it said Viber (measured 2026-11-07: Viber's `AXFrontmost`
  False, Finder's True). `_with_viber_front` then skipped bringing Viber forward and its real
  key (Escape, Return) went to Finder.
- **Fix:** `foreground()` is the active app: the first window owner, topmost first, whose
  `AXFrontmost` is True (else another regular app's, else the topmost window's owner).
  `_with_viber_front` requires both before real input (`_viber_in_front`: Viber owns the
  topmost window and is active) and hands the front back to the app that was active.
  Checked live: from the desktop-click state the hand-off made Viber active, then Finder got
  the front back.
- **Tests:** `test_the_front_app_is_the_active_one_not_the_topmost_window`,
  `test_a_hand_off_needs_viber_active_not_just_its_window_on_top`.

### Checked, not bugs

- macOS `_asked_ax` / `_asked_screen`: the flag is set before the prompt call, so a duplicate
  prompt needs a thread switch between two bytecodes.
- macOS `_cg_hangs` stays set for the run on purpose: each retried hung capture would keep
  another thread.
- macOS late paste: the clipboard is restored only after Viber shows the files. A paste that
  times out is caught by `send_files`' "a preview is already open" check, and the next send
  clears the message box first.
- `_keep_viber_behind` pushes Viber back for 20 s after an automatic start, even if the user
  brings it forward then. That's the documented trade-off (CLAUDE.md, "front app").
- Windows `_set_value` uses the cached node without a fallback: `_value` has just
  re-validated it in both callers.
