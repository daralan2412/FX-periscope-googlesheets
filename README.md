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

## Assumptions made building this (please correct any that are wrong)

Only one of the four setup questions got an explicit answer from you: **pull
buffer, cron at 7:00 AM — and since this data is all TPE-station ramp ops, I
anchored "today" and the cron to Asia/Taipei, not America/Bogota.** Buffer was
originally D-1, changed to D0 (same-day pull allowed) on 2026-08-30 per
explicit instruction. The rest default to the recommended option since they
weren't answered:

1. **Deployment path**: I have no GitHub or Google login from this session
   (verified — both loaded logged-out), so I can't push a repo or deploy the
   Web App myself. This is delivered as ready-to-deploy files for you to set
   up (see steps below). If you'd rather I drive it directly, log into
   GitHub and Google in the linked browser and tell me — I can then create
   the repo and deploy the Apps Script project myself.
2. **Repo name**: assumed `periscope-ramp-to-sheets` under the same
   `daralan2412` account. Rename/move it if you want it elsewhere.
3. **Starting point**: the DATA tab already has ~20 rows for Aug 3–5, 2026,
   several with placeholder `job_name` values like `"test"` — these look
   like manual test entries, not a real pull. I left them alone and did
   **not** set `lastProcessedDate` for you — you must seed it manually (see
   step 4 below) before the first run, choosing whichever date makes sense:
   the day before the oldest real day you want pulled, or the day before
   today for a "start fresh, no backfill" approach.

## What I could NOT verify (be aware before the first live run)

The Periscope report's date-range filter (`Start Date`/`End Date` textboxes)
and its "Data" table were confirmed live in a browser at build time, but only
against a small, unfiltered sample (~12 rows, no date filter applied). I did
**not** verify:

- Whether the "Data" table virtualizes/lazy-loads rows once a full day's
  volume (the old pipeline saw 400–2,400 rows/day) is rendered — the scraper
  scrolls and re-collects to handle that, but it's untested at that volume.
- The exact table CSS selector under load (`scrape_and_upload.py` uses the
  generic `table` selector, marked `TODO-VERIFY` in the code).
- Whether REP-1901 ever shows a password gate under different conditions
  (the old report did; this one didn't when tested).

Recommend running the workflow once via `workflow_dispatch` against a known
day and manually checking the row count in Periscope's UI against what
landed in the sheet before trusting the daily cron.

## Setup steps

1. **Apps Script (Google side)**
   - Go to script.google.com > New project. Name it e.g. "Periscope Ramp Ops WebApp".
   - Replace `Code.gs` with `apps-script/Code.gs` from this repo. Add
     `apps-script/appsscript.json` via Project Settings > "Show appsscript.json".
   - Project Settings > Script Properties, add:
     - `AUTH_TOKEN` = a random secret string you generate (e.g. `openssl rand -hex 16`).
     - `lastProcessedDate` = `YYYY-MM-DD`, the day BEFORE you want the first pull.
   - Deploy > New deployment > Web app. Execute as "Me", access "Anyone".
     Copy the `/exec` URL.

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
   - After it succeeds, the daily cron (`0 23 * * *` UTC = 7:00 AM
     Asia/Taipei) takes over automatically.

## Files

- `apps-script/Code.gs` — the Web App: `doGet` returns the next date to pull
  (D0 buffer, Asia/Taipei calendar); `doPost` appends that date's rows to
  the DATA tab and advances `lastProcessedDate`.
- `scrape_and_upload.py` — calls the Web App, scrapes REP-1901 for the
  target date via Playwright, posts the rows back.
- `.github/workflows/run.yml` — daily cron + manual trigger.
- `requirements.txt` — `requests`, `playwright`.
