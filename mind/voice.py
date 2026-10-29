"""The one place that calls the network: one Messages API request per line she might say.

A chat reply may carry Anthropic's web search tool (speak(..., searches=N)): the search
runs on Anthropic's side, within the same request, so nothing here talks to any other
host. A turn the API pauses mid-search is resumed at most MAX_CONTINUATIONS times. The
search results are read by the model only; nothing here logs or keeps them.

The client is injected, so tests pass a fake and nothing here runs offline by accident.
Any failure (no key, network, API error, refusal, malformed reply) comes back as None
and is logged by type and status only, never with prompt or reply text.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass

from companion.model import MOODS

from .prompts import REPLY_SCHEMA

log = logging.getLogger("mind")

KEY_NAMES = ("CC_COMPANION_API_KEY", "ANTHROPIC_API_KEY")
MAX_SAY = 120
MAX_NOTICE = 200
SEARCH_TOOL = "web_search_20250305"  # the basic search: results go straight to the model
MAX_CONTINUATIONS = 2


@dataclass(frozen=True)
class Reply:
    say: str  # "" means she chose silence
    mood: str
    notice: str


def find_api_key() -> str | None:
    """CC_COMPANION_API_KEY, then ANTHROPIC_API_KEY: from this process, else from the user's saved environment.

    The saved environment (HKCU\\Environment) is read because a variable set after Claude
    Code started is not in the environment the daemon inherited.
    """
    for name in KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                for name in KEY_NAMES:
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    if isinstance(value, str) and value:
                        return value
        except OSError:
            pass
    return None


def parse_reply(text: str) -> Reply | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    say, mood, notice = data.get("say"), data.get("mood"), data.get("notice", "")
    if not isinstance(say, str) or mood not in MOODS or not isinstance(notice, str):
        return None
    say = " ".join(say.split())
    if len(say) > MAX_SAY:
        return None  # /say would refuse it; better silent than cut mid-word
    return Reply(say, mood, " ".join(notice.split())[:MAX_NOTICE])


class Voice:
    def __init__(self, client, model: str, max_tokens: int = 2048) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def speak(self, system: str, user: str, searches: int = 0) -> tuple[Reply | None, dict]:
        """(reply or None, usage dict); usage is empty when the request never completed.

        With `searches` above 0 she may search the web up to that many times for this reply;
        usage["web_search_requests"] says how many she did.
        """
        tools = [{"type": SEARCH_TOOL, "name": "web_search", "max_uses": searches}] if searches > 0 else None
        text, usage = self.ask(system, user, REPLY_SCHEMA, effort="low", max_tokens=self.max_tokens, tools=tools)
        if text is None:
            return None, usage
        reply = parse_reply(text)
        if reply is None:
            log.warning("no line: reply did not match the schema")
        return reply, usage

    def ask(
        self, system: str, user: str, schema: dict, *, effort: str, max_tokens: int, tools: list | None = None,
    ) -> tuple[str | None, dict]:  # fmt: skip
        """One request with a cached system prompt and a JSON-schema reply: (JSON text or None, usage).

        With server `tools`, a paused turn is sent back as it came to let it finish, and the
        usage adds up over those requests.
        """
        messages: list[dict] = [{"role": "user", "content": user}]
        usage: dict = {}
        for _ in range(1 + (MAX_CONTINUATIONS if tools else 0)):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=messages,
                    output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                    **({"tools": tools} if tools else {}),
                )
            except Exception as error:  # the SDK's typed errors, timeouts and anything else: stay silent
                status = getattr(error, "status_code", None)
                log.warning("api call failed: %s%s", type(error).__name__, f" {status}" if status else "")
                return None, usage
            usage = _added(usage, _usage(getattr(response, "usage", None)))
            if response.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": response.content}]
        if response.stop_reason != "end_turn":
            log.info("no reply: stop_reason=%s", response.stop_reason)
            return None, usage
        text = _final_text(response.content)
        return (text if isinstance(text, str) else None), usage


def _final_text(content) -> str | None:
    """The reply's text: what follows the last search result (the words before a search are not the reply)."""
    blocks = list(content or [])
    last_tool = max((i for i, b in enumerate(blocks) if str(getattr(b, "type", "")).endswith("_tool_result")), default=-1)
    texts = [b.text for b in blocks[last_tool + 1 :] if getattr(b, "type", None) == "text" and isinstance(b.text, str)]
    return "".join(texts) if texts else None


def _usage(usage) -> dict:
    if usage is None:
        return {}
    names = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    counted = {name: getattr(usage, name, 0) or 0 for name in names}
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0)
    counted["web_search_requests"] = searches if isinstance(searches, int) else 0
    return counted


def _added(total: dict, more: dict) -> dict:
    return {name: total.get(name, 0) + more.get(name, 0) for name in {**total, **more}}
