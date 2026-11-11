# Prototype spec: MedCare Roster Loop (v4, as built, matches the submitted deck)

Live: https://medcare-roster-loop.onrender.com · Code: https://github.com/nityajain911/medcare-roster-loop

**Paste this into Claude Code, opened inside `proto_starter/`:**
> Read CLAUDE.md and PROTOTYPE_SPEC.md. Build exactly this spec in this Flask starter, P0 first. Synthetic data only. Then run the `prototype-tester` agent, fix what it finds, and tell me when it is ready to push to GitHub and Render.


## v4: deck alignment (overrides anything below that conflicts)
The submitted deck is final, so the app matches it.
1. **One pilot destination:** the plan's provider database, which feeds the directory. Applied needs both. "Claims and prior auth" is a greyed tile labelled "Later, shown for illustration" and never blocks Applied. Counts read "x of 2". Removals list "provider database, directory (claims and prior auth later)".
2. **Removals** show "Removed from directory" (not "Live in directory") on Practice status and Reconciliation.
3. **Approval note** is empty with the placeholder "Optional note for the audit trail". A back-dated change needs a reason.
4. **Counts:** a submitted change is any change record from a file or email, duplicates included. The banner reads "Submitted 5 changes (1 duplicate held)". Accepted first time = not sent back, rejected or held / submitted (3 / 5), showing "duplicates held: 1".
5. **Practice correct and resend** on Practice status: a bad NPI can be corrected, checked again and routed as normal (the right NPI counts as confirmed); a duplicate has "Remove duplicate row".
6. **Back-dated removals:** the plan chooses "Apply with original date" or "Apply from today instead". The choice and reason go in the audit trail; the effective date changes if today is chosen.
7. **Email intake:** "Paste an email" on the Practice page, with "Use sample email". Simulated AI (keyword rules) turns it into the same two changes with a confidence %; under 70% asks the user to confirm. SFTP stays a future channel.
8. **Plan settings** shows the full change-type policy from deck slide 8 as a read-only table, plus the phone toggle and window.
9. **Dashboard** (deck slide 13): "Share of eligible records confirmed and current", "Approved changes confirmed by every destination", "Directory updated within 2 business days" (commercial rule, directory only), and a guardrail strip (unauthorised changes, wrong removals, errors after loading, access incidents), all computed with formulas shown.
10. **Roles** (deck slide 12): the header shows Practice admin or Plan analyst (approver) with a switch. A practice admin sees the plan queue, reconciliation and settings read-only ("Only plan approvers can decide changes"); blocked attempts are counted. The guided Next button switches role as needed.
11. Practice page notes "Thin portal inside the plan's provider portal (single sign-on)". Home adds "Synthetic data. AI mapping and destination systems are simulated."

---

## 1. What this prototype must prove (one sentence)
A roster change from a small practice can be read from a messy file, checked by fixed rules, routed by change type, approved by the plan where needed, and only marked "Applied" once every destination system confirms it, with the practice told when it is live.

This is the deck's thesis: **plans don't lack a way to collect updates; they can't confirm a change reached every system.** The prototype shows the loop, not a fancy portal.

It answers three questions, in this order:
1. **Can MedCare understand the change?** Messy file, column mapping, affiliation-level diff.
2. **Can MedCare govern the change?** Fixed checks, routing policy, plan approval with reasons.
3. **Can MedCare prove the change happened?** Destination acknowledgements, partial failure, fix and resend, practice told.

