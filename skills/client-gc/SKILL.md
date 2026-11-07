---
name: client-gc
description: Create client group chats on Viber from templates posted in APG CLIENTS — either for all pending templates without a DONE reply or for one named client.
---

If the user named a specific client, open `APG CLIENTS` (`search_and_open`), find the template for that client with `list_messages`, and call `create_client_gc` with its `n`. Report each step's result; if it fails, report what failed and don't retry.

Otherwise, call `process_client_templates` to make the client group chats for every `APG CLIENTS` template without a `DONE-` reply. Report each GC created (name, whether the listing and DONE reply went through) and anything skipped or failed, with the reason.
