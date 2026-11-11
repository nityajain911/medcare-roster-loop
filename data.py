"""Synthetic, seeded demo data. Every name, NPI, practice, site and plan is fictional."""
import csv
import datetime as dt
import io
import random

PLAN = "Northwind Health Plan"

FIRST = ["Elena", "Marcus", "Priya", "Tomas", "Hannah", "Kwame", "Sofia", "Daniel", "Aisha", "Grant",
         "Leah", "Rafael", "Nina", "Oscar", "Ivy", "Samir", "Clara", "Jonah", "Freya", "Malik",
         "Rosa", "Theo", "Yara", "Felix", "Imani", "Victor", "Lena", "Omar", "Greta", "Silas",
         "Noor", "Emil", "Tessa", "Kofi", "Maren", "Dario", "Beatrix", "Ansel", "Lucia", "Ronan",
         "Zara", "Hugo", "Wren", "Idris"]
LAST = ["Halvorsen", "Okafor", "Brightwater", "Quintero", "Lindqvist", "Ashby", "Marchetti", "Delacroix",
        "Novak", "Pemberton", "Rahimi", "Castellan", "Thornbury", "Vasquez-Holt", "Eberly", "Mwangi",
        "Kestrel", "Fairbanks", "Oduya", "Sorensen", "Whitlock", "Albrecht", "Montague", "Ferrante",
        "Nakashima", "Greaves", "Abernathy", "Solberg", "Carrow", "Iyengar", "Duchamp", "Wexley",
        "Hollins", "Ravensworth", "Tanaka-Bell", "Ostrowski", "Lark", "Beaumont", "Kildare", "Mercer",
        "Ashgrove", "Penhallow", "Strand", "Vale"]

SPECIALTIES = ["Family Medicine", "Internal Medicine", "Pediatrics", "Nurse Practitioner", "Physician Assistant"]

# Illustrative subset of NUCC taxonomy codes. The simulated directory only accepts these names.
DIRECTORY_CODES = {
    "Family Medicine": "207Q00000X",
    "Internal Medicine": "207R00000X",
    "Pediatrics": "208000000X",
    "Nurse Practitioner": "363LF0000X",
    "Physician Assistant": "363A00000X",
}

PRACTICES = {
    "lakeview": {"name": "Lakeview Family Practice", "kind": "Independent"},
    "riverbend": {"name": "Riverbend Medical Group", "kind": "Delegated"},
    "cedar": {"name": "Cedar Pediatrics", "kind": "Independent"},
}

SITES = {
    "LV-MAIN": {"name": "Lakeview Main", "practice": "lakeview"},
    "LV-NORTH": {"name": "Lakeview North", "practice": "lakeview"},
    "LV-EAST": {"name": "Lakeview East", "practice": "lakeview"},
    "RB-CENTRAL": {"name": "Riverbend Central", "practice": "riverbend"},
    "CP-CLINIC": {"name": "Cedar Pediatrics Clinic", "practice": "cedar"},
}

# Lakeview clinicians C1..C8: (specialty, sites). C1 works at two sites. C7 is Dr Arjun Patel.
LAKEVIEW_PLAN = [
    ("Family Medicine", ["LV-MAIN", "LV-NORTH"]),
    ("Internal Medicine", ["LV-MAIN"]),
    ("Pediatrics", ["LV-MAIN"]),
    ("Family Medicine", ["LV-NORTH"]),
    ("Nurse Practitioner", ["LV-NORTH"]),
    ("Physician Assistant", ["LV-EAST"]),
    ("Internal Medicine", ["LV-EAST"]),
    ("Family Medicine", ["LV-MAIN"]),
]

FILE_DATE = "%m/%d/%Y"


def npi_ok(npi: str) -> bool:
    if len(npi) != 10 or not npi.isdigit(): return False
    digits = [int(d) for d in "80840" + npi]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9: d -= 9
        total += d
    return total % 10 == 0


def make_npi(rng):
    body = "9" + "".join(str(rng.randint(0, 9)) for _ in range(8))
    for c in "0123456789":
        if npi_ok(body + c): return body + c