## 2. The flow a judge clicks (target 3 minutes)
1. **Practice view** (Lakeview office manager) → sees the 9 affiliations the plan has on file → clicks **Use sample file**.
2. **What changed** → simulated AI maps the odd column names (one low-confidence column asks for confirmation); every row shows old vs new, check badges, its route and one plain line saying why.
3. **Submit** → **Plan queue** (Northwind analyst) → approves Dr Mei Lin (new clinician) → approves Dr Arjun Patel's back-dated removal with a required override note.
4. **Reconciliation** → approved and automatic changes show Provider database: Acknowledged, Claims and prior auth: Acknowledged, Directory: Pending → **Run directory sync** → two changes acknowledge; Mei Lin's change shows **Partially applied** (2 of 3 systems): the directory rejected her specialty because it has no directory code → the registry's code is pre-selected, analyst checks it and clicks **Fix and resend** → Applied.
5. **Practice status** → Lakeview sees each change: Submitted → Checked → Approved (or Automatic) → Applied, with "Live in directory" only after the directory acknowledges. The bad NPI row shows why it was sent back.
6. **Dashboard** → north star and driver metrics, computed from this demo's state.

A header line on every page says who you are viewing as ("Viewing as: Lakeview Family Practice, office manager" or "Viewing as: Northwind Health Plan, network analyst").

**Guided demo bar** (below the header, every page): steps 1 to 6 with done / current / to-do states, and a **Next: ... →** button worked out from the visitor's state (e.g. "Next: Approve the changes in the plan queue"). On the page where the next action happens it shows the instruction instead of a button. After the dashboard it says "Demo complete. Reset demo to run it again." A judge with no instructions can follow it end to end.

## 3. Pages and routes
Add each to `NAV` in `app.py`: Practice, Plan queue, Reconciliation, Dashboard, How it works.

| Route | Page | P |
|---|---|---|
| `/` | Home: one-paragraph thesis, **Start the demo** button, the 6 steps above as a numbered strip | P0 |
| `/practice` | Lakeview roster on file (8 clinicians, 9 affiliations, 3 sites). Buttons: **Confirm no changes**, **Use sample file**, **Upload a CSV**, plus a **Download sample file** link | P0 |
| `/practice/confirm` | Attestation panel (see 5.7). POST records it | P0 |
| `/sample.csv` | The messy sample file, generated on request with dates relative to today | P0 |
| `/practice/review/<batch_id>` | Column mapping card + "What changed" table + **Submit changes** | P0 |
| `/plan/queue` | Changes needing review, each with evidence and the reason it is here. **Approve change** / **Reject change** (reason required) | P0 |
| `/reconcile` | One reconciliation record per change, destination acks, **Run directory sync**, fix and resend | P0 |
| `/practice/status` | Status tracker per change for Lakeview | P0 |
| `/plan/settings` | Toggle "Phone changes apply automatically"; set the back-date window (days) | P0 (needed for acceptance check 5) |
| `/dashboard` | North star + 4 driver tiles, each with its formula | P0 (minimal) |
| `/about` | How it works: real code, simulated, assumptions, not in scope | P0 |
| `/reset` (POST) | Reset this visitor's demo to the seeded start | P0 |

Internal POST routes for approve, reject, sync, resend and confirm can be named as you like.

**State:**
- Server-side dict keyed by a random id (`secrets.token_urlsafe(16)`) in a cookie set `HttpOnly`, `SameSite=Lax`. Never read the id from a form field.
- Seed each visitor from the same generator, so two judges never collide.
- If the cookie id is unknown (server restarted, Render woke from sleep), seed a fresh state silently instead of erroring.
- Keep at most 500 visitor states; drop the oldest beyond that.
- A **Reset demo** button in the header POSTs to `/reset`.
- No database. The About page says state is in memory and is lost on restart.

## 4. Synthetic data (seeded, `random.Random(42)`)
Generate in `data.py`. Everything fictional.

### 4.1 Data model: provider and affiliation are separate
- **Provider:** provider_id, NPI, name, specialty.
- **Affiliation:** affiliation_id, provider_id, practice_id, site_id, phone, start_date, end_date, status.
- One provider can have several affiliations. **The diff key is NPI + practice + site**, not NPI alone. (Demo key; the About page notes production would use stable internal ids.)

