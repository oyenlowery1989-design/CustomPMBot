"""Tests for the Telegraph (Instant View) manual publisher."""
import json
from unittest.mock import MagicMock

import services.telegraph as telegraph_mod
from services.telegraph import md_to_nodes, publish_manual
from database.settings import db_get_setting, db_set_setting
from handlers.admin import cmd_manual
from tests.conftest import make_bot, make_context, make_message, make_tg_user, make_update

ADMIN_ID = 1000


class TestMdToNodes:
    def test_headings_mapped_to_h3_h4(self):
        nodes = md_to_nodes("# Title\n## Section\n### Sub")
        assert [n["tag"] for n in nodes] == ["h3", "h3", "h4"]
        assert nodes[0]["children"] == ["Title"]

    def test_paragraph_lines_joined(self):
        nodes = md_to_nodes("first line\nsecond line\n\nnew para")
        assert len(nodes) == 2
        assert nodes[0]["children"] == ["first line second line"]

    def test_inline_bold_code_link(self):
        nodes = md_to_nodes("use **bold** and `code` and [docs](https://x.y)")
        children = nodes[0]["children"]
        tags = [c["tag"] for c in children if isinstance(c, dict)]
        assert tags == ["b", "code", "a"]
        link = [c for c in children if isinstance(c, dict) and c["tag"] == "a"][0]
        assert link["attrs"]["href"] == "https://x.y"

    def test_bullets_grouped_into_ul(self):
        nodes = md_to_nodes("- one\n- two\n\ntext")
        assert nodes[0]["tag"] == "ul"
        assert len(nodes[0]["children"]) == 2
        assert nodes[0]["children"][0]["tag"] == "li"

    def test_table_becomes_bullets_without_separator(self):
        md = "| Cmd | Meaning |\n|---|---|\n| /ban | ban user |"
        nodes = md_to_nodes(md)
        assert nodes[0]["tag"] == "ul"
        items = nodes[0]["children"]
        assert len(items) == 2  # header + row, separator dropped
        assert "Cmd — Meaning" in items[0]["children"][0]

    def test_fenced_code_to_pre(self):
        nodes = md_to_nodes("```\npip install x\npython bot.py\n```")
        assert nodes[0]["tag"] == "pre"
        assert "pip install x\npython bot.py" in nodes[0]["children"][0]

    def test_hr_and_blockquote(self):
        nodes = md_to_nodes("---\n> quoted")
        assert nodes[0]["tag"] == "hr"
        assert nodes[1]["tag"] == "blockquote"

    def test_real_manual_converts(self):
        with open(telegraph_mod._manual_path(), encoding="utf-8") as f:
            nodes = md_to_nodes(f.read())
        assert len(nodes) > 50
        assert json.dumps(nodes)  # serializable
        assert all(isinstance(n, dict) and "tag" in n for n in nodes)
        # Telegraph rejects h1/h2 and tables — must not appear
        assert not any(n["tag"] in ("h1", "h2", "table") for n in nodes)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Records Telegraph API posts, returns scripted responses."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None):
        self.calls.append((url, data))
        return _FakeResponse(self.responses.pop(0))


def _install(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(telegraph_mod.httpx, "AsyncClient", lambda **kw: client)
    return client


class TestPublishManual:
    async def test_first_publish_creates_account_and_page(self, monkeypatch):
        client = _install(monkeypatch, [
            {"ok": True, "result": {"access_token": "TOK"}},
            {"ok": True, "result": {"path": "Manual-01-01", "url": "https://telegra.ph/Manual-01-01"}},
        ])
        url = await publish_manual()
        assert url == "https://telegra.ph/Manual-01-01"
        assert db_get_setting("telegraph_token") == "TOK"
        assert db_get_setting("telegraph_path") == "Manual-01-01"
        assert db_get_setting("telegraph_url") == url
        assert "createAccount" in client.calls[0][0]
        assert "createPage" in client.calls[1][0]

    async def test_republish_edits_same_page(self, monkeypatch):
        db_set_setting("telegraph_token", "TOK")
        db_set_setting("telegraph_path", "Manual-01-01")
        client = _install(monkeypatch, [
            {"ok": True, "result": {"path": "Manual-01-01", "url": "https://telegra.ph/Manual-01-01"}},
        ])
        await publish_manual()
        assert len(client.calls) == 1
        assert "editPage/Manual-01-01" in client.calls[0][0]

    async def test_api_error_raises(self, monkeypatch):
        db_set_setting("telegraph_token", "TOK")
        _install(monkeypatch, [{"ok": False, "error": "CONTENT_TOO_BIG"}])
        try:
            await publish_manual()
            assert False, "should have raised"
        except RuntimeError as e:
            assert "CONTENT_TOO_BIG" in str(e)


class TestManualCommandTelegraph:
    async def test_publish_subcommand(self, bot, monkeypatch):
        _install(monkeypatch, [
            {"ok": True, "result": {"access_token": "TOK"}},
            {"ok": True, "result": {"path": "P", "url": "https://telegra.ph/P"}},
        ])
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/manual"),
                             chat_type="group")
        await cmd_manual(update, make_context(bot, args=["publish"]))
        assert "https://telegra.ph/P" in update.message.reply_text.await_args.args[0]

    async def test_document_gets_instant_view_button_when_published(self, bot):
        db_set_setting("telegraph_url", "https://telegra.ph/P")
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/manual"),
                             chat_type="group")
        await cmd_manual(update, make_context(bot))
        markup = update.message.reply_document.await_args.kwargs["reply_markup"]
        btn = markup.inline_keyboard[0][0]
        assert btn.url == "https://telegra.ph/P"

    async def test_document_hints_publish_when_unpublished(self, bot):
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/manual"),
                             chat_type="group")
        await cmd_manual(update, make_context(bot))
        kwargs = update.message.reply_document.await_args.kwargs
        assert kwargs["reply_markup"] is None
        assert "/manual publish" in kwargs["caption"]

    async def test_publish_failure_reported(self, bot, monkeypatch):
        db_set_setting("telegraph_token", "TOK")
        _install(monkeypatch, [{"ok": False, "error": "FLOOD_WAIT"}])
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/manual"),
                             chat_type="group")
        await cmd_manual(update, make_context(bot, args=["publish"]))
        assert "failed" in update.message.reply_text.await_args.args[0].lower()
