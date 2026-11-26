"""R5's test double: app/desk/dismissals_store.py's contract in memory (tests/test_desk_dismissals.py holds the two to the
same tests, so the double cannot drift from the table's rules)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.desk.dismissals_store import DismissalRefused, DismissalStoreError

T0 = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


class MemoryDismissals:
    """`rows` as the table holds them, oldest first; `fail` (a DismissalStoreError) makes every call raise it; `calls`
    counts each call by its name."""

    kind = "memory"

    def __init__(self):
        self.rows: list = []
        self.fail = None
        self.calls: dict = {}

    def _call(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1
        if self.fail is not None:
            raise self.fail

    def _at(self) -> str:
        return (T0 + timedelta(minutes=len(self.rows))).isoformat(timespec="seconds")

    def live(self, company: str) -> list:
        self._call("live")
        return [{k: r[k] for k in ("id", "kind", "subject", "evidence", "by", "at")}
                for r in self.rows if r["company"] == company and r["undone_at"] is None]

    def _live_one(self, company, kind, subject):
        return next((r for r in self.rows if (r["company"], r["kind"], r["subject"]) == (company, kind, subject)
                     and r["undone_at"] is None), None)

    def dismiss(self, company: str, kind: str, subject: str, evidence: str, user_id: int) -> dict:
        self._call("dismiss")
        old = self._live_one(company, kind, subject)
        if old is not None and old["evidence"] == evidence:
            raise DismissalRefused("already_hidden")
        new = {"id": len(self.rows) + 1, "company": company, "kind": kind, "subject": subject, "evidence": evidence,
               "by": int(user_id), "at": self._at(), "undone_at": None, "undone_by": None, "replaced_by": None}
        if old is not None:
            old.update(undone_at=new["at"], undone_by=int(user_id), replaced_by=new["id"])
        self.rows.append(new)
        return {"id": new["id"], "at": new["at"], "replaced": None if old is None else old["id"]}

    def undo(self, company: str, kind: str, subject: str, dismissal_id: int, user_id: int) -> None:
        self._call("undo")
        row = self._live_one(company, kind, subject)
        if row is None or row["id"] != int(dismissal_id):
            raise DismissalRefused("not_live")
        if row["by"] != int(user_id):
            raise DismissalRefused("not_yours")
        row.update(undone_at=self._at(), undone_by=int(user_id))


def failing(kind: str = "store_schema", detail: str = "42P01") -> DismissalStoreError:
    return DismissalStoreError(kind, detail)
