---
name: inbox-triage
description: Summarize the Viber inbox — check unread chats, read recent messages and photos, record owner availability answers in Drive, and list who needs a reply without sending anything.
---

Check the Viber inbox with `list_chats(unread_only=True)` (`scroll_chat_list` for more; `list_chats()` shows every chat with `last_from` = who wrote last). For each chat with unread messages, open it (`search_and_open`), read the recent messages (`list_messages`, `open_image` for photos — then `close_viewer`), and summarize: who, what they need, how urgent, and whether the client has the last word. Don't reply to anyone and don't `mark_as_read` unless asked. If an owner (or the call staff) answered about a listing's availability, record it in the listing doc with `record_availability` (`find_listings` for the folder). End with a short prioritized list of suggested replies to approve.
