---
name: draft-reply
description: Draft (without sending) a reply in a Viber chat matching the conversation's language and tone, placing it in the message box with put_draft or reply_to_message(send=False) for review.
---

Open the target Viber chat with `search_and_open` (if `low_confidence` is true or several similar chats match, stop and ask which). Read the recent messages with `list_messages` (`open_image` for photos, then `close_viewer`), then write a reply in the chat's language and tone. If the reply recommends a listing, first make sure you know the client's line of business (if not, the reply asks for it) and look at the listing's photos with `view_listing_photos`. Show the reply and what it answers. Put it in the message box with `put_draft` (passing `expect_chat`) — or, if it answers a specific earlier message, with `reply_to_message(n, text, send=False, expect_chat=...)` so it is quoted. Do NOT send.
