"""R5 (Analiza-Monday backlog 0301): the table of "לא פער" - `alert_dismissals` (migrations/008) and its store
(app/desk/dismissals_store.py). One live dismissal per (company, kind, subject), held by the table itself; nothing deleted
or edited - an undo ends its row, and only its own dismisser may (ADR-0190 §6's norm); a dismissal that came back with
other evidence is replaced in one transaction, the old row naming the new; every value of a closed shape; the table in
/health and never made at runtime. The store's contract is held on the real table and on the tests' double
(tests/dismissal_fixtures.py) by the same tests, so the double cannot drift."""
from __future__ import annotations

import threading

import psycopg
import pytest

from app.desk import dismissals as dz
from app.desk import dismissals_store as ds
from tests.conftest import TEST_DATABASE_URL, drop_all, needs_pg
from tests.dismissal_fixtures import MemoryDismissals

COMPANY = "wizdb2245n5"
EVIDENCE_A, EVIDENCE_B = "a" * 64, "b" * 64

# ---- the table, and the contract its store and the tests' double both keep ----------------------------------------------
@pytest.fixture
def pg():
    drop_all(TEST_DATABASE_URL)
    from app.migrate import apply_migrations

    apply_migrations(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


def _rows(url: str) -> list:
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT id, kind, subject, evidence, user_id, undone_at IS NOT NULL, undone_by, replaced_by "
                            "FROM alert_dismissals ORDER BY id").fetchall()


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    """The store and how to read its rows back ((id, undone, undone_by, replaced_by) each): the double, and the table."""
    if request.param == "memory":
        m = MemoryDismissals()
        yield m, lambda: [(r["id"], r["undone_at"] is not None, r["undone_by"], r["replaced_by"]) for r in m.rows]
        return
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    url = request.getfixturevalue("pg")
    yield ds.DismissalStore(url), lambda: [(r[0], r[5], r[6], r[7]) for r in _rows(url)]


def test_one_live_dismissal_per_subject_and_the_same_evidence_twice_is_already_hidden(store):
    s, rows = store
    got = s.dismiss(COMPANY, "books_doc_students", "62503", EVIDENCE_A, 7)
    assert got["replaced"] is None and got["id"] and got["at"]
    with pytest.raises(ds.DismissalRefused) as again:
        s.dismiss(COMPANY, "books_doc_students", "62503", EVIDENCE_A, 8)        # anyone, the same evidence: nothing new
    assert again.value.code == "already_hidden"
    # another kind, another subject or another company is another dismissal
    s.dismiss(COMPANY, "books_doc_unclear", "62503", EVIDENCE_A, 7)
    s.dismiss(COMPANY, "books_doc_students", "62504", EVIDENCE_A, 7)
    s.dismiss("wizdb2245n4", "books_doc_students", "62503", EVIDENCE_A, 7)
    live = s.live(COMPANY)
    assert [(d["kind"], d["subject"], d["evidence"], d["by"]) for d in live] == [
        ("books_doc_students", "62503", EVIDENCE_A, 7), ("books_doc_unclear", "62503", EVIDENCE_A, 7),
        ("books_doc_students", "62504", EVIDENCE_A, 7)]
    assert len(rows()) == 4 and not any(undone for _i, undone, _b, _r in rows())


def test_other_evidence_replaces_the_live_one_and_the_old_row_says_by_whom_and_which(store):
    """An alert that came back ("חזר") is dismissed again by whoever may: one transaction closes the old row - by the new
    dismisser, naming the new row - and keeps the new one. Nothing is deleted."""
    s, rows = store
    first = s.dismiss(COMPANY, "books_ref_invalid", "50934486771", EVIDENCE_A, 7)
    second = s.dismiss(COMPANY, "books_ref_invalid", "50934486771", EVIDENCE_B, 8)
    assert second["replaced"] == first["id"]
    assert rows() == [(first["id"], True, 8, second["id"]), (second["id"], False, None, None)]
    assert [(d["id"], d["evidence"], d["by"]) for d in s.live(COMPANY)] == [(second["id"], EVIDENCE_B, 8)]


