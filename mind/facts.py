"""What she has learned about you: facts.json and the operations sleep applies to it. Pure.

    {"next_id": 4, "facts": [
      {"id": "f1", "text": "often works past 1am", "days": ["2026-10-27", "2026-10-28"],
       "seen": 3, "told": false, "archived": false, "reason": ""}, ...]}

A fact seen on KNOWN_AFTER_DAYS or more different days is known and reaches her
prompts; until then it is tentative. A fact the user told her themself in the chat
("told") is known after one night: their own words are the best evidence she has.
Sleep may mark a fact told only on a day the user did type to her. Known facts the
user told her come first. Nothing is deleted: a wrong, stale or merged fact is archived
with a reason. At most MAX_ACTIVE facts stay active; the least reinforced are archived
first.
"""

from __future__ import annotations

KNOWN_AFTER_DAYS = 2
MAX_ACTIVE = 150
MAX_TEXT = 200
OPS = ("add", "reinforce", "merge", "archive")

OPS_SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": list(OPS)},
                    "text": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "told": {"type": "boolean"},
                },
                "required": ["op", "text", "ids", "reason", "told"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ops"],
    "additionalProperties": False,
}


def blank() -> dict:
    return {"next_id": 1, "facts": []}


def clean(doc: object) -> dict:
    """A facts document with every malformed fact dropped."""
    if not isinstance(doc, dict) or not isinstance(doc.get("facts"), list):
        return blank()
    facts = []
    for fact in doc["facts"]:
        if not isinstance(fact, dict) or not isinstance(fact.get("id"), str) or not isinstance(fact.get("text"), str):
            continue
        days = [d for d in fact.get("days", []) if isinstance(d, str)]
        seen = fact.get("seen") if isinstance(fact.get("seen"), int) else len(days)
        facts.append({
            "id": fact["id"], "text": fact["text"][:MAX_TEXT], "days": days, "seen": seen,
            "told": fact.get("told") is True, "archived": bool(fact.get("archived")), "reason": str(fact.get("reason", "")),
        })
    next_id = doc.get("next_id") if isinstance(doc.get("next_id"), int) else len(facts) + 1
    return {"next_id": max(next_id, len(facts) + 1), "facts": facts}


def active(doc: dict) -> list[dict]:
    return [f for f in doc["facts"] if not f["archived"]]


def is_known(fact: dict) -> bool:
    return fact["told"] or len(fact["days"]) >= KNOWN_AFTER_DAYS


def known(doc: dict, limit: int = 15) -> list[str]:
    """Her known facts: what the user told her first, then the most reinforced."""
    ranked = sorted((f for f in active(doc) if is_known(f)), key=lambda f: (not f["told"], -f["seen"], f["id"]))
    return [f["text"] for f in ranked[:limit]]


def apply_ops(doc: dict, ops: list, day: str, *, user_spoke: bool = False) -> tuple[dict, list[str]]:
    """Apply sleep's operations for `day`; (the new document, one line per change).

    Unknown ids, empty texts and malformed operations are skipped. A fact is reinforced
    at most once per day. An operation's "told" (the user said it themself in the chat)
    counts only if `user_spoke`: they typed to her that day.
    """
    doc = clean(doc)
    by_id = {f["id"]: f for f in active(doc)}
    changes: list[str] = []

    def new_fact(text: str, days: list[str], seen: int, told: bool) -> dict:
        fact = {
            "id": f"f{doc['next_id']}", "text": text[:MAX_TEXT], "days": sorted(set(days)), "seen": seen,
            "told": told, "archived": False, "reason": "",
        }
        doc["next_id"] += 1
        doc["facts"].append(fact)
        by_id[fact["id"]] = fact
        return fact

    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict) or op.get("op") not in OPS:
            continue
        text = " ".join(str(op.get("text", "")).split())
        ids = [i for i in op.get("ids", []) if isinstance(i, str) and i in by_id] if isinstance(op.get("ids"), list) else []
        reason = " ".join(str(op.get("reason", "")).split())[:MAX_TEXT]
        told = user_spoke and op.get("told") is True
        mark = " (you told her)" if told else ""
        if op["op"] == "add" and text:
            fact = new_fact(text, [day], 1, told)
            changes.append(f"learned {fact['id']}{mark}: {fact['text']}")
        elif op["op"] == "reinforce":
            for fid in ids:
                fact = by_id[fid]
                if told and not fact["told"]:
                    fact["told"] = True
                    changes.append(f"you told her {fid}: {fact['text']}")
                if day not in fact["days"]:
                    fact["days"] = sorted(fact["days"] + [day])
                    fact["seen"] += 1
                    changes.append(f"reinforced {fid}: {fact['text']}")
        elif op["op"] == "merge" and text and len(ids) >= 2:
            merged = [by_id.pop(fid) for fid in ids]
            fact = new_fact(
                text, [d for f in merged for d in f["days"]] + [day], sum(f["seen"] for f in merged),
                told or any(f["told"] for f in merged),
            )
            for old in merged:
                old["archived"], old["reason"] = True, f"merged into {fact['id']}"
            changes.append(f"merged {', '.join(ids)} into {fact['id']}: {fact['text']}")
        elif op["op"] == "archive":
            for fid in ids:
                fact = by_id.pop(fid)
                fact["archived"], fact["reason"] = True, reason or "archived"
                changes.append(f"archived {fid}: {fact['text']}")

    live = active(doc)
    if len(live) > MAX_ACTIVE:
        for fact in sorted(live, key=lambda f: (f["seen"], max(f["days"], default=""), f["id"]))[: len(live) - MAX_ACTIVE]:
            fact["archived"], fact["reason"] = True, "too many facts; least reinforced"
            changes.append(f"archived {fact['id']} (cap): {fact['text']}")
    return doc, changes
