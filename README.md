# periscope-ramp-to-sheets

A **new, independent** pipeline. It does not read, write, or redeploy anything
in the old `daralan2412/periscope-to-sheets` repo or its Apps Script project —
that pipeline keeps feeding the old MISSIONS/WC_PAX tab exactly as before.

- **Source**: Periscope/Sisense shared report **REP-1901**
  `https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35`
  — FedEx Ramp Team ops/task data (35 columns: mission_sas_id, date, station,
  tail_number, job_name, mission_name, flight numbers, times, agent_name,
  mission_notes, task_1..task_15). Confirmed live on 2026-08-30 with **no
  password gate**.
- **Target**: Google Sheet **"2026_RAW_MISSIONS"**
  (`1gMxa0iisay-S6L3QvlfjFv0QwJTGt6xUptp6j9GegBU`), tab **"DATA"**
  (gid=1584481054). Its header row already matches this pipeline's column
  order. A separate "SOURCE" tab in that sheet already notes the Periscope
  link — this pipeline is what actually keeps "DATA" filled in.

## How it pulls data (v4, 2026-08-31)

Every run:

1. Opens REP-1901 and sets the Date Range filter to a rolling
   **D0-to-D-7 window** — today back through 7 days ago, computed fresh
   every run in Asia/Taipei time — via Periscope's **"Custom Range"** filter
   with explicit Start/End dates, not a single day and not the "Current
   Week" preset. (Current Week is a calendar-week bucket that resets to
   empty every Sunday even though there's still recent data from the prior
   week worth re-syncing — confirmed live on 2026-08-30 — so the filter was
   switched to this explicit trailing window instead.)
2. Uses the "Data" widget's own **"Download Data"** CSV export (not DOM
   scraping — the grid is virtualized, so a DOM scrape would silently miss
   rows/columns outside the current viewport).
3. POSTs every row to the Apps Script Web App, which appends them to DATA
   and then **dedupes the whole tab by `mission_sas_id`** (column A),
   keeping the most-recently-posted row per mission and deleting older
   duplicates. This matters because the D0-to-D-7 window re-scrapes the
   same missions on every run, and Periscope can fill in a mission's own
   fields (times, task columns) after it was first logged — "last wins"
   keeps the sheet holding the freshest version of each mission's row.

There's no date watermark anymore (earlier versions tracked a
`lastProcessedDate` Script Property for a D-1, then D0, single-day pull —
see version history in `apps-script/Code.gs` if you're curious). Every run
just re-syncs the trailing 7-day window and lets the dedup step keep things
clean.

**Schedule**: twice a day, Asia/Taipei time — 9:00 AM and 9:00 PM
(`.github/workflows/run.yml`; TPE has no DST, so the UTC offset is constant
year-round).

## Setup steps

1. **Apps Script (Google side)**
   - Go to script.google.com > New project. Name it e.g. "Periscope Ramp Ops WebApp".
   - Replace `Code.gs` with `apps-script/Code.gs` from this repo. Add
     `apps-script/appsscript.json` via Project Settings > "Show appsscript.json".
   - Project Settings > Script Properties, add:
     - `AUTH_TOKEN` = a random secret string you generate (e.g. `openssl rand -hex 16`).
       (No `lastProcessedDate` needed anymore — v3 has no watermark.)
   - Deploy > New deployment > Web app. Execute as "Me", access "Anyone".
     Copy the `/exec` URL.
   - **If you're updating an existing deployment** rather than creating a
     new one: Apps Script Web Apps published via "New deployment" are
     pinned to a specific saved version — editing and saving `Code.gs` in
     the editor does NOT change what the live `/exec` URL serves. You must
     go to Deploy > Manage deployments > (pencil/edit icon) > Version >
     "New version" > Deploy for a code change to actually reach the live
     endpoint. (Learned the hard way on 2026-08-30: two rounds of fixes sat
     unpublished on HEAD for a while before this was caught.)

2. **GitHub (this repo)**
   - Create the repo (default assumed: `daralan2412/periscope-ramp-to-sheets`).
   - Push these files as-is.
   - Settings > Secrets and variables > Actions, add:
     - `SHEETS_WEBAPP_URL` = the `/exec` URL from step 1.
     - `WEBAPP_TOKEN` = the same `AUTH_TOKEN` value from step 1.
   - Confirm Actions is enabled for the repo.

3. **First run**
   - Actions tab > "Pull Periscope Ramp Ops (REP-1901) to Google Sheet" >
     Run workflow (manual `workflow_dispatch`), rather than waiting for the
     cron, so you can watch the log and check the sheet afterward.
   - After it succeeds, the twice-daily cron (9am/9pm Asia/Taipei) takes
     over automatically.

## Known edge case

If Periscope shows "Query returned no matching rows" for the D0-to-D-7
window (extremely unlikely given it spans 8 calendar days, but possible if
nothing at all has been logged in that stretch), there's no "Download Data"
menu to click at all. The scraper detects this up front and exits cleanly
with "No rows... Will retry next run" rather than failing — nothing is
posted, so there's nothing to dedupe either.

## Files

- `apps-script/Code.gs` — the Web App: `doGet` is now just a token/health
  check; `doPost` appends posted rows to DATA and dedupes the tab by
  `mission_sas_id`, keeping the freshest row per mission.
- `scrape_and_upload.py` — scrapes REP-1901's rolling D0-to-D-7 CSV export
  (Custom Range, dates computed fresh every run) via Playwright, posts the
  rows to the Web App.
- `.github/workflows/run.yml` — 9am + 9pm Asia/Taipei cron, plus manual trigger. (Note: GitHub fires cron jobs 2-4 h late on busy days, so expect the actual runs closer to 11am / 11pm.)
- `requirements.txt` — `requests`, `playwright`.
