import csv
import json
import secrets
import threading
from collections import OrderedDict
from pathlib import Path

from flask import Flask, Response, abort, g, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import data
import logic

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
BRAND = json.loads((Path(__file__).parent / "brand.json").read_text())

NAV = [
    ("Practice", "practice"),
    ("Plan queue", "plan_queue"),
    ("Reconciliation", "reconcile"),
    ("Dashboard", "dashboard"),
    ("How it works", "about"),
]
PERSONAS = {
    "practice": "Lakeview Family Practice, office manager",
    "plan": "Northwind Health Plan, network analyst",
}
OPEN_STATUSES = {"In plan review", "Waiting for systems", "Partially applied"}

# Demo state lives in process memory, one entry per visitor. Run with a single worker.
COOKIE = "medcare_demo"
MAX_VISITORS = 500
STATES = OrderedDict()
LOCK = threading.Lock()


@app.before_request
def load_state():
    vid = request.cookies.get(COOKIE)
    with LOCK:
        if vid in STATES:
            STATES.move_to_end(vid)
            g.new_visitor = False
        else:
            # Unknown or missing id (first visit, restart, Render wake-up): seed quietly.
            vid = secrets.token_urlsafe(16)
            STATES[vid] = data.generate()
            g.new_visitor = True
            while len(STATES) > MAX_VISITORS:
                STATES.popitem(last=False)
        g.vid, g.state = vid, STATES[vid]
    if request.method == "GET" and request.endpoint in ("practice_status", "dashboard"):
        g.state.setdefault("seen", set()).add(request.endpoint)


@app.after_request
def save_cookie(resp):
    if getattr(g, "new_visitor", False):
        resp.set_cookie(COOKIE, g.vid, httponly=True, samesite="Lax", secure=request.is_secure,
                        max_age=60 * 60 * 24 * 7)
    return resp


DEMO_STEPS = ["Practice", "What changed", "Plan queue", "Reconciliation", "Practice status", "Dashboard"]


def next_step(state):
    """Where a judge should go next, worked out from this visitor's state."""
    batches = list(state["batches"].values())
    changes = list(state["changes"].values())
    seen = state.setdefault("seen", set())
    open_batch = next((b for b in reversed(batches) if not b["submitted"]), None)
    directory = [d for c in changes for d in c["destinations"] if d["name"] == "Directory"]
    if not batches:
        return 1, "Click Use sample file", "practice", {}
    if open_batch:
        return 2, "Confirm the mapping, then Submit changes", "review", {"bid": open_batch["id"]}
    if any(c["status"] == "In plan review" for c in changes):
        return 3, "Approve the changes in the plan queue", "plan_queue", {}
    if any(d["status"] == "Pending" for d in directory):
        return 4, "Click Run directory sync", "reconcile", {}
    if any(d["status"] == "Failed" for d in directory):
        return 4, "Fix and resend the rejected change", "reconcile", {}
    if "practice_status" not in seen:
        return 5, "See what Lakeview sees", "practice_status", {}
    if "dashboard" not in seen:
        return 6, "See the dashboard", "dashboard", {}
    return 7, "Demo complete. Reset demo to run it again.", None, {}


@app.context_processor
def inject():
    flashes, g.state["flash"] = g.state["flash"], []
    step, text, endpoint, args = next_step(g.state)
    guide = {"step": step, "text": text, "here": endpoint == request.endpoint and
             (not args or request.view_args == args),
             "url": url_for(endpoint, **args) if endpoint else None}
    return {"brand": BRAND, "nav": NAV, "flashes": flashes, "plan_name": data.PLAN,
            "sites": data.SITES, "practices": data.PRACTICES, "personas": PERSONAS,
            "codes": data.DIRECTORY_CODES, "settings": g.state["settings"],
            "guide": guide, "demo_steps": DEMO_STEPS}


def flash(msg, kind="info"):
    g.state["flash"].append({"msg": msg, "kind": kind})


def lakeview_roster(state):
    rows = []
    for a in state["affiliations"].values():
        if a["practice"] == "lakeview":
            p = state["providers"][a["provider_id"]]
            rows.append({**a, "name": p["name"], "npi": p["npi"], "specialty": p["specialty"],
                         "site_name": data.SITES[a["site"]]["name"], "confirmed": a["id"] in state["confirmed"]})
    rows.sort(key=lambda r: (r["status"] != "Active", r["name"], r["site_name"]))
    return rows


