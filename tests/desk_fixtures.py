"""The finance desk's test builders (Analiza-Monday backlog 0301 D2): analiza-metrics tests/test_finance.py at `da671b2`,
its builders moved here so the view's, the files' and the routes' tests share them.

A row is built the way the reading gives it (app/monday/client.py `flatten`): a status cell as its label under the
column id and its index under `id#`, a relation as a list of ids, any other cell as its text. `snap` is a snapshot in
the reading's shape; `served` makes the desk's reading answer given snapshots instead of monday, as analiza-metrics'
FakeFinance stood for monday in its route tests.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.desk import reading as rd
from app.desk import view as fd

TODAY = date(2026, 11, 9)
D = Decimal


def status(row: dict, col: str, idx: str, labels: dict) -> None:
    row[col] = labels.get(idx, "תווית חדשה") if idx else ""
    row[col + "#"] = idx


def sc(id_, st="1", paid="4000", lead=None, enr=None, receipt="1", **kw):
    r = {"id": str(id_), "name": "תלמיד/ה %s" % id_, "created_at": kw.pop("created", "2026-10-01T10:00:00Z"),
         fd.SC["paid"]: paid, fd.SC["lead"]: lead or [], fd.SC["enrollment"]: enr or [],
         fd.SC["invoice"]: kw.pop("invoice", "100")}
    status(r, fd.SC["status"], st, fd.PAY)
    status(r, fd.SC["receipt"], receipt, fd.RECEIPT)
    for k, v in kw.items():
        r[fd.SC[k]] = v
    return r


def enrollment(id_, cohort_id=None, voucher="", asked=""):
    """An Enrollments item: its cohort (by id), its voucher status (by index) and its "תאריך בקשת ביטול"."""
    r = {"id": str(id_), "name": "הרשמה %s" % id_, fd.EN["cohort"]: [str(cohort_id)] if cohort_id else [],
         fd.EN["asked"]: asked}
    status(r, fd.EN["voucher"], voucher, fd.VOUCHER)
    return r


def lead(id_, *cohort_ids, rep=""):
    return {"id": str(id_), "name": "ליד %s" % id_, fd.LD["cohort"]: [str(c) for c in cohort_ids], fd.LD["rep"]: rep}


def cohort(id_, code, flow="שוברים משרד העבודה", st="פעיל", dates="2026-08-01 - 2026-12-01", name=None):
    return {"id": str(id_), "name": name or "%s | קורס | %s" % (code, flow), fd.CO["code"]: code, fd.CO["flow"]: flow,
            fd.CO["status"]: st, fd.CO["dates"]: dates, fd.CO["external"]: "קורס"}


def b2(id_, billing="6", payment="1", amount="1000", account="A1", deal="D1", due="2026-10-01", invoice="55", paid=""):
    r = {"id": str(id_), "name": "חיוב", "created_at": "2026-09-01T00:00:00Z", fd.B2["amount"]: amount,
         fd.B2["account"]: [account] if account else [], fd.B2["deal"]: [deal] if deal else [],
         fd.B2["due"]: due, fd.B2["invoice"]: invoice, fd.B2["paid_so_far"]: paid}
    status(r, fd.B2["billing"], billing, fd.BILLING)
    status(r, fd.B2["payment"], payment, fd.B2B_PAY)
    return r


def deal(id_, payer="A1", stage="1", value=""):
    return {"id": str(id_), "name": "עסקה %s" % id_, fd.DL["stage"]: "", fd.DL["stage"] + "#": stage,
            fd.DL["value"]: value, fd.DL["payer"]: [payer]}


def acct(id_, terms=""):
    return {"id": str(id_), "name": "לקוח %s" % id_, fd.AC["terms"]: terms}


def snap(sc_rows=(), b2_rows=(), cohorts=(), deals=(), accounts=(), sc_count=None, b2_count=None, enrollments=(), leads=()):
    return {"read_at": "2026-11-09T20:00:00+00:00",
            "student_charge": {"items_count": len(sc_rows) if sc_count is None else sc_count, "rows": list(sc_rows)},
            "b2b_charge": {"items_count": len(b2_rows) if b2_count is None else b2_count, "rows": list(b2_rows)},
            "enrollments": list(enrollments), "leads": list(leads), "cohorts": list(cohorts),
            "deals": list(deals) or [deal("D1")], "accounts": list(accounts) or [acct("A1")]}


def view(*a, **kw):
    return fd.build_view(snap(*a, **kw), TODAY)


def cohort_of(v, code):
    """The student payments the view placed in a cohort ('' for none)."""
    return [x for x in v["b2c"]["rows"] if x["cohort_code"] == code]


def row_of(v, id_):
    return next(x for x in v["b2c"]["rows"] if x["id"] == str(id_))


def b2row(v, id_):
    return next(x for x in v["b2b"]["rows"] if x["id"] == str(id_))


# ---- a reading that answers given snapshots (the routes' tests) ------------------------------------------------------
def reading_of(s: dict, n: int = 1) -> dict:
    """A snapshot as the desk's reading holds it (app/desk/reading.py `read_desk`): the names and Owners of every lead the
    payments link to, as the reading gives them, every board counted whole, and a time of its own - the n-th reading of
    the test, so two readings are two times."""
    sc_rows = s["student_charge"]["rows"]
    ids, leads = rd.lead_ids(sc_rows), s.get("leads") or []
    at = "2026-11-24T10:%02d:%02d+00:00" % (n // 60, n % 60)
    out = dict(s, read_at=at, full_read_at=at, kind="full", complete=True, error=None, detail=None, calls=10,
               seconds=0.1, checks={k: {"board_id": b, "ok": True, "rows": 0} for k, b in rd.BOARDS.items()})
    out.setdefault("lead_names", rd.lead_names_of(ids, leads))
    out.setdefault("lead_reps", rd.lead_reps_of(ids, leads))
    return out


class Served:
    """How many readings the desk made (`calls`), each answered with the next snapshot given, the last one again after."""

    def __init__(self, snapshots):
        self.snapshots, self.calls = list(snapshots), 0

    def __call__(self, _reader) -> dict:
        self.calls += 1
        return reading_of(self.snapshots[min(self.calls, len(self.snapshots)) - 1], self.calls)


def served(monkeypatch, *snapshots) -> Served:
    """The desk's reading answers these snapshots in turn instead of reading monday (the members still come from the
    test's monday): app/desk/reading.py's HeldReading calls `read_desk`, which this replaces."""
    s = Served(snapshots)
    monkeypatch.setattr(rd, "read_desk", s)
    return s
