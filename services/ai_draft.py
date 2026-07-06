import json
import anthropic
from config import AI_API_KEY, AI_MODEL, log

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=AI_API_KEY)
    return _client

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["draft", "escalate"]},
        "text": {"type": "string", "description": "The drafted reply text if action=draft, or empty string if action=escalate"},
        "reason": {"type": "string", "description": "Why escalated, if action=escalate; empty string if action=draft"},
    },
    "required": ["action", "text", "reason"],
    "additionalProperties": False,
}

def generate_draft(guidelines: str, conversation: list[tuple[str, str]], canned_responses: list[tuple[str, str]]) -> dict:
    """
    conversation: list of (direction, text) tuples, oldest first, direction is "in"/"out" (matches messages.direction column).
    canned_responses: list of (name, body) tuples for context.

    Both database.messages.db_export_messages() and database.canned.db_canned_list()
    return List[sqlite3.Row] (newest-first for messages), NOT tuples — the caller
    (cb_ai_draft) is responsible for converting before calling this function:
        rows = list(reversed(db_export_messages(user_id, limit=10)))
        conversation = [(r["direction"], r["text"]) for r in rows]
        canned_responses = [(r["name"], r["body"]) for r in db_canned_list()]
    generate_draft() itself takes plain tuples only — it must not import or know
    about sqlite3.Row, keeping it decoupled from the DB layer for easier testing.

    Returns {"action": "draft"|"escalate", "text": str, "reason": str}.
    On ANY exception (auth, network, rate limit, bad response), returns
    {"action": "escalate", "text": "", "reason": "AI draft unavailable"} and logs the real error —
    a provider outage must never block the relay or crash a handler.
    """
    if not AI_API_KEY:
        return {"action": "escalate", "text": "", "reason": "AI draft unavailable"}

    try:
        system_parts = []
        if guidelines:
            system_parts.append(f"Guidelines:\n{guidelines}")
        if canned_responses:
            canned_text = "\n".join(f"- {name}: {body}" for name, body in canned_responses)
            system_parts.append(f"Canned responses (for reference/context):\n{canned_text}")
        system = "\n\n".join(system_parts)

        conversation_text = "\n".join(f"[{direction}] {text}" for direction, text in conversation)
        user_content = f"{conversation_text}\n\nDraft a reply to the user's most recent message, or escalate."

        response = _get_client().messages.create(
            model=AI_MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_config={"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
        )

        text_block = next(block for block in response.content if block.type == "text")
        return json.loads(text_block.text)
    except Exception as e:
        log.error("AI draft generation failed: %s", e)
        return {"action": "escalate", "text": "", "reason": "AI draft unavailable"}
