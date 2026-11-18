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


def doc_summary(data: Any) -> dict:
    doc = _unwrap(data)
    if not isinstance(doc, dict):
        return {"found": doc not in (None, "", [], {}), "shape": type(doc).__name__}
    lists, objects = {}, {}
    for key, value in doc.items():
        if isinstance(value, list):
            first = next((v for v in value if isinstance(v, dict)), None)
            lists[str(key)] = {
                "count": len(value),
                "fields": sorted(map(str, first.keys())) if first else [],
                "safe_values_first": _safe(first) if first else {},
            }
        elif isinstance(value, dict):
            objects[str(key)] = {"fields": sorted(map(str, value.keys())), "safe_values": _safe(value)}
    return {
        "found": True,
        "fields": sorted(map(str, doc.keys())),
        "safe_values": _safe(doc),
        "objects": objects,
        "lists": lists,
    }


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
