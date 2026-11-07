---
name: unsend
description: Find and unsend (delete for everyone) one of our own sent messages in a Viber chat, verifying that it was removed.
---

Open the target Viber chat with `search_and_open`, find our message matching the user's description with `list_messages`, and unsend it with `unsend_message`. If several of our messages match, stop and ask which one before deleting. Report which message was unsent and whether it was verified.