### 4.2 Plan, practices, sites
- **Plan:** Northwind Health Plan (fictional).
- **Lakeview Family Practice:** independent, 3 sites (Lakeview Main, Lakeview North, Lakeview East), 8 clinicians, **9 affiliations**: clinician C1 works at both Main and North. This is the demo practice.
- **Riverbend Medical Group:** delegated, 30 affiliations. Dashboard only.
- **Cedar Pediatrics:** independent, 4 affiliations. Dashboard only.
- Seed confirmation status so the north star starts near 60%: Riverbend 22 of 30 confirmed this cycle, Cedar 4 of 4, Lakeview 0 of 9 → 26 / 43 = 60%.

### 4.3 Clinicians and identifiers
- Invented first and last names from small seeded lists, plus the two named characters: **Dr Mei Lin** (new hire) and **Dr Arjun Patel** (C7, leaving).
- **NPIs:** 10 digits, **first digit 9**, valid check digit. Real NPIs start with 1 or 2, so none can match a real provider. Check digit (CMS rule, Luhn with prefix 80840):
```python
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
```
- **Synthetic registry:** the set of all seeded NPIs plus Mei Lin's. Label it **"Synthetic registry (test list, not NPPES)"**.
- **Specialties on file** use the directory's names exactly: Family Medicine, Internal Medicine, Pediatrics, Nurse Practitioner, Physician Assistant.
- **Directory specialty codes** (labelled "illustrative subset of NUCC taxonomy codes"): Family Medicine 207Q00000X, Internal Medicine 207R00000X, Pediatrics 208000000X, Nurse Practitioner 363LF0000X, Physician Assistant 363A00000X. The directory accepts a specialty only if its name is in this table.

### 4.4 Sample messy file (`/sample.csv`)
Generated on request so the relative dates never go stale. Headers: `Provider Nm, NPI #, Loc, Spec, Ph, Start Dt, Stat, Eff Dt`. Exactly 11 rows, in this order:

| Row | Who | What is in the row | Expected result |
|---|---|---|---|
| 1 | C1 at Lakeview Main | Same as on file | Unchanged |
| 2 | C1 at Lakeview North | Same as on file | Unchanged (same NPI, different site: **not** a duplicate) |
| 3 | C2 | Same as on file | Unchanged |
| 4 | C3 | Same as on file | Unchanged |
| 5 | C4 | Same as on file | Unchanged |
| 6 | C5 | Same as on file | Unchanged |
| 7 | C6 | New phone, Eff Dt 3 days ago | Changed (phone) → Automatic |
| 8 | Dr Arjun Patel (C7) | Stat "left", Eff Dt 200 days ago | Removed → Plan review, flagged back-dated (claims and prior auth end dates would move back) |
| 9 | C8 | NPI with one digit changed (fails check digit) | Sent back to practice |
| 10 | Dr Mei Lin, Lakeview East | Valid NPI in registry, Spec "Family Med", Eff Dt 10 days ago | Added → Plan review |
| 11 | C3 again | Exact repeat of row 4 | Held: duplicate of row 4, not processed |

Rules this file depends on:
- Unchanged rows copy the on-file specialty, phone format and start date exactly, so they diff clean. Eff Dt is blank on unchanged rows; the date-window check only runs on rows with a change.
- **Absence never means removal.** An affiliation on file but missing from the file is not removed; only an explicit status ("left", "terminated", "inactive") with an Eff Dt is a removal.
- A row that fails the check digit is matched to the roster by name + site. Row 9 shows: "NPI fails the check digit. Looks like a typo of the NPI on file for <C8 name>. Please correct and resend." C8's record on file stays as is and is not counted as removed.
- Mei Lin's "Family Med" is present, so it passes MedCare's required-field check. It is not in the directory's code table, which sets up the failure in 5.5.
- The mapper's synonyms are tuned so **Loc** comes out under 70% and shows the confirm dropdown (pre-filled with "site"); every other header maps above 70%.
- **No patients or members anywhere.** The removal shows "Affected systems: provider database, claims and prior auth, directory", not member counts.

