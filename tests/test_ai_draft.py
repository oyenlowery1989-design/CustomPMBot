"""Tests for services/ai_draft.py — the Anthropic call wrapper.

Mirrors the MagicMock-chain + monkeypatch.setattr mocking style already used
for the Stellar watcher in tests/test_services.py: the Anthropic client is
never constructed for real, so no network call can happen from this file."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import services.ai_draft as ai_draft
from services.ai_draft import DRAFT_SCHEMA, generate_draft


def _fake_response(payload: dict):
    """Mimics an Anthropic Message whose structured-output text block holds
    the JSON payload generate_draft() parses."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))])


class TestGenerateDraft:
    def test_no_api_key_escalates_without_api_call(self, monkeypatch):
        monkeypatch.setattr(ai_draft, "AI_API_KEY", None)
        fake_get_client = MagicMock()
        monkeypatch.setattr(ai_draft, "_get_client", fake_get_client)

        result = generate_draft("", [], [])

        assert result == {"action": "escalate", "text": "", "reason": "AI draft unavailable"}
        fake_get_client.assert_not_called()

    def test_api_exception_escalates(self, monkeypatch):
        monkeypatch.setattr(ai_draft, "AI_API_KEY", "sk-test-key")
        client = MagicMock()
        client.messages.create.side_effect = Exception("rate limited")
        monkeypatch.setattr(ai_draft, "_get_client", MagicMock(return_value=client))

        result = generate_draft("Be nice", [("in", "help me")], [])

        assert result == {"action": "escalate", "text": "", "reason": "AI draft unavailable"}

    def test_successful_call_returns_parsed_draft_shape(self, monkeypatch):
        monkeypatch.setattr(ai_draft, "AI_API_KEY", "sk-test-key")
        client = MagicMock()
        client.messages.create.return_value = _fake_response(
            {"action": "draft", "text": "Sure, happy to help!", "reason": ""})
        monkeypatch.setattr(ai_draft, "_get_client", MagicMock(return_value=client))

        conversation = [("in", "Can you help me?"), ("out", "Of course, what's up?")]
        canned = [("greet", "Hello and welcome!")]
        result = generate_draft("Be friendly and concise", conversation, canned)

        assert result == {"action": "draft", "text": "Sure, happy to help!", "reason": ""}
        client.messages.create.assert_called_once()
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == ai_draft.AI_MODEL
        assert kwargs["max_tokens"] == 1024
        assert "Be friendly and concise" in kwargs["system"]
        assert "greet" in kwargs["system"] and "Hello and welcome!" in kwargs["system"]
        assert "Can you help me?" in kwargs["messages"][0]["content"]
        assert kwargs["output_config"] == {"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}}

    def test_model_escalate_verdict_passes_through_unchanged(self, monkeypatch):
        """The model itself can decline to draft (e.g. guidelines say 'never
        discuss refunds') — that escalate shape must be returned as-is."""
        monkeypatch.setattr(ai_draft, "AI_API_KEY", "sk-test-key")
        client = MagicMock()
        client.messages.create.return_value = _fake_response(
            {"action": "escalate", "text": "", "reason": "Guidelines say never discuss refunds"})
        monkeypatch.setattr(ai_draft, "_get_client", MagicMock(return_value=client))

        result = generate_draft("Never discuss refunds", [("in", "can I get a refund?")], [])

        assert result == {"action": "escalate", "text": "", "reason": "Guidelines say never discuss refunds"}
