"""Real code: mapping, diff, checks, routing, reconciliation and metrics."""
import csv
import datetime as dt
import difflib
import io
import re

from data import DIRECTORY_CODES, SITES, npi_ok

FIELDS = ["name", "npi", "site", "specialty", "phone", "start_date", "status", "effective_date"]
FIELD_LABELS = {"name": "Clinician name", "npi": "NPI", "site": "Site", "specialty": "Specialty",
                "phone": "Phone", "start_date": "Start date", "status": "Status",
                "effective_date": "Effective date"}
# Past mappings the stand-in "model" has seen. "Loc" is deliberately not one of them.
SYNONYMS = {
    "name": ["clinician name", "provider name", "full name"],
    "npi": ["npi", "npi number"],
    "site": ["site", "practice location", "office"],
    "specialty": ["specialty", "speciality", "spec"],
    "phone": ["phone", "phone number", "telephone", "phn"],
    "start_date": ["start date", "hire date"],
    "status": ["status", "provider status"],
    "effective_date": ["effective date", "eff date"],
}
REMOVAL_WORDS = {"left", "terminated", "termed", "inactive", "removed"}
CONFIDENCE_FLOOR = 70


def now():
    return dt.datetime.now().strftime("%d %b, %H:%M")


ABBREVIATIONS = {"dt": "date", "nm": "name"}


def _norm(s):
    words = re.sub(r"[^a-z0-9 ]", "", s.lower()).split()
    return " ".join(ABBREVIATIONS.get(w, w) for w in words)


def map_headers(headers):
    out = []
    for h in headers:
        n = _norm(h)
        best, score = None, 0.0
        for field, words in SYNONYMS.items():
            for w in words + [FIELD_LABELS[field].lower()]:
                r = difflib.SequenceMatcher(None, n, w).ratio()
                if r > score:
                    best, score = field, r
        conf = round(score * 100)
        out.append({"header": h, "field": best, "confidence": conf,
                    "needs_confirm": conf < CONFIDENCE_FLOOR})
    return out


def parse_date(s):
    s = (s or "").strip()
    for f in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def read_csv(text):
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("The file needs a header row and at least one data row.")
    return rows[0], rows[1:]


def _site_id(value, practice="lakeview"):
    v = (value or "").strip().lower()
    for sid, s in SITES.items():
        if s["practice"] == practice and v and (v == s["name"].lower() or v in s["name"].lower()):
            return sid
    return None


def _norm_name(s):
    return re.sub(r"^(dr\.?\s+)", "", (s or "").strip(), flags=re.I).lower()


def canon_specialty(state, s):
    """The directory name for a specialty, using saved mappings, so "Family Med" and "Family Medicine" match."""
    code = DIRECTORY_CODES.get(s) or state["saved_specialty_map"].get(s)
    return next((n for n, c in DIRECTORY_CODES.items() if c == code), s) if code else s


def _days_text(today, eff):
    delta = (today - eff).days
    return f"{delta} days ago" if delta >= 0 else f"in {-delta} days"


def _status(s):
    v = (s or "").strip().lower()
    if v in REMOVAL_WORDS:
        return "Removed"
    return "Active" if v.startswith("act") else (s or "").strip()