## 5. Logic (real code, keep it simple)

### 5.1 Column mapping (simulated AI)
Match each header to the canonical fields with `difflib.SequenceMatcher` plus a small synonym list. Show confidence %. Under 70% shows a dropdown for the user to confirm. Card label: **"Simulated AI mapping (fuzzy matching stand-in). In production, a model trained on past mappings; low-confidence fields always go to a person."**

### 5.2 Diff (by NPI + practice + site)
Types: Added, Removed, Changed (list the fields, old → new), Unchanged, Duplicate (same key twice in one file).

### 5.3 Checks per row (badges: green pass, red fail)
Use these exact labels:
- **Check digit** (Luhn rule on the NPI-shaped id)
- **Found in synthetic registry**
- **Required fields present** (name, NPI, site, specialty, status; Eff Dt for any change)
- **Not a duplicate in this file**
- **Effective date inside the plan's window** (changes only; default 60 days)

### 5.4 Routing policy (same as deck slide 8)
Every routed row shows a one-line **"Why"** in plain words.

| Change type | Route | "Why" line |
|---|---|---|
| No change confirmation | Recorded with who, when and scope | "Practice confirmed these affiliations are accurate." |
| Phone change | Automatic if the plan allows it (default on), else plan review | "Plan allows phone changes to apply automatically." / "Plan requires review of phone changes." |
| New clinician | Plan review | "New clinicians always need plan approval." |
| Removal | Plan review; flagged if back-dated beyond the window | "Removals need plan approval. Back-dated 200 days, beyond the 60-day window. Review before claims and prior auth end dates move back." |
| Any failed check digit or registry check | Sent back to practice | The plain reason from 4.4 |
| Duplicate in file | Held, not processed | "Same clinician and site appear twice. Row 11 repeats row 4." |

**Plan decisions:**
- **Approve change.** Approving a flagged change (back-dated) requires an override note. The demo pre-fills "Practice confirmed last day of work." to keep the flow fast; clearing it blocks approval.
- **Reject change** requires a reason. The practice sees "Sent back by plan: <reason>".
- The scripted demo approves both; rejection must still work (acceptance check 9).

### 5.5 Destinations, acknowledgement, partial failure
- Every approved or automatic data change goes to **three** destinations: **Provider database**, **Claims and prior auth** and **Directory**. Confirmations go to no destination; they update verification status only.
- **Provider database** acknowledges at once (it stores specialty as free text).
- **Claims and prior auth** acknowledges at once, recording the start or end date that claims and prior auth checks will use. MedCare updates the provider record these systems read; it does not process claims or make prior auth decisions.
- **Directory** is Pending until **Run directory sync**. It needs each specialty mapped to a directory code (4.3).
- On sync: C6's phone change and Arjun Patel's removal acknowledge. Mei Lin's is rejected:
  > **Directory rejected the update.** Provider database and claims and prior auth acknowledged it.
  > **Reason:** Specialty "Family Med" has no directory specialty code.
  > **Fix:** The synthetic registry lists this clinician as 207Q00000X. Check it and resend.
- Status rules: **Waiting for systems** (grey) while a destination is pending; **Partially applied** (amber) when one destination has rejected and others have acknowledged; **Applied** when all have acknowledged.
- **Fix and resend:** the dropdown comes pre-selected with the code the synthetic registry holds for this NPI ("Family Medicine (207Q00000X), suggested from synthetic registry"). The analyst checks it and clicks **Fix and resend**. This resends the **same change id** (attempt 2) to the directory only. The other destinations are not sent it again, because they already acknowledged. Resending never creates a second change. The directory acknowledges and the change becomes **Applied**. Show a small note: "Saved mapping: Family Med → 207Q00000X. Future files use it automatically."
- A change is **Applied** only when every destination has acknowledged.
- Label the page: **"Destination systems are simulated."**