def test_only_the_dismisser_undoes_and_an_undone_row_is_kept_not_deleted(store):
    s, rows = store
    mine = s.dismiss(COMPANY, "books_ref_fill", "50934486772", EVIDENCE_A, 7)
    with pytest.raises(ds.DismissalRefused) as other:
        s.undo(COMPANY, "books_ref_fill", "50934486772", mine["id"], 8)
    assert other.value.code == "not_yours" and rows() == [(mine["id"], False, None, None)]
    with pytest.raises(ds.DismissalRefused) as wrong:                        # its id, under another kind or subject
        s.undo(COMPANY, "books_date_fill", "50934486772", mine["id"], 7)
    assert wrong.value.code == "not_live"
    s.undo(COMPANY, "books_ref_fill", "50934486772", mine["id"], 7)
    assert rows() == [(mine["id"], True, 7, None)] and s.live(COMPANY) == []
    with pytest.raises(ds.DismissalRefused) as twice:
        s.undo(COMPANY, "books_ref_fill", "50934486772", mine["id"], 7)
    assert twice.value.code == "not_live"
    again = s.dismiss(COMPANY, "books_ref_fill", "50934486772", EVIDENCE_A, 8)  # undone: the same evidence may be kept again
    assert again["replaced"] is None and [d["id"] for d in s.live(COMPANY)] == [again["id"]]


@needs_pg
def test_the_table_holds_one_live_row_per_subject_whatever_writes_it(pg):
    """The rule is the table's own (the partial unique index), not only the store's read before its write: a second live row
    of one (company, kind, subject) is refused even written by hand, and an ended one makes room for a new one."""
    insert = ("INSERT INTO alert_dismissals (company, kind, subject, evidence, user_id) VALUES (%s, %s, %s, %s, %s) "
              "RETURNING id")
    with psycopg.connect(pg, autocommit=True) as conn:
        first = conn.execute(insert, (COMPANY, "books_doc_students", "62503", EVIDENCE_A, 7)).fetchone()[0]
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (COMPANY, "books_doc_students", "62503", EVIDENCE_B, 8))
        conn.execute("UPDATE alert_dismissals SET undone_at = now(), undone_by = 7 WHERE id = %s", (first,))
        conn.execute(insert, (COMPANY, "books_doc_students", "62503", EVIDENCE_B, 8))
    assert [(r[3], r[5]) for r in _rows(pg)] == [(EVIDENCE_A, True), (EVIDENCE_B, False)]


@needs_pg
@pytest.mark.parametrize("column, value", [
    ("subject", "62503a"), ("subject", ""), ("subject", "1" * 21), ("evidence", "A" * 64), ("evidence", "a" * 63),
    ("kind", "Books"), ("kind", "1books"), ("kind", ""), ("company", ""), ("user_id", 0)])
def test_the_table_refuses_a_value_of_another_shape(pg, column, value):
    """No name, no amount, no free text: a subject is digits, the evidence one sha256 in hex, a kind a word, a user a
    monday id."""
    row = {"company": COMPANY, "kind": "books_doc_students", "subject": "62503", "evidence": EVIDENCE_A, "user_id": 7}
    row[column] = value
    with psycopg.connect(pg, autocommit=True) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO alert_dismissals (company, kind, subject, evidence, user_id) "
                     "VALUES (%(company)s, %(kind)s, %(subject)s, %(evidence)s, %(user_id)s)", row)


@needs_pg
@pytest.mark.parametrize("ended", ["undone_at = now()", "undone_by = 7", "replaced_by = id"])
def test_an_end_is_whole_or_none(pg, ended):
    """`undone_at` and `undone_by` together, and a replacement only of an ended row."""
    s = ds.DismissalStore(pg)
    got = s.dismiss(COMPANY, "books_doc_students", "62503", EVIDENCE_A, 7)
    with psycopg.connect(pg, autocommit=True) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE alert_dismissals SET %s WHERE id = %%s" % ended, (got["id"],))