def analyse(state, batch):
    """Diff a mapped file against the roster on file, by NPI + practice + site."""
    mapping = {i: m["field"] for i, m in enumerate(batch["mapping"])}
    provs, affs = state["providers"], state["affiliations"]
    window = state["settings"]["window_days"]
    today = dt.date.today()
    on_file = {}
    for a in affs.values():
        if a["practice"] == "lakeview" and a["status"] == "Active":
            on_file[(provs[a["provider_id"]]["npi"], a["site"])] = a

    noun = "email" if batch.get("kind") == "email" else "file"
    seen, results, touched = {}, [], set()
    for idx, raw in enumerate(batch["rows"]):
        row_no = idx + 1
        v = {f: "" for f in FIELDS}
        for i, cell in enumerate(raw):
            if i in mapping and mapping[i]:
                v[mapping[i]] = cell.strip()
        npi = re.sub(r"\D", "", v["npi"])
        site = _site_id(v["site"])
        status = _status(v["status"])
        start = parse_date(v["start_date"])
        eff = parse_date(v["effective_date"])
        key = (npi, site)
        r = {"row_no": row_no, "name": v["name"], "npi": npi, "site": site,
             "site_name": SITES[site]["name"] if site else (v["site"] or "Unknown"),
             "specialty": v["specialty"], "phone": v["phone"], "status": status,
             "start_date": start.isoformat() if start else "", "effective_date": eff.isoformat() if eff else "",
             "changes": [], "flagged": False, "aff_id": None, "why": "", "type": "", "route": "none"}

        digit_ok = npi_ok(npi)
        in_registry = npi in state["registry"]
        dup_of = seen.get(key)
        if dup_of is None:
            seen[key] = row_no
        existing = on_file.get(key) if digit_ok else None

        # Work out the change type first; the window check only applies to changes.
        if existing:
            p = provs[existing["provider_id"]]
            r["aff_id"] = existing["id"]
            if status == "Removed":
                r["type"] = "Removed"
            else:
                for f, old, new in (("name", p["name"], v["name"]), ("specialty", p["specialty"], v["specialty"]),
                                    ("phone", existing["phone"], v["phone"]),
                                    ("start_date", existing["start_date"], r["start_date"])):
                    if f == "name":
                        differs = _norm_name(old) != _norm_name(new)
                    elif f == "specialty":
                        differs = canon_specialty(state, old) != canon_specialty(state, new)
                    else:
                        differs = old != new
                    if differs:
                        r["changes"].append({"field": f, "label": FIELD_LABELS[f], "old": old, "new": new})
                r["type"] = "Changed" if r["changes"] else "Unchanged"
        else:
            r["type"] = "Removed" if status == "Removed" else "Added"
        # A row with a bad NPI can't be matched, so it is not treated as a change.
        is_change = r["type"] != "Unchanged" and digit_ok and in_registry

        missing = [FIELD_LABELS[f] for f in ("name", "npi", "site", "specialty", "status") if not (site if f == "site" else v[f])]
        if is_change and not eff:
            missing.append("Effective date")
        in_window = eff is not None and abs((today - eff).days) <= window
        checks = [
            {"label": "Check digit", "ok": digit_ok},
            {"label": "Found in synthetic registry", "ok": in_registry},
            {"label": "Required fields present", "ok": not missing,
             "detail": ("Missing: " + ", ".join(missing)) if missing else ""},
            {"label": f"Not a duplicate in this {noun}", "ok": dup_of is None},
            {"label": "Effective date inside the plan's window", "ok": in_window if is_change and eff else None,
             "detail": "" if not (is_change and eff) else f"{_days_text(today, eff)}, window {window} days"},
        ]
        r["checks"] = checks

        if dup_of is not None:
            r.update(type="Duplicate", route="held", why=f"Repeats row {dup_of}.")
        elif not digit_ok or not in_registry:
            guess = next((a for (n, s), a in on_file.items()
                          if s == site and _norm_name(provs[a["provider_id"]]["name"]) == _norm_name(v["name"])), None)
            if guess:
                touched.add(guess["id"])
                r["matched_name"] = provs[guess["provider_id"]]["name"]
                r["matched_npi"] = provs[guess["provider_id"]]["npi"]
            if not digit_ok:
                why = "Check digit fails."
                if guess:
                    why += f" Looks like a typo of {r['matched_name']}'s NPI on file."
            else:
                why = "NPI not in the synthetic registry."
            r.update(type="Invalid", route="sent_back", why=why)
        elif r["type"] == "Removed" and not existing:
            r.update(route="sent_back", why="Not on the roster at this site, so there is nothing to remove.")
        elif missing:
            r.update(route="sent_back", why="Missing: " + ", ".join(missing) + ".")
        elif r["type"] == "Unchanged":
            r["why"] = "Matches the record on file."
        elif r["type"] == "Removed":
            r["route"] = "plan_review"
            r["why"] = "Removals need plan approval."
            if not in_window:
                r["flagged"] = True
                r["why"] += (f" Back-dated {(today - eff).days} days (window {window})." if eff < today
                             else f" Dated {(eff - today).days} days ahead (window {window}).")
        elif r["type"] == "Added":
            r.update(route="plan_review", why="New clinicians need plan approval.")
            if not in_window:
                r["flagged"] = True
                r["why"] += f" Outside the {window}-day window."
        else:
            only_phone = all(c["field"] == "phone" for c in r["changes"])
            if only_phone and state["settings"]["phone_auto"] and in_window:
                r.update(route="automatic", why="Phone changes apply automatically.")
            elif only_phone:
                r.update(route="plan_review", why="Plan reviews phone changes." if not state["settings"]["phone_auto"]
                         else f"Outside the {window}-day window.")
                r["flagged"] = not in_window
            else:
                r.update(route="plan_review", why="Specialty, name or start date changes need review.")
        if r["aff_id"]:
            touched.add(r["aff_id"])
        results.append(r)

    missing_from_file = [a for a in on_file.values() if a["id"] not in touched]
    counts = {k: sum(1 for r in results if r["type"] == k)
              for k in ("Unchanged", "Added", "Removed", "Changed", "Invalid", "Duplicate")}
    return {"rows": results, "counts": counts, "missing": missing_from_file}


