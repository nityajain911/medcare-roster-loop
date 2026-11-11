import csv
import datetime as dt
import io
import json
import re
import secrets
import threading
from collections import OrderedDict
from functools import wraps
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
# Roles from deck slide 12. Only plan approvers can decide changes.
ROLES = {
    "practice": {"title": "Practice admin", "org": "Lakeview Family Practice", "short": "Practice admin, Lakeview"},
    "plan": {"title": "Plan analyst (approver)", "org": "Northwind Health Plan", "short": "Plan analyst, Northwind"},
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
    wanted = request.args.get("as")
    if wanted in ROLES and wanted != g.state["role"]:
        g.state["role"] = wanted
        flash(f"Now viewing as {ROLES[wanted]['short']}.", "role")
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
        return 1, "Click Use sample file", "practice", {}, "practice"
    if open_batch:
        what = "Confirm what the AI read" if open_batch.get("kind") == "email" else "Confirm the mapping"
        return 2, f"{what}, then Submit changes", "review", {"bid": open_batch["id"]}, "practice"
    if any(c["status"] == "In plan review" for c in changes):
        return 3, "Approve the changes in the plan queue", "plan_queue", {}, "plan"
    if any(d["status"] == "Pending" for d in directory):
        return 4, "Click Run directory sync", "reconcile", {}, "plan"
    if any(d["status"] == "Failed" for d in directory):
        return 4, "Fix and resend the rejected change", "reconcile", {}, "plan"
    if "practice_status" not in seen:
        return 5, "See what Lakeview sees", "practice_status", {}, "practice"
    if "dashboard" not in seen:
        return 6, "See the dashboard", "dashboard", {}, "plan"
    return 7, "Demo complete. Reset demo to run it again.", None, {}, None


@app.context_processor
def inject():
    flashes, g.state["flash"] = g.state["flash"], []
    step, text, endpoint, args, role = next_step(g.state)
    here = endpoint == request.endpoint and (not args or request.view_args == args) and g.state["role"] == role
    guide = {"step": step, "text": text, "here": here,
             "url": url_for(endpoint, **args, **{"as": role}) if endpoint else None}
    return {"brand": BRAND, "nav": NAV, "flashes": flashes, "plan_name": data.PLAN,
            "sites": data.SITES, "practices": data.PRACTICES, "roles": ROLES, "role": g.state["role"],
            "codes": data.DIRECTORY_CODES, "settings": g.state["settings"],
            "guide": guide, "demo_steps": DEMO_STEPS}


def flash(msg, kind="info"):
    g.state["flash"].append({"msg": msg, "kind": kind})


@app.template_global()
def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def actor():
    r = ROLES[g.state["role"]]
    return f"{r['title']}, {r['org']}"


def approver_only(view):
    """Plan decisions need the plan approver role. Blocked attempts are counted, not applied."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.state["role"] != "plan":
            g.state["blocked_attempts"] = g.state.get("blocked_attempts", 0) + 1
            flash("Only plan approvers can decide changes.", "error")
            return redirect(request.referrer or url_for("plan_queue"))
        return view(*args, **kwargs)
    return wrapped


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


@app.post("/role")
def switch_role():
    g.state["role"] = "plan" if g.state["role"] == "practice" else "practice"
    flash(f"Now viewing as {ROLES[g.state['role']]['short']}.", "role")
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("home"))


@app.route("/practice")
def practice():
    s = g.state
    roster = lakeview_roster(s)
    active = [r for r in roster if r["status"] == "Active"]
    return render_template("practice.html", active="practice", area="practice", roster=roster,
                           n_active=len(active), n_clinicians=len({r["provider_id"] for r in active}),
                           n_confirmed=sum(r["confirmed"] for r in active), email_sample=logic.EMAIL_SAMPLE)


@app.route("/sample.csv")
def sample_csv():
    return Response(data.sample_csv(g.state), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample_lakeview.csv"})


def new_batch_id():
    return f"B{len(g.state['batches']) + 1:02d}"


def make_batch(text, source):
    try:
        headers, rows = logic.read_csv(text)
    except (ValueError, csv.Error) as e:
        flash(f"Could not read that file. {e}", "error")
        return redirect(url_for("practice"))
    bid = new_batch_id()
    mapping = logic.map_headers(headers)
    g.state["batches"][bid] = {"id": bid, "kind": "file", "source": source, "headers": headers, "rows": rows,
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


@app.post("/practice/email")
def read_email():
    text = request.form.get("email", "").strip()[:5000]
    if not text:
        flash("Paste an email first, or click Use sample email.", "error")
        return redirect(url_for("practice") + "#email")
    items = logic.read_email(g.state, text)
    if not items:
        flash("No roster changes found in that email. Name the clinician (Dr First Last) and what changed.", "error")
        return redirect(url_for("practice") + "#email")
    bid = new_batch_id()
    g.state["batches"][bid] = {"id": bid, "kind": "email", "source": "Email", "email": text, "items": items,
                              "segments": logic.email_segments(text, items),
                              "rows": logic.email_rows(items), "mapping": logic.IDENTITY_MAPPING,
                              "created": logic.now(), "submitted": False,
                              "mapping_confirmed": not any(i["needs_confirm"] for i in items)}
    return redirect(url_for("review", bid=bid))


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
            s["confirmed"][aid] = {"time": t, "by": actor(), "source": "Confirmation"}
        s["attestations"].append({"time": t, "by": actor(), "scope": scope, "source": "Confirm no changes"})
        flash(f"{plural(len(scope), 'affiliation')} confirmed.", "success")
        return redirect(url_for("practice"))
    return render_template("confirm.html", active="practice", area="practice", roster=roster,
                           open_changes=open_changes,
                           n_clinicians=len({r["provider_id"] for r in roster}),
                           n_sites=len({r["site"] for r in roster}))


def open_keys(state, exclude=None):
    return {(c["npi"], c["site"]): c["id"] for c in state["changes"].values()
            if c["status"] in OPEN_STATUSES and c["id"] != exclude}


def analyse(batch, exclude=None):
    result = logic.analyse(g.state, batch)
    if not batch["submitted"]:
        keys = open_keys(g.state, exclude)
        for r in result["rows"]:
            cid = keys.get((r["npi"], r["site"]))
            if cid and r["route"] in ("automatic", "plan_review"):
                r.update(route="held", why=f"Already in progress ({cid}).")
    return result


ROUTE_STATUS = {"automatic": "Waiting for systems", "plan_review": "In plan review",
                "sent_back": "Sent back", "held": "Held", "none": "Confirmed"}


def change_details(s, r):
    """What a routed row changes: old and new values, the diff lines, and the specialty the directory sees."""
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
    return old, new, fields, specialty


def raw_row(r):
    return {"name": r["name"], "npi": r["npi"], "site": r["site_name"], "specialty": r["specialty"],
            "phone": r["phone"], "start_date": r["start_date"], "status": r["status"],
            "effective_date": r["effective_date"]}


def route_change(s, c, t):
    """Start a routed change on its way: automatic changes go straight to the destinations."""
    if c["route"] == "automatic":
        c["decision"] = {"action": "Automatic", "by": "Plan policy", "time": t, "note": c["why"]}
        logic.dispatch(s, c)


@app.route("/practice/review/<bid>")
def review(bid):
    batch = g.state["batches"].get(bid) or abort(404)
    result = batch.get("result") if batch["submitted"] else analyse(batch)
    return render_template("review.html", active="practice", area="practice", batch=batch,
                           result=result, fields=logic.FIELDS, labels=logic.FIELD_LABELS,
                           floor=logic.CONFIDENCE_FLOOR, registry=sorted(g.state["registry"]))


@app.post("/practice/review/<bid>/mapping")
def confirm_mapping(bid):
    batch = g.state["batches"].get(bid) or abort(404)
    if batch["submitted"]:
        return redirect(url_for("review", bid=bid))
    if batch.get("kind") == "email":
        for i, item in enumerate(batch["items"]):
            date = logic.parse_date(request.form.get(f"date_{i}", ""))
            if item["needs_confirm"] and date:
                item["row"]["effective_date"] = date.isoformat()
                if item["action"] == "Added":
                    item["row"]["start_date"] = date.isoformat()
            item["confirmed_by_person"] = item["needs_confirm"]
        batch["rows"] = logic.email_rows(batch["items"])
        batch["mapping_confirmed"] = True
        return redirect(url_for("review", bid=bid) + "#changes")
    for i, m in enumerate(batch["mapping"]):
        choice = request.form.get(f"field_{i}")
        if choice in logic.FIELDS:
            if choice != m["field"]:
                m["field"], m["confidence"] = choice, 100
            m["confirmed_by_person"] = m["needs_confirm"] or m.get("confirmed_by_person", False)
    batch["mapping_confirmed"] = True
    return redirect(url_for("review", bid=bid) + "#changes")


@app.post("/practice/review/<bid>/submit")
def submit(bid):
    s = g.state
    batch = s["batches"].get(bid) or abort(404)
    if batch["submitted"]:
        return redirect(url_for("practice_status"))
    if not batch["mapping_confirmed"]:
        flash("Confirm the mapping before submitting.", "error")
        return redirect(url_for("review", bid=bid))
    # Fixes made on this page: corrected NPIs are checked again; dropped duplicates are not sent.
    npi_col = next(i for i, m in enumerate(batch["mapping"]) if m["field"] == "npi")
    fixed = set()
    for i, row in enumerate(batch["rows"]):
        new_npi = re.sub(r"\D", "", request.form.get(f"npi_fix_{i + 1}", ""))
        if new_npi and new_npi != re.sub(r"\D", "", row[npi_col]):
            row[npi_col] = new_npi
            fixed.add(i + 1)
    dropped = {int(k[5:]) for k, v in request.form.items() if k.startswith("drop_") and v == "1" and k[5:].isdigit()}
    result = analyse(batch)
    t = logic.now()
    source = "Email" if batch.get("kind") == "email" else "File"
    created, scope = [], []
    for r in result["rows"]:
        r["fixed_inline"] = r["row_no"] in fixed
        if r["row_no"] in dropped and r["type"] == "Duplicate":
            r["dropped"] = True
            continue
        if r["type"] == "Unchanged" and r["route"] == "none":
            s["confirmed"][r["aff_id"]] = {"time": t, "by": actor(), "source": source}
            scope.append(r["aff_id"])
            continue
        old, new, fields, specialty = change_details(s, r)
        c = logic.new_change(s, source=source, batch_id=bid, row_no=r["row_no"], type=r["type"],
                             name=r["name"], npi=r["npi"], site=r["site"], site_name=r["site_name"],
                             aff_id=r["aff_id"], specialty=specialty, old=old, new=new,
                             changed_fields=fields, checks=r["checks"], route=r["route"], first_route=r["route"],
                             why=r["why"], flagged=r["flagged"], status=ROUTE_STATUS.get(r["route"], "Held"),
                             submitted_at=t, checked_at=t, matched_name=r.get("matched_name"), row=raw_row(r),
                             npi_note=("NPI matched from synthetic registry by name"
                                       if source == "Email" and r["type"] == "Added" else ""))
        logic.event(c, f"Submitted by Lakeview ({source.lower()})")
        logic.event(c, "Checked by MedCare")
        route_change(s, c, t)
        created.append(c)
        r["change_id"] = c["id"]
    if scope:
        s["attestations"].append({"time": t, "by": actor(), "scope": scope, "source": f"Full roster file {bid}"})
    batch["submitted"], batch["result"], batch["submitted_at"] = True, result, t
    review_n = sum(1 for c in created if c["route"] == "plan_review")
    held_n = sum(1 for c in created if c["route"] == "held")
    msg = f"Submitted {plural(len(created), 'change')}"
    msg += f" ({held_n} duplicate held)." if held_n else "."
    msg += f" {review_n} need plan review."
    flash(msg, "success")
    return redirect(url_for("practice_status"))


@app.post("/practice/change/<cid>/correct")
def correct_npi(cid):
    """Practice corrects a sent-back NPI. The row is checked again and routed as normal."""
    s = g.state
    c = get_change(cid)
    if c["status"] != "Sent back":
        return redirect(url_for("practice_status"))
    npi = re.sub(r"\D", "", request.form.get("npi", ""))
    old_npi = c["npi"]
    row = {**c["row"], "npi": npi}
    batch = {"mapping": logic.IDENTITY_MAPPING, "rows": [[row[f] for f in logic.FIELDS]], "submitted": False}
    r = analyse(batch, exclude=cid)["rows"][0]
    t = logic.now()
    old, new, fields, specialty = change_details(s, r)
    c.update(type=r["type"], npi=npi, site=r["site"], aff_id=r["aff_id"], specialty=specialty, old=old, new=new,
             changed_fields=fields, checks=r["checks"], route=r["route"], why=r["why"], flagged=r["flagged"],
             status=ROUTE_STATUS.get(r["route"], "Held"), checked_at=t, row={**c["row"], "npi": npi})
    logic.event(c, f"Corrected by practice: NPI {old_npi} → {npi}")
    logic.event(c, "Checked again by MedCare")
    if r["route"] == "none":
        s["confirmed"][r["aff_id"]] = {"time": t, "by": actor(), "source": "Correction"}
        s["attestations"].append({"time": t, "by": actor(), "scope": [r["aff_id"]], "source": f"Corrected {cid}"})
        c["why"] = "NPI corrected. Matches the record on file."
        flash(f"{cid}: NPI corrected and confirmed.", "success")
    elif r["route"] == "sent_back":
        flash(f"{cid}: still fails. {r['why']}", "error")
    else:
        route_change(s, c, t)
        flash(f"{cid}: NPI corrected. {r['why']}", "success")
    return redirect(url_for("practice_status") + f"#{cid}")


@app.post("/practice/change/<cid>/remove-duplicate")
def remove_duplicate(cid):
    c = get_change(cid)
    if c["status"] == "Held" and c["type"] == "Duplicate":
        c["status"] = "Duplicate removed"
        logic.event(c, "Duplicate row removed by practice")
        flash(f"{cid}: duplicate removed.", "success")
    return redirect(url_for("practice_status") + f"#{cid}")


@app.route("/practice/status")
def practice_status():
    changes = sorted(g.state["changes"].values(), key=lambda c: c["id"], reverse=True)
    return render_template("status.html", active="practice", area="practice", changes=changes,
                           attestations=list(reversed(g.state["attestations"])), today=dt.date.today().isoformat())


@app.route("/practice/status/export.csv")
def export_confirmation():
    """Applied changes and confirmed records for Lakeview, as the practice's proof of what is live."""
    s = g.state
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Clinician", "Site", "Change", "Effective date", "Applied time", "Acknowledged by"])
    done = set()
    for c in sorted(s["changes"].values(), key=lambda c: c["id"]):
        if c["status"] != "Applied":
            continue
        eff = c["new"].get("end_date") or (c.get("row") or {}).get("effective_date") or c["new"].get("start_date", "")
        acked = "; ".join(d["name"] for d in logic.required(c) if d["status"] == "Acknowledged")
        w.writerow([c["name"], c["site_name"], c["type"], eff, c.get("applied_at", ""), acked])
        done.add(c.get("aff_id"))
    for aid, info in s["confirmed"].items():
        a = s["affiliations"][aid]
        if a["practice"] != "lakeview" or aid in done:
            continue
        w.writerow([s["providers"][a["provider_id"]]["name"], data.SITES[a["site"]]["name"],
                    "Confirmed, no change", "", info["time"], "Not needed"])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=lakeview_confirmation.csv"})


def date_line(state, c):
    """Requested date, window limit and today, for a back-dated change."""
    requested = logic.parse_date((c.get("row") or {}).get("effective_date", ""))
    if not (c["flagged"] and requested):
        return None
    today = dt.date.today()
    limit = today - dt.timedelta(days=state["settings"]["window_days"])
    return {"requested": requested.isoformat(), "limit": limit.isoformat(), "today": today.isoformat(),
            "gap": (limit - requested).days, "window": state["settings"]["window_days"]}


@app.route("/plan/queue")
def plan_queue():
    changes = list(g.state["changes"].values())
    waiting = [c for c in changes if c["status"] == "In plan review"]
    decided = [c for c in changes if c["decision"] and c["decision"]["action"] in ("Approved", "Rejected")
               and c["source"] != "Correction"]
    automatic = [c for c in changes if c["route"] == "automatic"]
    return render_template("queue.html", active="plan_queue", area="plan", waiting=waiting,
                           decided=list(reversed(decided)), today=dt.date.today().isoformat(),
                           automatic=automatic, date_lines={c["id"]: date_line(g.state, c) for c in waiting})


@app.post("/plan/change/<cid>/approve")
@approver_only
def approve(cid):
    c = get_change(cid)
    if c["status"] != "In plan review":
        return redirect(url_for("plan_queue"))
    note = request.form.get("note", "").strip()
    if c["flagged"] and not note:
        flash(f"{cid} is flagged. Add a reason for the audit trail to approve it.", "error")
        return redirect(url_for("plan_queue") + f"#{cid}")
    if c["flagged"] and c["type"] == "Removed":
        original, today = c["new"]["end_date"], dt.date.today().isoformat()
        if request.form.get("date_choice") == "today":
            c["new"]["end_date"] = today
            c["changed_fields"][0]["new"] = f"Left, effective {today}"
            c["date_choice"] = f"Apply from today instead ({today}). File said {original}."
            c["plan_changed_date"] = f"The plan applied this from {today} instead of {original}."
        else:
            c["date_choice"] = f"Apply with original date ({original})."
        logic.event(c, c["date_choice"])
    c["decision"] = {"action": "Approved", "by": actor(), "role": "plan", "time": logic.now(), "note": note}
    logic.event(c, "Approved by Northwind" + (f": {note}" if note else ""))
    logic.dispatch(g.state, c)
    flash(f"{cid} approved.", "success")
    return redirect(url_for("plan_queue"))


@app.post("/plan/change/<cid>/reject")
@approver_only
def reject(cid):
    c = get_change(cid)
    if c["status"] != "In plan review":
        return redirect(url_for("plan_queue"))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A reason is required to reject a change. The practice will see it.", "error")
        return redirect(url_for("plan_queue") + f"#{cid}")
    c["decision"] = {"action": "Rejected", "by": actor(), "role": "plan", "time": logic.now(), "note": reason}
    c["status"] = "Rejected by plan"
    logic.event(c, f"Sent back by plan: {reason}")
    flash(f"{cid} rejected. The practice sees your reason.", "success")
    return redirect(url_for("plan_queue"))


@app.route("/reconcile")
def reconcile():
    changes = [c for c in g.state["changes"].values() if c["destinations"]]
    changes.sort(key=lambda c: ({"Partially applied": 0, "Waiting for systems": 1}.get(c["status"], 2), c["id"]))
    pending = sum(1 for c in changes for d in c["destinations"] if d["name"] == "Directory" and d["status"] == "Pending")
    pipe = pipeline(changes)
    key = (pipe["db"]["state"], pipe["db"]["label"], pipe["dir"]["state"], pipe["dir"]["label"])
    pipe["animate"] = g.state.get("last_pipeline") not in (None, key)
    g.state["last_pipeline"] = key
    return render_template("reconcile.html", active="reconcile", area="plan", changes=changes, pipe=pipe,
                           pending=pending, saved_map=g.state["saved_specialty_map"],
                           registry_codes=g.state["registry_taxonomy"])


def pipeline(changes):
    """Connector states for Practice submission → Provider database → Directory, from every sent change."""
    n = len(changes)
    db = sum(1 for c in changes for d in c["destinations"] if d["name"] == "Provider database" and d["status"] == "Acknowledged")
    dirs = [d["status"] for c in changes for d in c["destinations"] if d["name"] == "Directory"]
    acked, pending, failed = dirs.count("Acknowledged"), dirs.count("Pending"), dirs.count("Failed")
    if not n:
        return {"db": {"state": "idle", "label": "Nothing sent"}, "dir": {"state": "idle", "label": "Nothing sent"}}
    db_state = {"state": "ok" if db == n else "pending", "label": f"{db} of {n} acknowledged"}
    if failed:
        dir_state = {"state": "failed", "label": f"{failed} failed · {acked} of {n} acknowledged"}
    elif pending:
        dir_state = {"state": "pending", "label": f"{pending} pending · {acked} of {n} acknowledged"}
    else:
        dir_state = {"state": "ok", "label": f"{acked} of {n} acknowledged"}
    return {"db": db_state, "dir": dir_state}


@app.post("/reconcile/sync")
@approver_only
def sync():
    acked, failed = logic.run_directory_sync(g.state)
    if not acked and not failed:
        flash("Nothing is waiting for the directory.", "info")
    else:
        flash(f"Directory sync finished: {acked} acknowledged, {failed} rejected.",
              "error" if failed else "success")
    return redirect(url_for("reconcile"))


@app.post("/reconcile/<cid>/resend")
@approver_only
def resend(cid):
    c = get_change(cid)
    code = request.form.get("code", "")
    if code not in data.DIRECTORY_CODES.values():
        flash("Pick a directory specialty code first.", "error")
        return redirect(url_for("reconcile") + f"#{cid}")
    if logic.fix_and_resend(g.state, c, code):
        flash(f"{cid} resent. Now {c['status']}.", "success")
    return redirect(url_for("reconcile") + f"#{cid}")


@app.post("/reconcile/<cid>/reverse")
@approver_only
def reverse(cid):
    c = get_change(cid)
    if c["status"] != "Applied" or c["reversed_by"] or c["source"] == "Correction":
        return redirect(url_for("reconcile"))
    new = logic.reverse(g.state, c)
    flash(f"{new['id']} created to reverse {cid}.", "success")
    return redirect(url_for("reconcile") + f"#{new['id']}")


POLICY = [
    ("No change confirmation", "Recorded", ""),
    ("Phone or hours change", "Automatic if allowed, else plan review", "Toggle below"),
    ("New clinician", "Plan review", ""),
    ("Removal", "Plan review, flagged if back-dated", ""),
    ("Tax ID or billing change", "Plan review with extra checks", "Not in sample data"),
    ("Failed NPI check", "Sent back", ""),
    ("Duplicate or conflict", "Held and escalated", ""),
]


@app.route("/plan/settings", methods=["GET", "POST"])
def plan_settings():
    st = g.state["settings"]
    if request.method == "POST":
        if g.state["role"] != "plan":
            g.state["blocked_attempts"] = g.state.get("blocked_attempts", 0) + 1
            flash("Only plan approvers can change the policy.", "error")
            return redirect(url_for("plan_settings"))
        st["phone_auto"] = request.form.get("phone_auto") == "on"
        try:
            days = int(request.form.get("window_days", st["window_days"]))
            if not 1 <= days <= 365:
                raise ValueError
            st["window_days"] = days
        except ValueError:
            flash("The back-date window must be a whole number from 1 to 365.", "error")
            return redirect(url_for("plan_settings"))
        flash("Settings saved.", "success")
        return redirect(url_for("plan_settings"))
    return render_template("settings.html", active="plan_queue", area="plan", policy=POLICY)


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", active="dashboard", area="plan", m=logic.metrics(g.state))


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