### 5.6 Audit trail and reverse
Each change on `/reconcile` has an expandable **Audit trail**: change id (e.g. MC-1042), **record version** (e.g. "v1 → v2", or "v1 (new record)" for a new clinician), source (file and row), old → new, checks, route and why, approver, note and time, each destination with status, time and attempts, final status, and a timestamped event log.

- Every affiliation carries a version number. Each applied change adds a version; nothing is overwritten without one.
- **Reverse change** (on applied changes): creates a correcting change (source "Correction", not a delete) that all three destinations must acknowledge again. It adds the next version (e.g. "v2 → v3"). The original is marked "Reversed by MC-xxxx" and stays in the trail.

### 5.7 Attestation ("Confirm no changes")
- **Confirm no changes** opens `/practice/confirm`: Lakeview Family Practice, 3 sites, 9 affiliations listed, pending discrepancies count, and a checkbox "I confirm the affiliations shown are accurate as of today." **Record confirmation** stays disabled until the box is ticked.
- Store: who (Lakeview office manager), when, scope (the 9 Lakeview affiliation ids).
- It refreshes only the affiliations in scope. It never marks Riverbend or Cedar records as confirmed.
- Unchanged rows in a submitted full-roster file also count as confirmed for this cycle.

### 5.8 Status tracker (practice side)
Submitted → Checked → Approved (or Automatic) → Applied, with timestamps. While partially applied, show "2 of 3 systems updated". "Live in directory" shows only after the directory acknowledges. Sent back and Held rows show their plain reason.

## 6. Dashboard (P0 minimal), every number computed from state
- **North star:** share of Northwind affiliations confirmed and current = affiliations confirmed this cycle or changed-and-applied / all active affiliations. Starts near 60% (4.2); rises when Lakeview confirms or when its changes apply. A new clinician joins the denominator once applied; a removal leaves it once applied.
- **Changes confirmed by every destination:** applied / (approved + automatic).
- **Accepted first time:** changes not sent back / changes submitted. Split: failed MedCare checks vs rejected by plan.
- **Automatic share:** automatic / all changes.
- **Hours released (illustrative):** changes × 15 min + confirmations × 3 min, labelled **"Assumption from the deck's base case; replace with the pilot plan's time study."**

Each tile shows its formula in small text. Footer: "Synthetic data shows the checks work. It is not evidence of real error rates."

## 7. About page (P0): real vs simulated vs assumption
- **Real code:** check-digit rule, duplicate check, required fields, effective-date window, routing policy, affiliation-level diff, reconciliation logic, partial-apply and resend logic, metrics.
- **Simulated:** AI column mapping (fuzzy matching stand-in), the provider registry and its specialty codes (a synthetic test list, not NPPES), the three destination systems and their acknowledgements, the directory's specialty code table.
- **Identifiers:** "The prototype uses fictional, NPI-shaped identifiers with a valid check digit. They start with 9, so they are not real NPIs, and the prototype does not perform live NPPES verification."
- **Assumptions:** minutes saved per change and per confirmation; the 60-day back-date window (a plan setting).
- **Channels:** "The prototype shows one processing loop through a portal and a roster file. Other channels (CAQH, Availity, SFTP, email) would feed the same loop and are future integrations."
- **State:** in memory, per visitor, lost on restart.
- **Not in scope:** credentialing, contracting and licensing decisions. MedCare updates the provider record that claims and prior auth systems read; it does not process claims or make prior auth decisions. Care coordination platforms would be another destination later. No patient or member data.
- **Data:** all names, NPIs, practices, sites and the plan are fictional.