# Reconciliation ------------------------------------------------------------

def new_change(state, **kw):
    cid = f"MC-{state['next_change']}"
    state["next_change"] += 1
    c = {"id": cid, "events": [], "destinations": [], "decision": None, "reverses": None, "reversed_by": None}
    c.update(kw)
    state["changes"][cid] = c
    return c


def event(change, label):
    change["events"].append({"time": now(), "label": label})


# The pilot has one destination: the plan's provider database, which feeds the directory.
# Claims and prior auth is shown for illustration only and never blocks Applied.
REQUIRED_DESTINATIONS = ["Provider database", "Directory"]


def stamp():
    return dt.datetime.now().isoformat(timespec="seconds")


def required(change):
    return [d for d in change["destinations"] if not d.get("later")]


def dispatch(state, change):
    """Provider database acknowledges at once; the directory waits for sync."""
    change["released_ts"] = stamp()
    change["destinations"] = [
        {"name": "Provider database", "status": "Acknowledged", "time": now(), "ts": stamp(), "attempts": 1,
         "note": "Stored."},
        {"name": "Directory", "status": "Pending", "time": None, "ts": None, "attempts": 0,
         "note": "Waiting for sync."},
        {"name": "Claims and prior auth", "status": "Later", "later": True, "time": None, "ts": None, "attempts": 0,
         "note": "Later, shown for illustration."},
    ]
    event(change, "Sent to provider database and directory")
    event(change, "Provider database acknowledged")
    refresh_status(state, change)


def refresh_status(state, change):
    dests = required(change)
    if not dests:
        return
    acked = [d for d in dests if d["status"] == "Acknowledged"]
    failed = [d for d in dests if d["status"] == "Failed"]
    if len(acked) == len(dests):
        if change["status"] != "Applied":
            change["status"] = "Applied"
            change["applied_at"] = now()
            event(change, "Applied: provider database and directory acknowledged")
            apply_to_roster(state, change)
    elif failed and acked:
        change["status"] = "Partially applied"
    else:
        change["status"] = "Waiting for systems"