def get_change(cid):
    c = g.state["changes"].get(cid)
    if not c:
        abort(404)
    return c


# Pages --------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("home.html", active="home")


@app.route("/practice")
def practice():
    s = g.state
    roster = lakeview_roster(s)
    active = [r for r in roster if r["status"] == "Active"]
    open_changes = [c for c in s["changes"].values() if c["status"] in OPEN_STATUSES]
    return render_template("practice.html", active="practice", persona="practice", roster=roster,
                           n_active=len(active), n_clinicians=len({r["provider_id"] for r in active}),
                           n_confirmed=sum(r["confirmed"] for r in active), open_changes=open_changes,
                           attestations=s["attestations"])


@app.route("/sample.csv")
def sample_csv():
    return Response(data.sample_csv(g.state), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample_lakeview.csv"})


def make_batch(text, source):
    try:
        headers, rows = logic.read_csv(text)
    except (ValueError, csv.Error) as e:
        flash(f"Could not read that file. {e}", "error")
        return redirect(url_for("practice"))
    bid = f"B{len(g.state['batches']) + 1:02d}"
    mapping = logic.map_headers(headers)
    g.state["batches"][bid] = {"id": bid, "source": source, "headers": headers, "rows": rows,
                              "mapping": mapping, "created": logic.now(), "submitted": False,
                              "mapping_confirmed": not any(m["needs_confirm"] for m in mapping)}
    return redirect(url_for("review", bid=bid))


@app.post("/practice/sample")
def use_sample():
    return make_batch(data.sample_csv(g.state), "Sample file")


@app.post("/practice/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a CSV file first.", "error")
        return redirect(url_for("practice"))
    try:
        text = f.read(2_000_000).decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("That file is not a readable CSV.", "error")
        return redirect(url_for("practice"))
    return make_batch(text, f"Uploaded file ({f.filename})")


@app.route("/practice/confirm", methods=["GET", "POST"])
def confirm():
    s = g.state
    roster = [r for r in lakeview_roster(s) if r["status"] == "Active"]
    open_changes = [c for c in s["changes"].values() if c["status"] in OPEN_STATUSES]
    if request.method == "POST":
        if request.form.get("agree") != "yes":
            flash("Tick the box to confirm the affiliations are accurate.", "error")
            return redirect(url_for("confirm"))
        t = logic.now()
        scope = [r["id"] for r in roster]
        for aid in scope:
            s["confirmed"][aid] = {"time": t, "by": PERSONAS["practice"], "source": "Confirmation"}
        s["attestations"].append({"time": t, "by": PERSONAS["practice"], "scope": scope,
                                  "source": "Confirm no changes"})
        flash(f"Confirmation recorded for {len(scope)} affiliations at {t}.", "success")
        return redirect(url_for("practice"))
    return render_template("confirm.html", active="practice", persona="practice", roster=roster,
                           open_changes=open_changes,
                           n_clinicians=len({r["provider_id"] for r in roster}),
                           n_sites=len({r["site"] for r in roster}))


def open_keys(state):
    return {(c["npi"], c["site"]): c["id"] for c in state["changes"].values()
            if c["status"] in OPEN_STATUSES}


def analyse(batch):
    result = logic.analyse(g.state, batch)
    if not batch["submitted"]:
        keys = open_keys(g.state)
        for r in result["rows"]:
            cid = keys.get((r["npi"], r["site"]))
            if cid and r["route"] in ("automatic", "plan_review"):
                r.update(route="held", why=f"Already in progress as {cid}. Not submitted again.")
    return result


@app.route("/practice/review/<bid>")
def review(bid):
    batch = g.state["batches"].get(bid) or abort(404)
    result = batch.get("result") if batch["submitted"] else analyse(batch)
    return render_template("review.html", active="practice", persona="practice", batch=batch,
                           result=result, fields=logic.FIELDS, labels=logic.FIELD_LABELS,
                           floor=logic.CONFIDENCE_FLOOR)


@app.post("/practice/review/<bid>/mapping")
def confirm_mapping(bid):
    batch = g.state["batches"].get(bid) or abort(404)
    if batch["submitted"]:
        return redirect(url_for("review", bid=bid))
    for i, m in enumerate(batch["mapping"]):
        choice = request.form.get(f"field_{i}")
        if choice in logic.FIELDS:
            if choice != m["field"]:
                m["field"], m["confidence"] = choice, 100
            m["confirmed_by_person"] = m["needs_confirm"] or m.get("confirmed_by_person", False)
    batch["mapping_confirmed"] = True
    flash("Column mapping confirmed.", "success")
    return redirect(url_for("review", bid=bid) + "#changes")


@app.post("/practice/review/<bid>/submit")
def submit(bid):
    s = g.state
    batch = s["batches"].get(bid) or abort(404)
    if batch["submitted"]:
        return redirect(url_for("practice_status"))
    if not batch["mapping_confirmed"]:
        flash("Confirm the column mapping before submitting.", "error")
        return redirect(url_for("review", bid=bid))
    result = analyse(batch)
    t = logic.now()
    created, scope = [], []
    for r in result["rows"]:
        if r["type"] == "Unchanged" and r["route"] == "none":
            s["confirmed"][r["aff_id"]] = {"time": t, "by": PERSONAS["practice"], "source": "File"}
            scope.append(r["aff_id"])
            continue
        old, new, fields = {}, {}, list(r["changes"])
        specialty = r["specialty"]
        if r["aff_id"]:
            a = s["affiliations"][r["aff_id"]]
            specialty = s["providers"][a["provider_id"]]["specialty"]
        if r["type"] == "Added":
            new = {"specialty": r["specialty"], "phone": r["phone"], "start_date": r["start_date"]}
            fields = [{"field": "affiliation", "label": "Affiliation", "old": "Not listed",
                       "new": f"Joins {r['site_name']} as {r['specialty']}"}]
        elif r["type"] == "Removed":
            new = {"end_date": r["effective_date"]}
            fields = [{"field": "status", "label": "Status", "old": "Active",
                       "new": f"Left, effective {r['effective_date']}"}]
        elif r["type"] == "Changed":
            new = {c["field"]: c["new"] for c in r["changes"]}
            old = {c["field"]: c["old"] for c in r["changes"]}
            specialty = new.get("specialty", specialty)
        status = {"automatic": "Waiting for systems", "plan_review": "In plan review",
                  "sent_back": "Sent back", "held": "Held"}.get(r["route"], "Held")
        c = logic.new_change(s, source="File", batch_id=bid, row_no=r["row_no"], type=r["type"],
                             name=r["name"], npi=r["npi"], site=r["site"], site_name=r["site_name"],
                             aff_id=r["aff_id"], specialty=specialty, old=old, new=new,
                             changed_fields=fields, checks=r["checks"], route=r["route"], why=r["why"],
                             flagged=r["flagged"], status=status, submitted_at=t, checked_at=t,
                             matched_name=r.get("matched_name"))
        logic.event(c, "Submitted by Lakeview")
        logic.event(c, "Checked by MedCare")
        if r["route"] == "automatic":
            c["decision"] = {"action": "Automatic", "by": "Plan policy", "time": t, "note": r["why"]}
            logic.dispatch(s, c)
        created.append(c)
        r["change_id"] = c["id"]
    if scope:
        s["attestations"].append({"time": t, "by": PERSONAS["practice"], "scope": scope,
                                  "source": f"Full roster file {bid}"})
    batch["submitted"], batch["result"], batch["submitted_at"] = True, result, t
    review_n = sum(1 for c in created if c["route"] == "plan_review")
    flash(f"Submitted {len(created)} changes. {review_n} went to the plan for review; "
          f"{len(scope)} unchanged affiliations were confirmed.", "success")
    return redirect(url_for("practice_status"))


@app.route("/practice/status")
def practice_status():
    changes = sorted(g.state["changes"].values(), key=lambda c: c["id"], reverse=True)
    return render_template("status.html", active="practice", persona="practice", changes=changes,
                           attestations=list(reversed(g.state["attestations"])))


@app.route("/plan/queue")
def plan_queue():
    changes = list(g.state["changes"].values())
    waiting = [c for c in changes if c["status"] == "In plan review"]
    decided = [c for c in changes if c["decision"] and c["decision"]["action"] in ("Approved", "Rejected")
               and c["source"] != "Correction"]
    return render_template("queue.html", active="plan_queue", persona="plan", waiting=waiting,
                           decided=list(reversed(decided)), roster=g.state["affiliations"],
                           providers=g.state["providers"])


@app.post("/plan/change/<cid>/approve")
def approve(cid):
    c = get_change(cid)
    if c["status"] != "In plan review":
        return redirect(url_for("plan_queue"))
    note = request.form.get("note", "").strip()
    if c["flagged"] and not note:
        flash(f"{cid} is flagged. Add an override note to approve it.", "error")
        return redirect(url_for("plan_queue") + f"#{cid}")
    c["decision"] = {"action": "Approved", "by": PERSONAS["plan"], "time": logic.now(), "note": note}
    logic.event(c, "Approved by Northwind" + (f": {note}" if note else ""))
    logic.dispatch(g.state, c)
    flash(f"{cid} approved and sent to the provider database, claims and prior auth, and directory.", "success")
    return redirect(url_for("plan_queue"))


@app.post("/plan/change/<cid>/reject")
def reject(cid):
    c = get_change(cid)
    if c["status"] != "In plan review":
        return redirect(url_for("plan_queue"))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A reason is required to reject a change. The practice will see it.", "error")
        return redirect(url_for("plan_queue") + f"#{cid}")
    c["decision"] = {"action": "Rejected", "by": PERSONAS["plan"], "time": logic.now(), "note": reason}
    c["status"] = "Rejected by plan"
    logic.event(c, f"Sent back by plan: {reason}")
    flash(f"{cid} rejected. Lakeview will see the reason.", "success")
    return redirect(url_for("plan_queue"))


@app.route("/reconcile")
def reconcile():
    changes = [c for c in g.state["changes"].values() if c["destinations"]]
    changes.sort(key=lambda c: ({"Partially applied": 0, "Waiting for systems": 1}.get(c["status"], 2), c["id"]))
    pending = sum(1 for c in changes for d in c["destinations"] if d["name"] == "Directory" and d["status"] == "Pending")
    return render_template("reconcile.html", active="reconcile", persona="plan", changes=changes,
                           pending=pending, saved_map=g.state["saved_specialty_map"],
                           registry_codes=g.state["registry_taxonomy"])


@app.post("/reconcile/sync")
def sync():
    acked, failed = logic.run_directory_sync(g.state)
    if not acked and not failed:
        flash("Nothing is waiting for the directory.", "info")
    else:
        flash(f"Directory sync finished: {acked} acknowledged, {failed} rejected.",
              "error" if failed else "success")
    return redirect(url_for("reconcile"))


@app.post("/reconcile/<cid>/resend")
def resend(cid):
    c = get_change(cid)
    code = request.form.get("code", "")
    if code not in data.DIRECTORY_CODES.values():
        flash("Pick a directory specialty code first.", "error")
        return redirect(url_for("reconcile") + f"#{cid}")
    if logic.fix_and_resend(g.state, c, code):
        flash(f"{cid} resent to the directory and acknowledged. Status: {c['status']}.", "success")
    return redirect(url_for("reconcile") + f"#{cid}")


@app.post("/reconcile/<cid>/reverse")
def reverse(cid):
    c = get_change(cid)
    if c["status"] != "Applied" or c["reversed_by"] or c["source"] == "Correction":
        return redirect(url_for("reconcile"))
    new = logic.reverse(g.state, c)
    flash(f"Created {new['id']} to reverse {cid}. It needs both systems to acknowledge.", "success")
    return redirect(url_for("reconcile") + f"#{new['id']}")


@app.route("/plan/settings", methods=["GET", "POST"])
def plan_settings():
    st = g.state["settings"]
    if request.method == "POST":
        st["phone_auto"] = request.form.get("phone_auto") == "on"
        try:
            days = int(request.form.get("window_days", st["window_days"]))
            if not 1 <= days <= 365:
                raise ValueError
            st["window_days"] = days
        except ValueError:
            flash("The back-date window must be a whole number from 1 to 365.", "error")
            return redirect(url_for("plan_settings"))
        flash("Settings saved. They apply to the next file.", "success")
        return redirect(url_for("plan_settings"))
    return render_template("settings.html", active="plan_queue", persona="plan")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", active="dashboard", persona="plan", m=logic.metrics(g.state))


@app.route("/about")
def about():
    return render_template("about.html", active="about")


@app.post("/reset")
def reset():
    with LOCK:
        STATES[g.vid] = data.generate()
    STATES[g.vid]["flash"].append({"msg": "Demo reset to the seeded start.", "kind": "success"})
    return redirect(url_for("home"))


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", message="That page or change does not exist in this demo."), 404


if __name__ == "__main__":
    app.run(debug=True, port=5050)
