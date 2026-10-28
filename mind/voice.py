"""The one place that calls the network: one Messages API request per line she might say.

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

    def speak(self, system: str, user: str) -> tuple[Reply | None, dict]:
        """(reply or None, usage dict); usage is empty when the request never completed."""
        text, usage = self.ask(system, user, REPLY_SCHEMA, effort="low", max_tokens=self.max_tokens)
        if text is None:
            return None, usage
        reply = parse_reply(text)
        if reply is None:
            log.warning("no line: reply did not match the schema")
        return reply, usage

    def ask(self, system: str, user: str, schema: dict, *, effort: str, max_tokens: int) -> tuple[str | None, dict]:
        """One request with a cached system prompt and a JSON-schema reply: (JSON text or None, usage)."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except Exception as error:  # the SDK's typed errors, timeouts and anything else: stay silent
            status = getattr(error, "status_code", None)
            log.warning("api call failed: %s%s", type(error).__name__, f" {status}" if status else "")
            return None, {}
        usage = _usage(getattr(response, "usage", None))
        if response.stop_reason != "end_turn":
            log.info("no reply: stop_reason=%s", response.stop_reason)
            return None, usage
        text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), None)
        return (text if isinstance(text, str) else None), usage


def _usage(usage) -> dict:
    if usage is None:
        return {}
    names = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    return {name: getattr(usage, name, 0) or 0 for name in names}
