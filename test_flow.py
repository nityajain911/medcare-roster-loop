"""Walks the main demo flow with Flask's test client and checks the key acceptance points."""
import re

import app as webapp
import logic


def client():
    return webapp.app.test_client()


def state_of(c):
    vid = c.get_cookie(webapp.COOKIE).value
    return webapp.STATES[vid]


def text(resp):
    return resp.get_data(as_text=True)


def main():
    c = client()
    assert c.get("/").status_code == 200
    s = state_of(c)
    assert logic.metrics(s)["north_star"] == {"value": 60, "n": 26, "d": 43}

    assert webapp.next_step(s)[0] == 1
    # 1. Practice view, use sample file
    assert "Lakeview Family Practice" in text(c.get("/practice"))
    r = c.post("/practice/sample")
    bid = r.headers["Location"].rsplit("/", 1)[-1]
    page = text(c.get(f"/practice/review/{bid}"))
    res = logic.analyse(s, s["batches"][bid])
    assert res["counts"] == {"Unchanged": 6, "Added": 1, "Removed": 1, "Changed": 1, "Invalid": 1, "Duplicate": 1}, res["counts"]
    routes = {r["row_no"]: r["route"] for r in res["rows"]}
    assert routes[7] == "automatic" and routes[8] == "plan_review" and routes[9] == "sent_back"
    assert routes[10] == "plan_review" and routes[11] == "held"
    assert res["rows"][7]["flagged"]
    assert "Needs your confirmation" in page and "Submit changes" in page

    # 2. Confirm mapping and submit
    c.post(f"/practice/review/{bid}/mapping", data={"field_2": "site"})
    c.post(f"/practice/review/{bid}/submit")
    ch = {x["name"]: x for x in s["changes"].values()}
    mei, arjun = ch["Mei Lin"], ch["Arjun Patel"]
    phone = next(x for x in s["changes"].values() if x["route"] == "automatic")
    assert phone["status"] == "Waiting for systems"
    assert mei["status"] == arjun["status"] == "In plan review"

    # 3. Plan queue: flagged removal needs a note, then approve both
    assert "Mei Lin" in text(c.get("/plan/queue"))
    c.post(f"/plan/change/{arjun['id']}/approve", data={"note": ""})
    assert arjun["status"] == "In plan review"
    c.post(f"/plan/change/{arjun['id']}/approve", data={"note": "Practice confirmed last day of work."})
    c.post(f"/plan/change/{mei['id']}/approve", data={"note": ""})
    assert mei["status"] == "Waiting for systems"
    assert [d["name"] for d in mei["destinations"]] == ["Provider database", "Claims and prior auth", "Directory"]
    assert [d["status"] for d in mei["destinations"]] == ["Acknowledged", "Acknowledged", "Pending"]
    assert webapp.next_step(s)[:2] == (4, "Click Run directory sync")
    assert all(x["status"] != "Applied" for x in (mei, arjun, phone))

    # 4. Reconciliation: sync, one failure, fix and resend
    c.post("/reconcile/sync")
    assert phone["status"] == arjun["status"] == "Applied"
    assert mei["status"] == "Partially applied" and mei["id"] == "MC-1042"
    rec = text(c.get("/reconcile"))
    assert "Directory rejected the update" in rec
    assert 'value="207Q00000X" selected' in rec and "suggested from synthetic registry" in rec
    c.post("/reconcile/sync")  # a second sync must not fail it again
    d = [x for x in mei["destinations"] if x["name"] == "Directory"][0]
    assert d["attempts"] == 1
    c.post(f"/reconcile/{mei['id']}/resend", data={"code": "207Q00000X"})
    assert mei["status"] == "Applied"
    assert mei["destinations"][0]["attempts"] == 1 and mei["destinations"][1]["attempts"] == 1 and d["attempts"] == 2
    assert mei["version"] == "v1 (new record)" and arjun["version"] == "v1 → v2"
    assert len([x for x in s["changes"].values() if x["name"] == "Mei Lin"]) == 1

    # 5. Practice status
    st = text(c.get("/practice/status"))
    assert st.count("class=\"live\"") == 3 and "Looks like a typo" in st

    assert webapp.next_step(s)[0] == 6
    # 6. Dashboard
    m = logic.metrics(s)
    assert m["north_star"] == {"value": 79, "n": 34, "d": 43}, m["north_star"]
    assert "79%" in text(c.get("/dashboard"))
    assert webapp.next_step(s)[0] == 7

    # Reverse creates a correcting change; nothing is deleted
    c.post(f"/reconcile/{phone['id']}/reverse")
    fix = s["changes"][phone["reversed_by"]]
    assert fix["reverses"] == phone["id"] and phone["status"] == "Applied" and fix["status"] == "Waiting for systems"
    c.post("/reconcile/sync")
    assert fix["status"] == "Applied" and fix["version"] == "v2 → v3"
    assert s["affiliations"][phone["aff_id"]]["phone"] == phone["changed_fields"][0]["old"]

    # Confirm no changes raises the north star (fresh visitor)
    c2 = client()
    c2.get("/")
    c2.post("/practice/confirm", data={})
    assert logic.metrics(state_of(c2))["north_star"]["n"] == 26
    c2.post("/practice/confirm", data={"agree": "yes"})
    assert logic.metrics(state_of(c2))["north_star"] == {"value": 81, "n": 35, "d": 43}

    # Two visitors don't share state; reset works
    assert state_of(c2) is not s
    c.post("/reset")
    assert logic.metrics(state_of(c))["north_star"]["n"] == 26 and not state_of(c)["changes"]

    # Settings: phone changes off sends the phone change to review
    c.post("/plan/settings", data={"window_days": "60"})
    r = c.post("/practice/sample")
    bid = r.headers["Location"].rsplit("/", 1)[-1]
    res = logic.analyse(state_of(c), state_of(c)["batches"][bid])
    assert next(x for x in res["rows"] if x["row_no"] == 7)["route"] == "plan_review"

    # Every route renders; no em dashes; no NPI starting 1 or 2
    for path in ["/", "/practice", "/practice/confirm", f"/practice/review/{bid}", "/plan/queue", "/reconcile",
                 "/practice/status", "/plan/settings", "/dashboard", "/about", "/sample.csv"]:
        resp = c.get(path)
        assert resp.status_code == 200, path
        body = text(resp)
        assert "—" not in body, f"em dash on {path}"
        assert not re.search(r"\b[12]\d{9}\b", body), f"real-looking NPI on {path}"
    assert c.get("/practice/review/nope").status_code == 404
    print("Main flow OK")


if __name__ == "__main__":
    main()
