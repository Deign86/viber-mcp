---
name: property-details
description: Find a property listing in Google Drive, verify the client's line of business and owner availability, and send the client-facing listing text and photos to a Viber chat.
---

Open the target Viber chat (`search_and_open`; if `low_confidence` or several similar chats, ask which) and `backread_chat` for the client's requirements and line of business (what the space is for). If the line of business isn't known, ask the client in one short line and stop there. Then `find_listings` for the requested property (if several match, ask which), read it with `get_listing` and look at its photos with `view_listing_photos` to check it fits their business. If the owner hasn't confirmed availability yet, follow the operating rules (short teaser, ask the owner, `record_availability` with the answer) instead of sending. Once confirmed, run `send_listing` for that `folder_id` with `expect_chat` set to the chat name so it refuses if another chat took over. Never send, quote or summarize the internal part of the doc below the dashed line. Report what was sent and whether the text and photos were verified.