## 8. Look and copy
- Use `brand.json` and `base.html` as they are. Set `app_name` to "MedCare Roster Loop" and `tagline` to "Every roster change, checked and confirmed live".
- Clean cards, tables with coloured status chips: green Applied, blue Automatic, purple Plan review, amber Partially applied, coral Sent back or Failed, grey Pending or Held.
- Plain words, short labels, no em dashes. Buttons say what they do ("Approve change", "Run directory sync", "Fix and resend").
- Every simulated element carries a small grey "Simulated" tag.

## 9. Acceptance checks (prototype-tester must pass all)
1. Fresh incognito visit → home loads → flow steps 1 to 6 work with no errors.
2. The sample file produces exactly: 6 unchanged (C1 at two sites not flagged), 1 added, 1 removed (back-dated flag), 1 phone change (automatic), 1 bad NPI (sent back, matched to C8, C8 not removed), 1 duplicate (held). **Loc** shows the confirm dropdown.
3. A change never shows "Applied" before all three destinations acknowledge. Mei Lin's change shows "Partially applied" (2 of 3) between the directory rejection and the resend.
4. The directory failure appears exactly once. The fix dropdown is pre-selected with the registry's code. Fix and resend clears it, keeps the same change id, and does not resend to the other destinations.
5. Turning "Phone changes apply automatically" off in settings sends the phone change to plan review on the next upload.
6. Confirm no changes is blocked until the checkbox is ticked, then raises the north star from about 60% (26/43) to about 81% (35/43). Riverbend and Cedar counts do not change.
7. Reset returns to the seeded start. Two browsers don't share state. An unknown cookie id seeds a fresh state instead of erroring.
8. Every number on screen is computed or labelled. No real people's names. No NPI starting with 1 or 2.
9. Rejecting a change requires a reason, and the practice status shows it. Approving the back-dated removal requires a note.
10. No page errors on any route; no em dashes in UI copy.
11. The guided bar's Next button leads a judge from step 1 to 6 with no other instructions.
12. Audit trail shows record versions. Reverse creates a correcting change, needs all three destinations again, and adds a version; the original stays.

## 10. Build order
1. `data.py` (providers, affiliations, sites, registry, code table) + per-visitor state + `/practice` + `/sample.csv` (P0)
2. Mapping, affiliation diff, checks, routing with "Why" lines, `/practice/review` (P0)
3. `/plan/queue` with approve, reject, override note (P0)
4. `/reconcile`: destinations, directory sync, partial apply, fix and resend (P0)
5. `/practice/status`, `/practice/confirm`, `/plan/settings`, minimal `/dashboard`, About, Reset (P0)
6. P1, only after every P0 acceptance check passes

**If time runs short:** cut P1 first, then trim dashboard tiles to the north star only. Never cut reconciliation or partial apply; they are the thesis. Five screens that work beat nine that half work.

## 11. P1 and later
- **P1 (if time allows):** expandable change detail panel in the plan queue; site-move detection (same clinician leaves one site and joins another in one file, shown as one "Moved site" change for plan review).
- **Considered and not built:** fixing a bad NPI inline before submitting. Valid rows already go forward and failed rows go back to the practice with a plain reason, which is the loop the deck describes. File-based session state: not needed with one gunicorn worker, and Render wipes `/tmp` on restart anyway.
- **Later, do not build:** CAQH, Availity, SFTP and email ingestion; multiple plans; cross-plan sharing; downstream impact analysis; anomaly detection; roster health score.

## 12. Deploy
- New public GitHub repo `medcare-roster-loop` (account nityajain911).
- Render web service using `render.yaml`; rename the service to `medcare-roster-loop`. **Run gunicorn with one worker** (`--workers 1 --threads 4`), because state lives in process memory and a second worker would not see it.
- Render's free tier sleeps when idle and wipes memory. Open the live URL a minute before any demo; an unknown cookie reseeds cleanly (check 7).
- Test the live URL in incognito, then send me the URL and 3 screenshots for the deck: What changed, Plan queue, Reconciliation (showing Partially applied).
