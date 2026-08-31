#!/usr/bin/env python3
"""
scrape_and_upload.py - NEW pipeline, independent of the old
daralan2412/periscope-to-sheets repo/script (which is left untouched).

Source: Periscope/Sisense shared report "REP-1901"
        https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35
Target: Google Sheet "2026_RAW_MISSIONS", tab "DATA"
        via the Apps Script Web App (SHEETS_WEBAPP_URL / WEBAPP_TOKEN secrets).

Flow:
  1. Open REP-1901, set the Date Range filter to a rolling D0-to-D-7 window
     (today back through 7 days ago, via Custom Range with computed
     Start/End dates - not a single day), then use the widget's own
     "Download Data" CSV export (NOT DOM scraping - the Data table is a
     virtualized Sisense grid, so most rows/columns are never in the DOM at
     once; the CSV export is generated server-side and is complete).
  2. POST {"rows": [[...35 cols...], ...]} to the Web App, which appends the
     rows to the DATA tab and then dedupes the whole tab by mission_sas_id
     (column A), keeping the most-recently-posted row per mission.

Rebuilt 2026-08-31 (v4): switched the Date Range filter from Periscope's
"Current Week" preset (a calendar-week bucket that resets to empty every
Sunday) to an explicit rolling trailing-7-day window - "D0 to D-7" - built
from Periscope's "Custom Range" filter with Start/End Date computed fresh
on every run (today in Asia/Taipei, and today minus 7 days). This avoids
the Current Week edge case where the filter returns zero rows right after
the calendar week rolls over (confirmed live on 2026-08-30, a Sunday) even
though there is recent data from the prior week that should still be
re-synced. Everything downstream is unchanged: the widget's CSV export is
still used (not DOM scraping), rows are still POSTed as a full batch, and
the Apps Script side still dedupes the DATA tab by mission_sas_id, keeping
the freshest (most-recently-posted) row per mission - so re-pulling
overlapping days on every run is still safe and expected.

Rebuilt 2026-08-30 (v3, superseded by the above) switched from a
single-day pull tracked by a lastProcessedDate watermark (first D-1, then
D0) to always pulling Periscope's "Current Week" filter, run twice a day
(7am/7pm Asia/Taipei). Current Week necessarily re-scrapes rows already in
the sheet on every run (and a mission's own fields - times, task columns -
can fill in after it was first logged), so there is no watermark anymore:
every run re-syncs a window of recent days, and the Apps Script side is
responsible for collapsing duplicate mission_sas_id rows down to the
freshest one. See Code.gs for the dedup logic.

Rebuilt 2026-08-30 (v2, superseded by the above) after the first live run
failed: the original version DOM-scraped a "table tbody tr" selector that
does not exist on this page (the Data widget is a div-based Sisense
"ninja-grid", not an HTML table), so it silently scraped an unrelated table
and posted malformed rows, which crashed the Apps Script doPost()
(setValues() column-count mismatch) and came back as a non-JSON error page.
The CSV export path used here reads the exact same 35 columns, in the same
order, as the target sheet's header row - confirmed live against the
deployed report.
"""

import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

PERISCOPE_URL = "https://app.periscopedata.com/shared/b9a2f550-7597-46fe-952a-baa20efe4d35"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
LOOKBACK_DAYS = 7  # "D0 to D-7": today back through 7 days ago, inclusive.
WEBAPP_URL = os.environ["SHEETS_WEBAPP_URL"]
WEBAPP_TOKEN = os.environ["WEBAPP_TOKEN"]

HEADERS = [
    "mission_sas_id", "date", "station", "airline_code", "tail_number", "vessel_description",
    "job_name", "mission_name", "arrival_flight_number", "departure_flight_number",
    "org_city", "dest_city", "arr_time", "dep_time", "disp_name", "agent_name", "mission_notes",
    "assigned_date", "start_time", "finish_time",
    "task_1", "task_2", "task_3", "task_4", "task_5", "task_6", "task_7", "task_8", "task_9",
    "task_10", "task_11", "task_12", "task_13", "task_14", "task_15",
]


