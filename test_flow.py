"""Walks the demo with Flask's test client and checks the acceptance points."""
import datetime as dt
import re

import app as webapp
import logic


def client():
    return webapp.app.test_client()


def state_of(c):
    return webapp.STATES[c.get_cookie(webapp.COOKIE).value]


def text(resp):
    return resp.get_data(as_text=True)


def main():
    today = dt.date.today().isoformat()
    c = client()
    assert c.get("/").status_code == 200
    s = state_of(c)
    assert logic.metrics(s)["north_star"] == {"value": 60, "n": 26, "d": 43}
    assert webapp.next_step(s)[0] == 1 and s["role"] == "practice"

    # 1-2. Sample file: counts and routes
    r = c.post("/practice/sample")
    bid = r.headers["Location"].rsplit("/", 1)[-1]
    res = logic.analyse(s, s["batches"][bid])
    assert res["counts"] == {"Unchanged": 6, "Added": 1, "Removed": 1, "Changed": 1, "Invalid": 1, "Duplicate": 1}
    routes = {x["row_no"]: x["route"] for x in res["rows"]}
    assert routes == {**{i: "none" for i in range(1, 7)}, 7: "automatic", 8: "plan_review", 9: "sent_back",
                      10: "plan_review", 11: "held"}
    assert res["rows"][7]["flagged"]
    c.post(f"/practice/review/{bid}/mapping", data={"field_2": "site"})
    banner = text(c.post(f"/practice/review/{bid}/submit", follow_redirects=True))
    assert "Submitted 5 changes (1 duplicate held)." in banner
    ch = {x["name"]: x for x in s["changes"].values() if x["type"] != "Duplicate"}
    mei, arjun = ch["Mei Lin"], ch["Arjun Patel"]
    ivy = next(x for x in s["changes"].values() if x["type"] == "Invalid")
    dup = next(x for x in s["changes"].values() if x["type"] == "Duplicate")
    phone = next(x for x in s["changes"].values() if x["route"] == "automatic")

    # Roles: a practice admin cannot decide; the queue is read-only
    q = text(c.get("/plan/queue"))
    assert "Only plan approvers can decide changes" in q and "Approve change" not in q
    c.post(f"/plan/change/{mei['id']}/approve", data={})
    assert mei["status"] == "In plan review" and s["blocked_attempts"] == 1

    # 3. Plan queue as approver: no pre-filled note; back-date choice
    q = text(c.get("/plan/queue?as=plan"))
    assert s["role"] == "plan" and "Practice confirmed last day of work" not in q
    assert "Optional note for the audit trail" in q and "Apply from today instead" in q
    c.post(f"/plan/change/{arjun['id']}/approve", data={"note": "", "date_choice": "today"})
    assert arjun["status"] == "In plan review"
    c.post(f"/plan/change/{arjun['id']}/approve", data={"note": "Practice confirmed the last day was recent.",
                                                      "date_choice": "today"})
    assert arjun["new"]["end_date"] == today and "Apply from today instead" in arjun["date_choice"]
    c.post(f"/plan/change/{mei['id']}/approve", data={"note": ""})
    assert [d["name"] for d in mei["destinations"]] == ["Provider database", "Directory", "Claims and prior auth"]
    assert mei["destinations"][2]["later"] and mei["status"] == "Waiting for systems"

    # 4. Reconciliation: claims tile never blocks Applied; one failure, fix and resend
    c.post("/reconcile/sync")
    assert phone["status"] == arjun["status"] == "Applied"
    assert mei["status"] == "Partially applied"
    rec = text(c.get("/reconcile"))
    assert "Later, shown for illustration" in rec and "1 of 2 systems updated" in rec
    assert "Removed from directory" in rec and f"Left, effective {today}" in rec and "Back-date choice" in rec
    c.post("/reconcile/sync")
    d = mei["destinations"][1]
    assert d["attempts"] == 1
    c.post(f"/reconcile/{mei['id']}/resend", data={"code": "207Q00000X"})
    assert mei["status"] == "Applied" and d["attempts"] == 2 and mei["destinations"][0]["attempts"] == 1
    assert mei["destinations"][2]["status"] == "Later"

    # 5. Practice status: removal wording, correct and resend, remove duplicate
    st = text(c.get("/practice/status?as=practice"))
    assert st.count('class="live"') == 2 and st.count('class="removed-live"') == 1
    still_bad = ivy["npi"][:-1] + str((int(ivy["npi"][-1]) + 1) % 10)
    c.post(f"/practice/change/{ivy['id']}/correct", data={"npi": still_bad})
    assert ivy["status"] == "Sent back"
    good = next(p["npi"] for p in s["providers"].values() if p["name"] == ivy["name"])
    c.post(f"/practice/change/{ivy['id']}/correct", data={"npi": good})
    assert ivy["status"] == "Confirmed" and ivy["npi"] == good
    c.post(f"/practice/change/{dup['id']}/remove-duplicate")
    assert dup["status"] == "Duplicate removed"

    # 6. Dashboard
    m = logic.metrics(s)
    assert m["north_star"] == {"value": 81, "n": 35, "d": 43}, m["north_star"]
    assert m["first_time"] == {"value": 60, "n": 3, "d": 5, "checks": 1, "plan": 0, "held": 1}, m["first_time"]
    assert m["applied"]["n"] == m["applied"]["d"] == 3 and m["directory_2d"]["value"] == 100
    gr = m["guardrails"]
    assert (gr["unauthorised"], gr["wrong_removals"], gr["errors_after_loading"], gr["access_incidents"]) == (0, 0, 0, 0)
    assert gr["blocked"] == 1
    dash = text(c.get("/dashboard?as=plan"))
    assert "Share of eligible records confirmed and current" in dash and "duplicates held: 1" in dash
    assert "Directory updated within 2 business days" in dash and "Commercial rule, directory only" in dash
    assert webapp.next_step(s)[0] == 7

    # Reverse creates a correcting change that both pilot destinations must acknowledge
    c.post(f"/reconcile/{phone['id']}/reverse")
    fix = s["changes"][phone["reversed_by"]]
    assert fix["status"] == "Waiting for systems"
    c.post("/reconcile/sync")
    assert fix["status"] == "Applied" and fix["version"] == "v2 → v3"
    assert logic.metrics(s)["guardrails"]["errors_after_loading"] == 1

    # Email intake gives the same two changes
    e = client()
    e.get("/")
    es = state_of(e)
    r = e.post("/practice/email", data={"email": logic.EMAIL_SAMPLE})
    ebid = r.headers["Location"].rsplit("/", 1)[-1]
    batch = es["batches"][ebid]
    assert [(i["action"], i["name"]) for i in batch["items"]] == [("Added", "Mei Lin"), ("Removed", "Arjun Patel")]
    assert not batch["mapping_confirmed"] and "Needs your confirmation" in text(e.get(f"/practice/review/{ebid}"))
    e.post(f"/practice/review/{ebid}/mapping", data={"date_1": today})
    e.post(f"/practice/review/{ebid}/submit")
    got = sorted((x["type"], x["name"], x["route"], x["source"]) for x in es["changes"].values())
    assert got == [("Added", "Mei Lin", "plan_review", "Email"), ("Removed", "Arjun Patel", "plan_review", "Email")], got
    assert "SFTP" in text(e.get("/about"))

    # Confirm no changes raises the north star
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

    # Settings: a practice admin can't change policy; turning phone off sends the phone change to review
    c.post("/plan/settings", data={"window_days": "60"})
    assert state_of(c)["settings"]["phone_auto"] is True
    pol = text(c.get("/plan/settings?as=plan"))
    assert "Tax ID or billing change" in pol and "Duplicate or conflict" in pol
    c.post("/plan/settings", data={"window_days": "60"})
    assert state_of(c)["settings"]["phone_auto"] is False
    r = c.post("/practice/sample")
    bid = r.headers["Location"].rsplit("/", 1)[-1]
    res = logic.analyse(state_of(c), state_of(c)["batches"][bid])
    assert next(x for x in res["rows"] if x["row_no"] == 7)["route"] == "plan_review"

    # Every route renders; no em dashes; no NPI starting 1 or 2
    pages = [(c, p) for p in ["/", "/practice", "/practice/confirm", f"/practice/review/{bid}", "/plan/queue",
                              "/reconcile", "/practice/status", "/plan/settings", "/dashboard", "/about", "/sample.csv"]]
    pages += [(e, f"/practice/review/{ebid}"), (e, "/practice/status"), (e, "/plan/queue")]
    for who, path in pages:
        resp = who.get(path)
        assert resp.status_code == 200, path
        body = text(resp)
        assert "—" not in body, f"em dash on {path}"
        assert not re.search(r"\b[12]\d{9}\b", body), f"real-looking NPI on {path}"
    assert c.get("/practice/review/nope").status_code == 404
    print("Acceptance checks OK")


if __name__ == "__main__":
    main()