def apply_to_roster(state, change):
    affs, provs = state["affiliations"], state["providers"]
    t = change["type"]
    if t == "Added":
        pid = f"P{len(provs) + 1:03d}"
        known = next((p for p in provs.values() if p["npi"] == change["npi"]), None)
        if known:
            pid = known["id"]
        else:
            specialty = next((n for n, code in DIRECTORY_CODES.items() if code == change.get("directory_code")),
                             change["new"]["specialty"])
            provs[pid] = {"id": pid, "npi": change["npi"], "name": change["name"], "specialty": specialty}
        aid = f"A{len(affs) + 1:03d}"
        affs[aid] = {"id": aid, "provider_id": pid, "practice": "lakeview", "site": change["site"],
                     "phone": change["new"]["phone"], "start_date": change["new"]["start_date"],
                     "end_date": None, "status": "Active", "version": 1}
        change["aff_id"] = aid
        change["version"] = "v1 (new record)"
        state["applied_affs"].add(aid)
        return
    a = affs[change["aff_id"]]
    # Every applied change adds a version; nothing is overwritten without one.
    change["version"] = f"v{a['version']} → v{a['version'] + 1}"
    a["version"] += 1
    if t == "Removed":
        a["status"], a["end_date"] = "Terminated", change["new"]["end_date"]
    elif t == "Changed":
        for f, val in change["new"].items():
            if f in ("phone", "start_date"):
                a[f] = val
            else:
                provs[a["provider_id"]][f] = val
        state["applied_affs"].add(a["id"])
    elif t == "Reinstated":
        a["status"], a["end_date"] = "Active", None
        state["applied_affs"].add(a["id"])


def directory_code(state, specialty):
    return DIRECTORY_CODES.get(specialty) or state["saved_specialty_map"].get(specialty)


def directory_code(state, specialty):
    return DIRECTORY_CODES.get(specialty) or state["saved_specialty_map"].get(specialty)


def run_directory_sync(state):
    acked = failed = 0
    for c in state["changes"].values():
        for d in c["destinations"]:
            if d["name"] == "Directory" and d["status"] == "Pending":
                d["attempts"] += 1
                d["time"], d["ts"] = now(), stamp()
                code = directory_code(state, c["specialty"])
                if code:
                    d.update(status="Acknowledged", note=f"Accepted ({code}).")
                    c["directory_code"] = code
                    event(c, "Directory acknowledged")
                    acked += 1
                else:
                    d.update(status="Failed", note="Rejected.")
                    event(c, "Directory rejected the update")
                    failed += 1
                refresh_status(state, c)
    return acked, failed


def fix_and_resend(state, change, code):
    d = next(d for d in change["destinations"] if d["name"] == "Directory")
    if d["status"] != "Failed":
        return False  # idempotent: nothing to resend
    state["saved_specialty_map"][change["specialty"]] = code
    change["directory_code"] = code
    d["attempts"] += 1
    d.update(status="Acknowledged", time=now(), ts=stamp(),
             note=f"Accepted ({code}).")
    event(change, f"Resent to directory, attempt {d['attempts']}. Provider database already acknowledged, not resent.")
    event(change, "Directory acknowledged")
    refresh_status(state, change)
    return True


def reverse(state, change):
    """Create a correcting change. Nothing is deleted."""
    inverse = {"Added": "Removed", "Removed": "Reinstated", "Changed": "Changed"}[change["type"]]
    if change["type"] == "Changed":
        new = {c["field"]: c["old"] for c in change["changed_fields"]}
        fields = [{**c, "old": c["new"], "new": c["old"]} for c in change["changed_fields"]]
    elif change["type"] == "Added":
        new, fields = {"end_date": dt.date.today().isoformat()}, [{"field": "status", "label": "Status", "old": "Active", "new": "Removed"}]
    else:
        new, fields = {}, [{"field": "status", "label": "Status", "old": "Terminated", "new": "Active"}]
    aff = state["affiliations"][change["aff_id"]]
    c = new_change(state, source="Correction", type=inverse, name=change["name"], npi=change["npi"],
                   site=change["site"], site_name=change["site_name"], aff_id=change["aff_id"],
                   specialty=state["providers"][aff["provider_id"]]["specialty"],
                   new=new, changed_fields=fields, checks=[], route="Correction",
                   why=f"Reverses {change['id']}.", flagged=False,
                   status="Waiting for systems", submitted_at=now(), reverses=change["id"])
    change["reversed_by"] = c["id"]
    event(c, f"Created to reverse {change['id']}")
    c["decision"] = {"action": "Approved", "by": "Plan analyst (approver)", "time": now(), "note": "Reversal"}
    dispatch(state, c)
    return c