def generate():
    """Build one visitor's starting state. Same seed every time; dates are relative to today."""
    rng = random.Random(42)
    today = dt.date.today()
    used_npis, used_names, used_phones = set(), set(), set()

    def new_npi():
        while True:
            n = make_npi(rng)
            if n not in used_npis:
                used_npis.add(n)
                return n

    def new_name():
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if name not in used_names:
                used_names.add(name)
                return name

    def new_phone(area="312"):
        while True:
            p = f"({area}) 555-01{rng.randint(0, 99):02d}"
            if p not in used_phones:
                used_phones.add(p)
                return p

    def past(lo, hi):
        return (today - dt.timedelta(days=rng.randint(lo, hi))).isoformat()

    providers, affiliations, lakeview_ids = {}, {}, []
    aff_n = 0

    def add(name, specialty, practice, sites, area):
        nonlocal aff_n
        pid = f"P{len(providers) + 1:03d}"
        providers[pid] = {"id": pid, "npi": new_npi(), "name": name, "specialty": specialty}
        for site in sites:
            aff_n += 1
            aid = f"A{aff_n:03d}"
            affiliations[aid] = {"id": aid, "provider_id": pid, "practice": practice, "site": site,
                                 "phone": new_phone(area), "start_date": past(400, 3000),
                                 "end_date": None, "status": "Active", "version": 1}
        return pid

    for i, (specialty, sites) in enumerate(LAKEVIEW_PLAN):
        name = "Arjun Patel" if i == 6 else new_name()
        lakeview_ids.append(add(name, specialty, "lakeview", sites, "312"))
    for _ in range(30):
        add(new_name(), rng.choice(SPECIALTIES[:2] + SPECIALTIES[3:]), "riverbend", ["RB-CENTRAL"], "708")
    for _ in range(4):
        add(new_name(), "Pediatrics", "cedar", ["CP-CLINIC"], "630")

    # North star starts near 60%: Riverbend 22 of 30, Cedar 4 of 4, Lakeview 0 of 9 confirmed.
    confirmed = {}
    rb = [a for a in affiliations.values() if a["practice"] == "riverbend"]
    for a in rng.sample(rb, 22) + [a for a in affiliations.values() if a["practice"] == "cedar"]:
        confirmed[a["id"]] = {"time": "Earlier this cycle", "by": "Practice attestation", "source": "Confirmation"}

    new_hire = {"name": "Mei Lin", "npi": new_npi(), "site": "LV-EAST", "specialty": "Family Med",
                "phone": new_phone("312"), "start_date": (today - dt.timedelta(days=10)).isoformat()}
    phone_change = new_phone("312")

    return {
        "settings": {"phone_auto": True, "window_days": 60},
        "role": "practice",
        "blocked_attempts": 0,
        "providers": providers,
        "affiliations": affiliations,
        "lakeview_providers": lakeview_ids,
        "registry": set(used_npis),
        # The synthetic registry also knows each clinician's directory specialty code.
        "registry_taxonomy": {**{p["npi"]: DIRECTORY_CODES[p["specialty"]] for p in providers.values()},
                              new_hire["npi"]: DIRECTORY_CODES["Family Medicine"]},
        "new_hire": new_hire,
        "phone_change": phone_change,
        "confirmed": confirmed,
        "applied_affs": set(),
        "attestations": [],
        "batches": {},
        "changes": {},
        "next_change": 1039,
        "saved_specialty_map": {},
        "flash": [],
    }


def fmt_file_date(iso):
    return dt.date.fromisoformat(iso).strftime(FILE_DATE)


def sample_csv(state):
    """The messy Lakeview file: 11 rows, odd headers, dates relative to today."""
    today = dt.date.today()
    provs, affs = state["providers"], state["affiliations"]
    by_provider = {}
    for a in affs.values():
        by_provider.setdefault(a["provider_id"], []).append(a)
    c = state["lakeview_providers"]
    short = {"LV-MAIN": "Main", "LV-NORTH": "North", "LV-EAST": "East"}

    def row(pid, aff, phone=None, status="Active", eff=""):
        p = provs[pid]
        return [p["name"], p["npi"], short[aff["site"]], p["specialty"], phone or aff["phone"],
                fmt_file_date(aff["start_date"]), status, eff]

    def ago(days):
        return (today - dt.timedelta(days=days)).strftime(FILE_DATE)

    c1a, c1b = by_provider[c[0]]
    rows = [
        row(c[0], c1a),
        row(c[0], c1b, status="active"),
        row(c[1], by_provider[c[1]][0]),
        row(c[2], by_provider[c[2]][0], status="ACTIVE"),
        row(c[3], by_provider[c[3]][0]),
        row(c[4], by_provider[c[4]][0], status="active"),
        row(c[5], by_provider[c[5]][0], phone=state["phone_change"], eff=ago(3)),
        row(c[6], by_provider[c[6]][0], status="left", eff=ago(200)),
    ]
    c8 = row(c[7], by_provider[c[7]][0])
    npi = c8[1]
    c8[1] = npi[:4] + str((int(npi[4]) + 3) % 10) + npi[5:]  # one-digit typo, always fails the check digit
    rows.append(c8)
    nh = state["new_hire"]
    rows.append([nh["name"], nh["npi"], "East", nh["specialty"], nh["phone"],
                 fmt_file_date(nh["start_date"]), "Active", ago(10)])
    rows.append(list(rows[3]))  # exact repeat of row 4

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Provider Nm", "NPI #", "Loc", "Spec", "Ph", "Start Dt", "Stat", "Eff Dt"])
    w.writerows(rows)
    return out.getvalue()
