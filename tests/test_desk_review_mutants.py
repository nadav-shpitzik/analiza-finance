"""D2's review (on 62fd993): the mutants of the server it found surviving, each held by a test here."""
from __future__ import annotations

from app import main
from app.desk import reading as rd
from app.desk import view as fd
from tests.desk_fixtures import TODAY, sc, snap
from tests.test_desk import B2, SC, desk, session_token  # noqa: F401 - `desk` is the fixture


def _auth(user: int) -> dict:
    return {"Authorization": "Bearer " + session_token(user)}


def _link(desk, user: int, tab: str) -> str:  # noqa: F811
    taken = desk.get(session_token(user)).json()["taken_at"]
    resp = desk.client.post("/api/desk/export-link", headers=_auth(user), json={"tab": tab, "taken": taken})
    assert resp.status_code == 200, resp.json()
    return resp.json()["url"]


def test_the_companies_file_asks_b2b_charges_members_not_student_charges(desk):  # noqa: F811
    url = _link(desk, 3, "b2b")  # user 3 is on B2B Charge only
    assert desk.client.get(url).status_code == 200
    desk.w.members[B2] = {"1"}  # user 3 left B2B Charge
    main.app.state.desk.members._held.clear()
    assert desk.client.get(url).status_code == 403


def test_the_file_is_the_held_reading_even_past_its_minute(desk):  # noqa: F811
    url = _link(desk, 1, "b2c")
    held = main.app.state.desk.reading
    held.reading_at -= rd.HELD_TTL_S * 3  # the held reading is old now
    before = len(desk.w.queries_of("columns(ids:"))
    assert desk.client.get(url).status_code == 200
    assert len(desk.w.queries_of("columns(ids:")) == before  # no new reading for a file


def test_a_one_tab_reader_is_not_told_of_the_other_tabs_short_board(desk):  # noqa: F811
    desk.w.count_off[B2] = 1  # B2B Charge reads short
    inna = desk.get(session_token(2)).json()  # Student Charge only
    assert inna["view"]["checks"]["ok"] is True
    both = desk.get(session_token(1)).json()
    assert both["view"]["checks"]["ok"] is False and both["view"]["checks"]["b2b_ok"] is False


def test_the_visible_part_never_carries_an_id_number_even_if_a_row_had_one():
    view = fd.build_view(snap([sc("1")]), TODAY)
    for row in view["b2c"]["rows"]:
        row["id_number"] = "012345678"
    out = fd.visible_part(view, True, True)
    assert not any("id_number" in row for row in out["b2c"]["rows"])
