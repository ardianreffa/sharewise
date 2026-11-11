"""Tool schemas: narrow on purpose. The agent passes check ids, photo numbers and fixed choices, never file paths."""

CHECK = {
    "name": "sharewise_check",
    "description": (
        "Before photos or videos are shared: check ONE folder the user named and report, as yes/no counts only, what they "
        "would reveal (exact location, date and time, device, unique IDs, owner name, captions, hidden thumbnails, depth "
        "maps). Never reports a value: no coordinates, times, devices or text. Use the folder exactly as the user wrote it; "
        "do not widen it or add subfolders unless asked. Changes nothing."
    ),
    "parameters": {"type": "object", "properties": {
        "folder": {"type": "string", "description": "The folder exactly as the user named it."},
        "include_subfolders": {"type": "boolean", "description": "Only true if the user asked for subfolders."}},
        "required": ["folder"]},
}

REVIEW = {
    "name": "sharewise_review",
    "description": (
        "Find faces, people, street names, house or unit numbers and licence plates in the checked photos (on macOS, with "
        "Apple Vision on this computer) and build a local review page per photo. On the page the user switches areas off "
        "or on and draws their own, then copies a selection code back. Give the user the page paths. Detection can miss "
        "things or be wrong: never promise that everything was found. Changes nothing."
    ),
    "parameters": {"type": "object", "properties": {
        "check_id": {"type": "string"},
        "photos": {"type": "array", "items": {"type": "integer"}, "description": "Photo numbers from the check (default: the first 12)."}}},
}

SHARE = {
    "name": "sharewise_share",
    "description": (
        "Make the copies to share, in a Sharewise folder next to the photos: hidden areas are rebuilt from their "
        "surroundings (nothing of the original is kept), and all metadata is removed except copyright and what the user "
        "chose to keep. Every copy is re-read and verified. Pass the user's selection codes from the review pages as given. "
        "You may ADD areas (add_areas) when the user asks or agrees; you cannot switch areas off. Keeping location needs "
        "the user's approval in Hermes. Call it only when the user's current message asks for the copies."
    ),
    "parameters": {"type": "object", "properties": {
        "check_id": {"type": "string"},
        "photos": {"type": "array", "items": {"type": "integer"}, "description": "Photo numbers (default: all)."},
        "keep": {"type": "array", "items": {"type": "string", "enum": ["time", "device", "location"]},
                 "description": "Only what the user explicitly wants to keep. Default: nothing."},
        "selection_codes": {"type": "array", "items": {"type": "string"}, "description": "Codes the user pasted, exactly as given."},
        "add_areas": {"type": "array", "items": {"type": "object", "properties": {
            "photo": {"type": "integer"}, "box": {"type": "array", "items": {"type": "number"}, "description": "[x, y, width, height], 0..1, top-left origin"}}},
            "description": "Extra areas to hide that the user asked for or agreed to."}},
        "required": ["check_id"]},
}

LOOK = {
    "name": "sharewise_look",
    "description": (
        "For a visual check, only when the user asks: returns downsized images of the FINISHED copies (already hidden and "
        "clean) for your vision tool, to spot anything still identifying (a face, a house number, a sign, a badge, a "
        "screen). Never the originals. Looking sends these images to the model provider."
    ),
    "parameters": {"type": "object", "properties": {"check_id": {"type": "string"}}},
}
