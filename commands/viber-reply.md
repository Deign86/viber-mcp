---
description: Read the currently-open Viber chat and draft a reply into the compose box (not sent)
---

Draft a reply to the Viber conversation that is currently open on screen.

Steps:
1. Call the `read_open_chat` MCP tool to OCR the visible conversation.
2. Identify the most recent incoming message(s) and the thread's context, language, and tone
   (the user is in the Philippines — match Taglish/English/Filipino as the chat uses it).
3. Compose a natural, appropriately concise reply **as the user** (first person, their voice).
   - If the user gave guidance in the arguments below, follow it for content/tone/length.
   - Otherwise infer a sensible reply from context.
4. Show me the drafted reply text and a one-line summary of what you're replying to.
5. Call the `put_draft` MCP tool with that reply to paste it into the open chat's compose box.
   Do NOT send it — the user reviews and presses send themselves.
6. Tell the user the draft is in Viber's compose box, ready to review and send.

Important:
- Never call `send_message` for this command — drafting only, the user sends.
- OCR only sees on-screen text; if the conversation is unclear or empty, say so and ask the user
  to scroll the relevant messages into view rather than guessing.

User guidance for this reply (optional): $ARGUMENTS
