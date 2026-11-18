"""A manual, read-only check against WizCloud, run by .github/workflows/live-check.yml.

Prints one JSON object:
- `check`: the same round as GET /check (session, companies, registered webhooks).
- `docs`: when DOC_IDS is set, one entry per stockID read with invApi/getDoc: whether a
  document came back, its field names, how many rows each list inside it has, and a few
  values that identify no person (number, type, dates, totals).
Never printed: names, addresses, phones, free text, account keys, the key or the session.
GitHub also masks the WIZ_PRIVATE_KEY secret in the log.

Run locally only with a real key in the environment, which never happens in a Claude session.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.main import run_check  # noqa: E402
from app.wizcloud import WizClient, WizError  # noqa: E402

# Fields whose values identify no person. Matched case-insensitively. Everything else is
# reported by name only.
SAFE_FIELDS = {
    "id",
    "stockid",
    "documentid",
    "docnumber",
    "doctype",
    "transtype",
    "status",
    "valuedate",
    "duedate",
    "paydate",
    "tftal",
    "tftalvat",
    "tftalvatfree",
    "vatprc",
    "currency",
    "currencycode",
    "quantity",
    "price",
}
MAX_DOCS = 10


def _unwrap(data: Any) -> Any:
    """Strip the API envelope ({"statusCode", "repdata"} and the like), nothing deeper."""
    if isinstance(data, dict):
        for key in ("repdata", "data", "result"):
            inner = data.get(key)
            if isinstance(inner, (dict, list)) and inner:
                return _unwrap(inner)
        return data
    if isinstance(data, list) and data:
        return _unwrap(data[0])
    return data


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def describe(value: Any, depth: int = 4) -> Any:
    """Field names, row counts and SAFE_FIELDS values, any nesting. Seen live 18.11: a
    document is {"DocumentDetails": [<two parts that are not dicts>]}."""
    value = _maybe_json(value)
    if isinstance(value, dict):
        out: dict = {"fields": sorted(map(str, value.keys())), "safe_values": _safe(value)}
        if depth:
            nested = {
                str(k): describe(v, depth - 1)
                for k, v in value.items()
                if isinstance(_maybe_json(v), (dict, list))
            }
            if nested:
                out["nested"] = nested
        return out
    if isinstance(value, list):
        out = {"count": len(value)}
        if value and depth:
            out["first"] = [describe(v, depth - 1) for v in value[:2]]
        return out
    if isinstance(value, str):
        return f"str({len(value)})"
    return type(value).__name__


def doc_summary(data: Any) -> dict:
    doc = _unwrap(data)
    if doc in (None, "", [], {}):
        return {"found": False}
    return {"found": True, "shape": describe(doc)}


def _safe(d: dict) -> dict:
    return {
        str(k): v
        for k, v in d.items()
        if str(k).lower() in SAFE_FIELDS and (v is None or isinstance(v, (str, int, float, bool)))
    }


def parse_ids(raw: str) -> list:
    ids = []
    for part in (raw or "").replace(" ", "").split(","):
        if part.isdigit():
            ids.append(int(part))
    return ids[:MAX_DOCS]


def main() -> int:
    cfg = config.load()
    client = WizClient(cfg.server, cfg.db or "", cfg.private_key) if cfg.ok else None
    out: dict = {"code_sha": os.environ.get("GITHUB_SHA"), "check": run_check(cfg, client=client)}
    ids = parse_ids(os.environ.get("DOC_IDS", ""))
    if ids and client is not None and out["check"].get("session", {}).get("ok"):
        docs = {}
        for stock_id in ids:
            try:
                docs[str(stock_id)] = doc_summary(client.get_doc(stock_id))
            except WizError as exc:
                docs[str(stock_id)] = exc.as_dict()
        out["docs"] = docs
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["check"].get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
