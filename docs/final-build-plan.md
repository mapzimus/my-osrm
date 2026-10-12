# Final build plan (rebuild #4)

## Why another build
The cloud build from 2026-10-11 scored 37/39. Every remaining defect is in the
data pipeline, so the fix needs a rebuild. Two audits had read the code, and the
mini-engine had tested five small areas. Nothing had tested every ferry on the
full network until the 2026-10-11 sweep, which is how these were found.

## Fixed in this branch
| Bug | Where | Proof |
|---|---|---|
| Dock connector stopped at a stranded "major road" fragment or an exit-only ramp. Tsawwassen, Swartz Bay, Comox and Duke Point became exit-only. | `scripts/ferry_connectors.py`: a major node only counts as the network with at least 3 km of oneway-legal major road behind it (way in) or ahead of it (way out) | Test engine from the build's own data: Hwy 17 to all 5 Tsawwassen berths went from NoRoute to about 5 min. 15 CA and 9 US docks change status. |
| Ferry line ends at a one-way boarding lane, so cars can board but never drive off (Walpole Island to Algonac) | same file: retry the failed direction letting oneway service lanes run backwards (x4 cost); those lanes are written as two-way | `one_way_only` changes to `connected_contraflow` in CA and US |
| Approach stub copies the whole crossing time, charged twice (Bremerton, Jamestown–Scotland, Île d'Entrée). MM:SS typos read as hours (Bequia 60 h). | `scripts/ferry_durations.py`, from the app session; now wired as the "ferry durations" stage after "car ferries only" | 8/8 unit tests; dry run on the full file: 13 stubs, 7 typos |
| 0-byte leftovers from a crashed run trusted as complete | `stage()`, `border_crossings.py`, `ferry_connectors.py` check that size > 0 | two 0-byte boundary files deleted |
| Cloud: `reports.tgz` empty; final network not kept; verification only after download | `cloud_remote.sh` runs the report card and ferry sweep on the VM, packs reports correctly, and keeps `na-ferry.osm.pbf` | `bash -n` |
| `-SkipBuild` checked one engine file; a failing render step left the wrong folder current | `overnight.ps1` checks all 26 files and uses Push/Pop-Location | parses |
| Bremerton case asserted a ferry that correctly loses to the drive | `routes.toml` adds `exclude = "motorway"`, so it checks that the ferry exists | passes on the live engine |
| No regression cases for the new bugs | `routes.toml`: Courtenay to Powell River, Victoria to Vancouver | both FAIL on the live engine, as expected |

New tools: `benchmarks/ferry_sweep.py` routes every kept car ferry dock to dock in
both directions; it runs offline from the pipeline reports.
`benchmarks/road_sweep.py` checks towns in all 34 countries plus every border
crossing in both directions.

## Known and accepted (not bugs)
- **Small river ferries go unused** (Oxford–Bellevue, Québec–Lévis, Millersburg and about 30 more). The flat 30-min boarding charge makes driving around win. This is policy, not a bug. Kept at 30 min unless Max decides otherwise.
- North Ferry and Jamestown–Scotland report `one_way_only`. Each lane has its own ferry line, so routing is unaffected.
- Belize water-taxi docks (car-free islands) are classed as `island`.

## Run order
0. `create` ships the LOCAL scripts/ profiles/ tools/ benchmarks/ folders, not git. Run it from main after the merge, with `git status --short scripts profiles tools benchmarks` showing no modified files. Untracked caches like `cities.json` are harmless.
1. `pwsh -File scripts/cloud_build.ps1 create`: about 45 min build, then verification on the VM.
2. `pwsh -File scripts/cloud_build.ps1 status` until `DONE`. Read `report_card.txt` on the VM. **Expect 41/41.** Ferry sweep: no Tsawwassen, Swartz Bay or Walpole flags.
3. If anything fails, debug on the VM; the intermediates are still there. Otherwise run `fetch`, then `delete`.
4. `pwsh -File scripts/overnight.ps1 -SkipBuild`: renders, report card, commit.