@needs_pg
def test_two_presses_at_once_keep_one(pg):
    """Both read no live row before either wrote: the index lets one through, and the other is `already_hidden`."""
    s, barrier, out = ds.DismissalStore(pg), threading.Barrier(8), []

    def press(user):
        barrier.wait()
        try:
            out.append(s.dismiss(COMPANY, "books_doc_companies", "50500", EVIDENCE_A, user)["id"])
        except ds.DismissalRefused as refused:
            out.append(refused.code)

    threads = [threading.Thread(target=press, args=(u,)) for u in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(map(str, out)).count("already_hidden") == 7 and len(s.live(COMPANY)) == 1 and len(_rows(pg)) == 1


@needs_pg
def test_without_the_table_every_call_is_store_schema_and_never_its_text(pg):
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("DROP TABLE alert_dismissals")
    s = ds.DismissalStore(pg)
    for call in (lambda: s.live(COMPANY), lambda: s.dismiss(COMPANY, "books_doc_students", "1", EVIDENCE_A, 7),
                 lambda: s.undo(COMPANY, "books_doc_students", "1", 1, 7)):
        with pytest.raises(ds.DismissalStoreError) as got:
            call()
        assert (got.value.kind, got.value.detail) == ("store_schema", "42P01") and got.value.__cause__ is None


def test_a_database_that_cannot_be_reached_is_store_unreachable_by_class_never_its_text():
    s = ds.DismissalStore("postgresql://nobody:SECRETPW@127.0.0.1:1/none?connect_timeout=1")
    with pytest.raises(ds.DismissalStoreError) as got:
        s.live(COMPANY)
    assert got.value.kind == "store_unreachable" and "SECRETPW" not in str(got.value) and "127.0.0.1" not in str(got.value)
    assert "SECRETPW" not in repr(s)


def test_the_migration_only_creates_and_names_the_stores_columns():
    import re

    from app.migrate import MIGRATIONS_DIR, checked_tables

    sql = (MIGRATIONS_DIR / "008_alert_dismissals.sql").read_text(encoding="utf-8")
    code = re.sub(r"--[^\n]*", "", sql)
    body = code[code.index("CREATE TABLE IF NOT EXISTS alert_dismissals"):]
    for col in ds.DISMISSAL_TABLES["alert_dismissals"]:
        assert "\n  %s " % col in body, col
    statements = [x.strip() for x in code.split(";") if x.strip()]
    assert [x.split("(")[0].strip() for x in statements] == [
        "CREATE TABLE IF NOT EXISTS alert_dismissals",
        "CREATE UNIQUE INDEX IF NOT EXISTS alert_dismissals_live ON alert_dismissals"]
    assert statements[1].endswith("(company, kind, subject) WHERE undone_at IS NULL")
    assert not re.search(r"\b(DROP|ALTER|DELETE|UPDATE|INSERT|TRUNCATE)\b", code.upper())
    assert checked_tables()["alert_dismissals"] == ds.DISMISSAL_TABLES["alert_dismissals"]
    # the kinds are the code's closed list; the table checks their shape, so a kind added later drops no constraint
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k) for k in dz.KINDS) and "books_doc_students" not in code


@needs_pg
def test_the_migration_runs_twice_and_the_table_is_in_health(pg, app_client, monkeypatch):
    """`store.tables` of the public /health names it present once the migration ran (never a count of its rows), and absent
    before; the deployment never makes it (it is not made by any call above)."""
    from app import main
    from app.migrate import MIGRATIONS_DIR, apply_migrations

    sql = (MIGRATIONS_DIR / "008_alert_dismissals.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute(sql)
        conn.execute(sql)                                                    # re-runnable
    monkeypatch.setenv("DATABASE_URL", pg)
    main._store_cache.update(at=0.0, key=None, value=None)
    store_block = app_client.get("/health").json()["store"]
    assert store_block["tables"]["alert_dismissals"] == "present" and store_block["missing_columns"] == []
    assert "008_alert_dismissals.sql" in store_block["migrations"]
    with psycopg.connect(pg, autocommit=True) as conn:
        conn.execute("DROP TABLE alert_dismissals")
    main._store_cache.update(at=0.0, key=None, value=None)
    store_block = app_client.get("/health").json()["store"]
    assert store_block["tables"]["alert_dismissals"] == "absent" and "alert_dismissals" in store_block["missing_columns"]
    assert apply_migrations(pg)["missing"] == []                             # repaired from the ledger