# Email intake (simulated AI: keyword rules) -----------------------------------

EMAIL_SAMPLE = ("Hi, Dr Mei Lin joined our East site this month and Dr Arjun Patel has left. "
                "Thanks, Lakeview office.")
JOIN_WORDS = r"\b(joined|joins|started|starts|hired|new)\b"
LEAVE_WORDS = r"\b(left|leaves|leaving|resigned|retired|no longer)\b"


def read_email(state, text):
    """Turn a free-text email into roster rows, each with a confidence score."""
    today = dt.date.today()
    provs, affs = state["providers"], state["affiliations"]
    people = {p["name"].lower(): p for p in provs.values()}
    nh = state["new_hire"]
    people.setdefault(nh["name"].lower(), {"name": nh["name"], "npi": nh["npi"], "specialty": "Family Medicine"})
    matches = list(re.finditer(r"\bDr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][A-Za-z-]+)+)", text))
    items = []
    for i, m in enumerate(matches):
        seg = text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        name = m.group(1).strip()
        if re.search(LEAVE_WORDS, seg, re.I):
            action = "Removed"
        elif re.search(JOIN_WORDS, seg, re.I):
            action = "Added"
        else:
            continue
        reasons, conf = [], 95
        spans = [(m.start(), m.end(), "name")]
        person = people.get(name.lower())
        if not person:
            conf = min(conf, 40)
            reasons.append("name not found in the synthetic registry")
        site_m = re.search(r"\b(Main|North|East)\b", seg, re.I)
        site = f"Lakeview {site_m.group(1).title()}" if site_m else ""
        if site_m:
            spans.append((m.end() + site_m.start(), m.end() + site_m.end(), "site"))
        on_file = None
        if person and action == "Removed":
            on_file = next((a for a in affs.values() if a["practice"] == "lakeview" and a["status"] == "Active"
                            and provs[a["provider_id"]]["npi"] == person["npi"]
                            and (not site or SITES[a["site"]]["name"] == site)), None)
            if on_file:
                site = SITES[on_file["site"]]["name"]
        if not site:
            conf = min(conf, 60)
            reasons.append("site not stated")
        date_m = re.search(r"this month|\btoday\b", seg, re.I)
        if date_m:
            spans.append((m.end() + date_m.start(), m.end() + date_m.end(), "date"))
        if re.search(r"this month", seg, re.I):
            eff, conf = today.replace(day=1), min(conf, 85)
            reasons.append('date read from "this month"')
        elif re.search(r"\btoday\b", seg, re.I):
            eff = today
        else:
            eff, conf = today, min(conf, 55)
            reasons.append("no date given, assumed today")
        row = {"name": name, "npi": person["npi"] if person else "", "site": site,
               "specialty": person["specialty"] if person else "", "phone": "",
               "start_date": eff.isoformat() if action == "Added" else "",
               "status": "Active" if action == "Added" else "left", "effective_date": eff.isoformat()}
        if on_file:
            row.update(phone=on_file["phone"], start_date=on_file["start_date"])
        items.append({"action": action, "name": name, "row": row, "confidence": conf,
                      "needs_confirm": conf < CONFIDENCE_FLOOR, "reasons": reasons,
                      "quote": (m.group(0) + seg).strip(" ,."), "spans": spans,
                      "npi_from_registry": bool(person) and action == "Added"})
    return items


def email_segments(text, items):
    """Split the email into plain text and highlighted tags (name, site, date) for display."""
    spans = sorted(sp for it in items for sp in it["spans"])
    out, pos = [], 0
    for start, end, kind in spans:
        if start < pos:
            continue
        if start > pos:
            out.append({"text": text[pos:start], "kind": None})
        out.append({"text": text[start:end], "kind": kind})
        pos = end
    if pos < len(text):
        out.append({"text": text[pos:], "kind": None})
    return out


