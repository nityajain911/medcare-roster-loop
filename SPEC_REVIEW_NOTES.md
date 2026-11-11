# Spec review notes: v1 → v2 → v3

Sources: the original spec (`PROTOTYPE_SPEC_v1.md`), the GPT review, Claude's own read, and (for v3) the Gemini review. The first "Gemini" paste was a copy of the GPT review; the real Gemini review came later and reviewed v1.

## Taken from GPT
| Point | What changed in v2 |
|---|---|
| Diff by NPI alone is wrong | Provider and affiliation are separate; diff key is NPI + practice + site. C1 works at two sites to show it is not a duplicate |
| Partial failure not explicit | New "Partially applied" status; fix and resend keeps the same change id and skips the destination that already acknowledged |
| Where did the specialty go? | File now has a `Spec` column. "Family Med" passes MedCare checks but has no directory code, so the failure is a destination mismatch, not missing data |
| NPI wording overclaims | Labels are "Check digit" and "Found in synthetic registry"; About says these are not real NPIs and there is no NPPES check |
| "Confirm no changes" is just a click | Attestation panel with checkbox, who, when and scope; only refreshes in-scope records |
| No "why" behind routes | Every route shows a one-line reason |
| Duplicate and reject path ambiguous | Exact rows specified; script approves both, back-dated approval needs an override note, rejection is tested separately |
| Dashboard and settings P1 but tested | Both P0 in minimal form |
| Destination set per change unclear | All data changes go to both; confirmations go to none |
| Channel claims | About says other channels are future integrations |
| 20-minute time box optimistic | Removed; P1 starts only after P0 checks pass |

## Changed or not taken
| GPT point | Decision | Why |
|---|---|---|
| Drop HiLabs colours | **Kept as is (confirmed)** | GPT assumed HiLabs is a competitor. This prototype is for HiLabs, so its brand is intended |
| CSRF and Secure cookies | Skipped CSRF; kept HttpOnly and SameSite | Synthetic data, nothing to protect. Secure flag breaks local http |
| Affiliation discrepancy (site move) | Moved to P1 | Adds rows and demo time; the two-site clinician already shows the affiliation model |
| Exception detail view | P1; the P0 queue shows old vs new, checks and why inline | Covers most of the value without a new screen |
| Roster health score | Not built | A second score muddies the north star |

## Found in Claude's read (neither review caught these)
1. **Stale dates.** A static `sample_lakeview.csv` with "10 days ago" falls outside the 60-day window after 50 days and breaks the demo. v2 generates `/sample.csv` on request.
2. **The typo row would create a phantom removal.** In v1, C8 appears only with a bad NPI, so a diff by key sees C8 as missing → removed. v2: absence never means removal, and bad-check-digit rows are matched to the roster by name + site.
3. **Render workers.** In-memory state breaks with more than one gunicorn worker, and the free tier wipes memory on sleep. v2 pins one worker and reseeds unknown cookies.
4. **Unchanged rows must match exactly** (specialty text, phone format, start date) or they show up as false changes.
5. **"Hours" changes** were in the routing table, but there is no hours field. Dropped.
6. **North star numbers pinned** (26/43 → 35/43 after confirming) so the tester has a fixed target.
7. **Low-confidence mapping** was never forced to show in v1. v2 tunes `Loc` to fall under 70%.

## v2 → v3: taken from Gemini (built and live)
| Point | What changed |
|---|---|
| Case prompt lists claims, prior auth and care coordination as downstream systems | Third destination "Claims and prior auth"; back-dated removal flag mentions claims end dates; About says MedCare updates the record those systems read, not claims processing |
| Judges may open the link with no instructions | Guided demo bar: steps 1 to 6 and a Next button worked out from state |
| An analyst shouldn't guess a specialty | Fix dropdown pre-selects the code the synthetic registry holds for the NPI; analyst still confirms |
| Compliance and audit trails | "Audit trail" with record versions; Reverse button adds a correcting version, nothing deleted |

## Gemini points not taken
| Point | Why |
|---|---|
| Multi-worker state bug | Already fixed in v2: one gunicorn worker, confirmed in Render logs. JSON files in `/tmp` add locking and are wiped on restart anyway |
| "Specialty missing" contradiction | Already fixed in v2 (file has a specialty; directory lacks a code for it) |
| Fix bad NPI inline before submit | The built flow already does Gemini's default: valid rows go forward, failed rows go back with a reason. A second edit path adds risk |
| Scrub starter template text | No starter was used; every page was written for this app |
