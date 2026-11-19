"""A manual, read-only check against WizCloud, run by .github/workflows/live-check.yml.

Prints one JSON object:
- `check`: the same round as GET /check (session, companies, registered webhooks).
- `docs`: when DOC_IDS is set, one entry per stockID read with invApi/getDoc: whether a
  document came back, its field names, how many rows each list inside it has, and a few
  values that identify no person (number, type, dates, totals).
- `session_test`: when SESSION_TEST is "true", whether a second session leaves the first one
  working (spec §10, "a new WizCloud session revokes the previous one": tried on Demo first).
  Two clients with no store each open a session; the first is then tried again, with no retry.
  Printed: the kinds of the answers and whether the two sessions were the same string.
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


TOKEN_COMPANIES = "CompanyListToTokenApi/TokenCompanies"


def _probe(client: WizClient) -> str:
    """One read with the client's current session and no fresh try: "ok", or the kind of refusal."""
    try:
        client.call(TOKEN_COMPANIES, retry=False)
        return "ok"
    except WizError as exc:
        return exc.kind


def session_test(cfg: config.Config, transport=None) -> dict:
    """Does opening a second session revoke the first? Reads only; no session string is printed."""
    first = WizClient(cfg.server, cfg.db or "", cfg.private_key, transport=transport)
    second = WizClient(cfg.server, cfg.db or "", cfg.private_key, transport=transport)
    out: dict = {"company": cfg.company}
    try:
        a = first.session()
        out["first"] = _probe(first)
        b = second.session()
        out["second"] = _probe(second)
        out["same_string"] = a.token == b.token
        out["first_after_second"] = _probe(first)
    except WizError as exc:
        out["error"] = exc.as_dict()
        return out
    if out["first"] != "ok" or out["second"] != "ok":
        out["verdict"] = "inconclusive: a read failed before the test could start"
    elif out["same_string"]:
        out["verdict"] = "one session: createSession answered the same string"
    elif out["first_after_second"] == "ok":
        out["verdict"] = "both live: a new session does not revoke the previous one"
    elif out["first_after_second"] == "session_rejected":
        out["verdict"] = "revoked: a new session ends the previous one"
    else:  # a network blip or a server error says nothing about revocation (review 19.11)
        out["verdict"] = f"inconclusive: the third read failed with {out['first_after_second']}"
    return out


def main() -> int:
    cfg = config.load()
    client = WizClient(cfg.server, cfg.db or "", cfg.private_key) if cfg.ok else None
    out: dict = {"code_sha": os.environ.get("GITHUB_SHA"), "check": run_check(cfg, client=client)}
    if os.environ.get("SESSION_TEST", "").strip().lower() == "true" and cfg.ok:
        out["session_test"] = session_test(cfg)
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