def email_rows(items):
    return [[it["row"][f] for f in FIELDS] for it in items]


IDENTITY_MAPPING = [{"header": FIELD_LABELS[f], "field": f, "confidence": 100, "needs_confirm": False} for f in FIELDS]


# Metrics -------------------------------------------------------------------

def business_days(start_iso, end_iso):
    start, end = dt.datetime.fromisoformat(start_iso).date(), dt.datetime.fromisoformat(end_iso).date()
    days, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def metrics(state):
    affs = state["affiliations"]
    active = [a for a in affs.values() if a["status"] == "Active"]
    current = [a for a in active if a["id"] in state["confirmed"] or a["id"] in state["applied_affs"]]
    # A submitted change is any change record a practice sent (file or email), duplicates included.
    submitted = [c for c in state["changes"].values() if c["source"] in ("File", "Email")]
    failed_checks = [c for c in submitted if c.get("first_route", c["route"]) == "sent_back"]
    held = [c for c in submitted if c.get("first_route", c["route"]) == "held"]
    rejected = [c for c in submitted if c["status"] == "Rejected by plan"]
    released = [c for c in submitted if c["route"] == "automatic" or (c["decision"] or {}).get("action") == "Approved"]
    applied = [c for c in released if c["status"] == "Applied"]
    automatic = [c for c in submitted if c["route"] == "automatic"]
    dir_ts = [(c, next(d for d in c["destinations"] if d["name"] == "Directory")) for c in applied]
    on_time = [c for c, d in dir_ts if d.get("ts") and business_days(c["released_ts"], d["ts"]) <= 2]
    all_applied = [c for c in state["changes"].values() if c["status"] == "Applied"]
    unauthorised = [c for c in all_applied if (c["decision"] or {}).get("action") not in ("Approved", "Automatic")]
    reversed_applied = [c for c in state["changes"].values() if c.get("reversed_by")]
    wrong_removals = [c for c in reversed_applied if c["type"] == "Removed"]
    access_incidents = [c for c in state["changes"].values()
                        if (c["decision"] or {}).get("action") in ("Approved", "Rejected")
                        and c["decision"].get("role", "plan") != "plan"]
    confirmations = sum(len(a["scope"]) for a in state["attestations"])
    minutes = len(applied) * 15 + confirmations * 3
    accepted = len(submitted) - len(failed_checks) - len(rejected) - len(held)
    waiting_sync = [c for c in released for d in c["destinations"] if d["name"] == "Directory" and d["status"] == "Pending"]

    def pct(n, d):
        return round(100 * n / d) if d else None

    return {
        "north_star": {"value": pct(len(current), len(active)), "n": len(current), "d": len(active)},
        "applied": {"value": pct(len(applied), len(released)), "n": len(applied), "d": len(released)},
        "first_time": {"value": pct(accepted, len(submitted)), "n": accepted, "d": len(submitted),
                       "checks": len(failed_checks), "plan": len(rejected), "held": len(held)},
        "automatic": {"value": pct(len(automatic), len(submitted)), "n": len(automatic), "d": len(submitted)},
        "directory_2d": {"value": pct(len(on_time), len(applied)), "n": len(on_time), "d": len(applied)},
        "guardrails": {"unauthorised": len(unauthorised), "wrong_removals": len(wrong_removals),
                       "errors_after_loading": len(reversed_applied), "access_incidents": len(access_incidents),
                       "blocked": state.get("blocked_attempts", 0), "applied": len(all_applied)},
        "hours": {"value": round(minutes / 60, 1), "changes": len(applied), "confirmations": confirmations},
        "waiting_sync": len(waiting_sync),
        "funnel": [("Submitted", len(submitted)), ("Passed checks", len(submitted) - len(failed_checks) - len(held)),
                   ("Approved or automatic", len(released)), ("Confirmed by every destination", len(applied))],
        "by_practice": {
            p: {"n": sum(1 for a in current if a["practice"] == p), "d": sum(1 for a in active if a["practice"] == p)}
            for p in ("lakeview", "riverbend", "cedar")},
    }
