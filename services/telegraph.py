"""Publish the manual to Telegraph (telegra.ph) — Telegram's article platform.
Telegraph pages get Instant View automatically, so the manual opens as a
native in-app article.

Telegraph supports a limited node set: h3/h4 (no h1/h2), p, ul/ol/li, pre,
blockquote, hr, b/i/a/code. Markdown tables become bullet lists."""
import json
import logging
import os
import re
from typing import List, Union

import httpx

log = logging.getLogger("nopmsbot")

API = "https://api.telegra.ph"
Node = Union[str, dict]

_INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")

def _inline(text: str) -> List[Node]:
    """Parse **bold**, `code`, [label](url) into Telegraph nodes."""
    nodes: List[Node] = []
    for part in _INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            nodes.append({"tag": "b", "children": [part[2:-2]]})
        elif part.startswith("`") and part.endswith("`"):
            nodes.append({"tag": "code", "children": [part[1:-1]]})
        elif part.startswith("[") and "](" in part:
            label, url = part[1:-1].split("](", 1)
            nodes.append({"tag": "a", "attrs": {"href": url}, "children": [label]})
        else:
            nodes.append(part)
    return nodes

def _table_row_cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]

def md_to_nodes(md: str) -> List[dict]:
    nodes: List[dict] = []
    lines = md.splitlines()
    i = 0
    para: List[str] = []
    bullets: List[List[Node]] = []

    def flush_para():
        if para:
            nodes.append({"tag": "p", "children": _inline(" ".join(para))})
            para.clear()

    def flush_bullets():
        if bullets:
            nodes.append({"tag": "ul",
                          "children": [{"tag": "li", "children": c} for c in bullets]})
            bullets.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_para(); flush_bullets()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            nodes.append({"tag": "pre", "children": ["\n".join(block)]})
        elif not stripped:
            flush_para(); flush_bullets()
        elif stripped.startswith("###"):
            flush_para(); flush_bullets()
            nodes.append({"tag": "h4", "children": _inline(stripped.lstrip("#").strip())})
        elif stripped.startswith("#"):
            flush_para(); flush_bullets()
            nodes.append({"tag": "h3", "children": _inline(stripped.lstrip("#").strip())})
        elif stripped == "---":
            flush_para(); flush_bullets()
            nodes.append({"tag": "hr"})
        elif stripped.startswith("> "):
            flush_para(); flush_bullets()
            nodes.append({"tag": "blockquote", "children": _inline(stripped[2:])})
        elif stripped.startswith(("- ", "• ")):
            flush_para()
            bullets.append(_inline(stripped[2:]))
        elif stripped.startswith("|"):
            flush_para()
            if not re.fullmatch(r"[|\s:-]+", stripped):  # skip |---|---| separators
                cells = [c for c in _table_row_cells(stripped) if c]
                bullets.append(_inline(" — ".join(cells)))
        else:
            para.append(stripped)
        i += 1

    flush_para(); flush_bullets()
    return nodes

def _manual_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "docs", "MANUAL.md")

async def publish_manual(title: str = "CustomPMBot — Complete Manual") -> str:
    """Create or update the Telegraph page for MANUAL.md. Returns the page URL.
    Account token and page path persist in settings, so re-publishing keeps
    the same URL."""
    from database.settings import db_get_setting, db_set_setting

    with open(_manual_path(), "r", encoding="utf-8") as f:
        nodes = md_to_nodes(f.read())

    async with httpx.AsyncClient(timeout=20) as client:
        token = db_get_setting("telegraph_token")
        if not token:
            r = await client.post(f"{API}/createAccount",
                                  data={"short_name": "NoPMsBot", "author_name": "NoPMsBot"})
            j = r.json()
            if not j.get("ok"):
                raise RuntimeError(f"Telegraph createAccount failed: {j}")
            token = j["result"]["access_token"]
            db_set_setting("telegraph_token", token)

        payload = {"access_token": token, "title": title,
                   "content": json.dumps(nodes), "return_content": "false"}
        path = db_get_setting("telegraph_path")
        endpoint = f"{API}/editPage/{path}" if path else f"{API}/createPage"
        r = await client.post(endpoint, data=payload)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"Telegraph publish failed: {j}")

        db_set_setting("telegraph_path", j["result"]["path"])
        url = j["result"]["url"]
        db_set_setting("telegraph_url", url)
        log.info("Manual published to Telegraph: %s", url)
        return url