def check_token():
    """Cheap pre-flight auth check before paying for a headless-browser scrape.

    doGet() no longer decides what to pull (there's no watermark anymore),
    it's just a token/connectivity health check now.
    """
    resp = requests.get(WEBAPP_URL, params={"token": WEBAPP_TOKEN}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Web App auth check failed: {data}")


def compute_date_range_mmddyyyy():
    """D0 to D-7: today (Asia/Taipei) and today minus LOOKBACK_DAYS, both
    formatted MM/DD/YYYY for Periscope's Custom Range Start/End Date inputs.
    Computed fresh on every call so the window is always "as of right now",
    not pinned to whatever day the code was last edited.
    """
    today = datetime.now(TAIPEI_TZ).date()
    start = today - timedelta(days=LOOKBACK_DAYS)
    return start.strftime("%m/%d/%Y"), today.strftime("%m/%d/%Y")


def scrape_last_7_days_csv():
    """Filter REP-1901's Data widget to a rolling D0-to-D-7 window and pull
    its CSV export.

    Uses Periscope's "Custom Range" Date Range filter with Start/End Date
    computed fresh on every run (see compute_date_range_mmddyyyy), rather
    than a built-in preset - this pins down the exact semantics ("today back
    through 7 days ago, inclusive") instead of relying on unclear/undocumented
    behavior of a preset like "7 Days". Verified live: filling Start/End Date
    with explicit MM/DD/YYYY values and clicking Apply correctly narrows the
    Data widget and the resulting breadcrumb to that exact range.

    Uses the widget's built-in "Download Data" export instead of scraping the
    DOM: the Data widget is a virtualized grid (rows AND columns are only
    rendered near the viewport), so a DOM scrape would silently miss most of
    a real week's rows/columns. The CSV export is generated server-side and
    is complete regardless of what happened to be scrolled into view.

    Returns the CSV text, or None if the widget shows "Query returned no
    matching rows" - Sisense doesn't even offer a "Download Data" menu item
    when there's nothing to export, so this has to be checked for explicitly
    rather than treated as a scrape failure.
    """
    start_str, end_str = compute_date_range_mmddyyyy()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        # Capture the export URL from the first matching request, then poll
        # it ourselves rather than relying on the page's own retry behavior.
        # Registered before any interaction so we can't race the request.
        export_url = {"value": None}

        def on_response(resp):
            if export_url["value"] is None and "/download_csv/" in resp.url:
                export_url["value"] = resp.url

        page.on("response", on_response)

        try:
            page.goto(PERISCOPE_URL, wait_until="networkidle", timeout=60_000)

            # Make sure the dashboard has actually rendered its widgets (not
            # just "network idle") before touching anything.
            page.wait_for_selector(".filters-bar-label", timeout=30_000)
            page.wait_for_selector(".ninja-grid", timeout=30_000)

            # Open the report-level filters panel.
            page.locator(".filters-bar-label").first.click()
            page.wait_for_selector(".radio-button-group", timeout=10_000)
            page.wait_for_timeout(300)

            # Date Range column: select "Custom Range". Unlike the preset
            # options (Current Week, 7 Days, etc.), which live inside
            # .radio-button-group > .small-radio-button, "Custom Range" is
            # rendered in its own sibling container - .custom-date-option -
            # under .options (confirmed live via DOM inspection: a selector
            # scoped to .radio-button-group never matches it, which is why
            # an earlier version of this selector timed out in CI). It's
            # always the first item in the Date Range column, so no scroll
            # is needed to reach it. force=True because a plain click here
            # can hit a transient overlap issue while the panel settles.
            custom_range_option = page.locator(".custom-date-option .small-radio-button").first
            custom_range_option.click(force=True)
            page.wait_for_timeout(800)

            # Fill Start/End Date with the freshly computed D-7/D0 window.
            # force=True for the same transient-overlap reason as above.
            #
            # This is a jQuery UI-style datepicker (class "hasDatepicker")
            # that only registers a value in its own real filter state in
            # response to real keystrokes - a bulk .fill() (optionally
            # followed by dispatching synthetic "change"/"blur"/"focusout"
            # events) leaves the field SHOWING the right text but the
            # underlying filter state stays unset, so Apply stays disabled
            # and/or silently applies nothing (confirmed both ways in CI:
            # a bare .fill() and a .fill() + dispatch_event() combo both
            # left the Apply button with class "...apply-button disabled").
            # press_sequentially() sends one real keydown/keypress/keyup
            # per character, which is what a datepicker actually listens
            # for, and is what worked reliably in manual verification.
            start_input = page.locator(".range-start")
            end_input = page.locator(".range-end")

            start_input.click(force=True)
            start_input.clear()
            start_input.press_sequentially(start_str, delay=40)
            end_input.click(force=True)
            end_input.clear()
            end_input.press_sequentially(end_str, delay=40)

            # Don't click Apply on a fixed delay - wait for the actual
            # signal that the datepicker has validated both typed dates:
            # the Apply button loses its "disabled" class. Confirmed live
            # in CI that even with real keystrokes, a short fixed wait
            # isn't always enough - the button can still read "disabled"
            # for a bit while the widget's own validation catches up, and
            # clicking (even with force=True) while it's disabled is a
            # no-op in the app's own click handler, silently applying
            # nothing.
            page.wait_for_function(
                """() => {
                    const btn = document.querySelector('.apply-button');
                    return btn && !btn.classList.contains('disabled');
                }""",
                timeout=15_000,
            )

            # Apply the filter and let the widget refresh, then verify the
            # dates actually committed - the breadcrumb only shows "<date>
            # to <date>" once the range is genuinely applied. This is a
            # hard failure (not a "no rows" case) if it doesn't happen.
            apply_button = page.locator(".apply-button")
            breadcrumb = page.locator(".filters-bar-label").first
            apply_button.click(force=True)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            if " to " not in (breadcrumb.text_content() or ""):
                raise RuntimeError(
                    "Custom Range Start/End Date did not commit - breadcrumb never showed "
                    "'<date> to <date>' after Apply"
                )

            # Find the "Data" widget specifically (report may gain more
            # widgets later).
            widget = page.locator(".widget-container", has=page.locator(".widget-title", has_text="Data")).first
            widget.scroll_into_view_if_needed()

            # No rows in this date range - Sisense shows this in place of
            # the grid and doesn't offer a "Download Data" menu item at all,
            # so check for it up front instead of timing out waiting for a
            # menu that will never appear.
            if widget.locator(".error-message", has_text="no matching rows").count() > 0:
                browser.close()
                return None

            # Open the per-widget menu.
            widget.hover()
            page.wait_for_timeout(500)
            widget.locator(".controls .expand.button").click(force=True)
            page.wait_for_selector("text=Download Data", timeout=10_000)
            page.get_by_text("Download Data", exact=True).click()

            deadline = time.time() + 20
            while export_url["value"] is None and time.time() < deadline:
                page.wait_for_timeout(250)
            if export_url["value"] is None:
                raise RuntimeError("Did not observe a download_csv request after clicking Download Data")
        except Exception:
            try:
                page.screenshot(path="debug_failure.png", full_page=True)
                with open("debug_failure.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception as diag_err:
                print(f"(could not capture debug artifacts: {diag_err})", file=sys.stderr)
            browser.close()
            raise

        csv_text = None
        deadline = time.time() + 90
        while time.time() < deadline:
            resp = page.context.request.get(export_url["value"])
            if resp.status == 200:
                csv_text = resp.text()
                break
            page.wait_for_timeout(2000)

        browser.close()

        if csv_text is None:
            raise RuntimeError("Timed out waiting for the CSV export to become ready")
        return csv_text


def parse_csv_rows(csv_text: str):
    reader = csv.reader(io.StringIO(csv_text))
    try:
        header = next(reader)
    except StopIteration:
        return []

    if [h.strip() for h in header] != HEADERS:
        print(
            "WARNING: CSV header does not match the expected 35-column schema. "
            f"Got: {header}",
            file=sys.stderr,
        )

    rows = []
    for row in reader:
        # Defensive pad/truncate: never let a stray column-count mismatch
        # (a trailing blank line, a schema tweak upstream, etc.) crash the
        # whole batch - Apps Script's setValues() requires a fixed width.
        if len(row) < len(HEADERS):
            row = row + [""] * (len(HEADERS) - len(row))
        elif len(row) > len(HEADERS):
            row = row[: len(HEADERS)]
        rows.append(row)
    return rows


def post_rows(rows: list):
    resp = requests.post(
        WEBAPP_URL,
        params={"token": WEBAPP_TOKEN},
        data=json.dumps({"rows": rows}),
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Web App POST failed: {data}")
    return data


def main():
    check_token()

    start_str, end_str = compute_date_range_mmddyyyy()
    print(f"Pulling Periscope REP-1901 data for {start_str} to {end_str} (D-7 to D0, Asia/Taipei)...")
    csv_text = scrape_last_7_days_csv()
    if csv_text is None:
        print(f"No rows for {start_str} to {end_str} - nothing to pull. Will retry next run.")
        return

    rows = parse_csv_rows(csv_text)
    print(f"Scraped {len(rows)} rows for {start_str} to {end_str}.")

    result = post_rows(rows)
    print(
        f"Posted {result.get('rows_received')} rows; "
        f"{result.get('duplicates_removed')} duplicate mission_sas_id row(s) removed on the sheet side."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
