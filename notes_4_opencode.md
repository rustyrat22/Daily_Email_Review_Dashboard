# Notes for opencode — Daily Email Review Dashboard build

Session log / knowledge file. Read this before resuming work so you don't
re-discover the setup the hard way.

## What this is

A Docker + Streamlit dashboard (single-page, `layout="wide"`) that reviews
email exports dropped into `data/`. It ingests **both**:

- Gmail API message-dump `.txt` files (one `Request`/`Response` JSON pair per
  file, e.g. `data/1a0a5a18017c3e70.txt`), and
- email CSV exports (e.g. `data/2026 Job Hunt 20260917.csv`).

Everything from both sources is merged into one dataframe and shown through
the same UI. There are no tabs/pages and no DB — parse → pandas → Streamlit.

## Environment / how to run

- Platform: **Windows** (PowerShell 5.1) + **Docker Desktop**. Docker Desktop
  must be running (it sometimes needs a manual start).
- Local machine Python 3.10 (`C:\Python`) has **no** pandas/streamlit — the
  `parse_emails.py` module is stdlib-only and CAN be tested directly with
  `python`. Streamlit/pandas are only available inside the container.
- Build & run:

  ```
  docker compose up --build -d
  ```

- Dashboard: http://localhost:8501
- Container name: `daily_email_review_dashboard-dashboard-1` (from service
  name `dashboard` in docker-compose.yml).
- Mounts `./data:/data`; env vars `DATA_DIR=/data`, `LABEL_CSV=/data/label naming.csv`.
- Image base: python:3.11-slim; `requirements.txt` pins `streamlit==1.38.0`,
  `pandas==2.2.2`; Dockerfile runs `streamlit run app.py --server.port=8501`.

## Repo layout

```
app/
  app.py            # the whole dashboard (300 lines, no modules)
  parse_emails.py   # stdlib-only parser: dumps + CSVs + label mapping
  requirements.txt  # streamlit==1.38.0, pandas==2.2.2
data/
  1a0a5a18017c3e70.txt  + 8 more .txt dumps  (9 parse OK)
  2026 Job Hunt 20260917.csv/.20260919/.20260921    (real CSV exports)
  Emails 20260904-20260917.txt    # DIFFERENT format -> intentionally fails, listed under Issues
  label naming.csv                # label_id -> label_name (messy CSV!)
  temp data/                      # ~100 generated test files + sample_data.csv — IGNORED
Dockerfile
docker-compose.yml
README.md
notes_4_opencode.md   # this file
```

Not a git repo. `app/generate_sample_emails.py` was **deleted on purpose** —
do NOT regenerate/generate test emails.

## Data input rules (important constraints)

- Only files **directly inside** `data/` (top level) are scanned. Implemented
  via `p.parent == directory` filter — this is what excludes `data/temp data/`.
- `data/temp data/` and everything in it is deliberately ignored, including
  `sample_data.csv`. Keep it that way.
- The label-mapping CSV (`LABEL_CSV`) is excluded from email ingestion both by
  exact-path comparison and by header detection (`label_id, label_name`).
- CSV email detection: a CSV is treated as email data only if it has
  ≥1 recognized email column (id/messageId, subject, sender/from, body,
  snippet, date, ...). `Category`/`Time` alone don't count. Unrecognized CSVs
  are reported under **Issues**.
- Force a re-read without a rebuild: the app re-parses on a 60 s cache TTL and
  there is a **🔄 Refresh data** button that clears the cache and reruns.

## The parser (app/parse_emails.py)

- `_iter_json_blocks(text)` — quote-aware balanced-`{}` scanner (braces inside
  strings don't confuse it). Dump files don't have to be strictly valid JSON
  overall.
- `parse_email_file(path, label_map)` → one row dict, or None.
- `parse_email_csv(path, label_map)` → list of row dicts, or None (see rules
  above). Column matching is case/space/underscore tolerant (normalized via
  `_norm_header`).
  - Recognized CSV columns: `id`/`messageId`, `subject`, `sender`/`from`,
    `toRecipients`/`to`, `date`, `internalDate`, **`time` (combined with date)**
    snippet, `body`/`plaintextBody`, `labelIds`/`labels`,
    `threadId`, `historyId`, `sizeEstimate`, `unread`, `important`, **`category`**.
  - `Category` (e.g. "LinkedIn", "ZipRecruiter") is appended to the label list,
    so emails sort/filter by source through the existing Label UI.
  - `unread`/`important` are derived from label tokens `UNREAD`/`IMPORTANT`
    unless explicit boolean columns exist.
- `load_emails(data_dir, label_csv=None)` → `(rows, errors)`. Globs `*.txt`
  then `*.csv` at top level only, merges, returns None if nothing parsed.
- Row dict keys (also the dataframe columns): `file` (for CSV rows it's
  `"<csvname>.csv#<rownum>"`), `id`, `thread_id`, `subject`, `sender`,
  `sender_domain`, `to_recipients`, `date` (aware UTC or naive), `snippet`,
  `body`, `labels` (comma-joined resolved names), `important`, `unread`,
  `size_estimate`, `history_id`.

### label naming.csv quirks (do not "fix" blindly)

The mapping file is messy by hand-editing: header is ` label_name` (leading
space) and values are wrapped in literal double quotes, e.g.
`, "Linked in"`, `, "zip-recruiter"`. `load_label_names()` handles this with a
tolerant `csv` reader. Labels NOT in the map stay raw (`Label_...`), but the
UI's Label dropdown filters those out on purpose. Current map:
Linked in, zip-recruiter, Glass Door, David Careers.

### Known data quirks

- `Emails 20260904-20260917.txt` (different format) fails to parse and shows
  under Issues — **expected**, do not treat as a regression.
- Current totals with the real data: **109 rows** (9 txt + 100 csv:
  34 + 34 + 32 across the three "2026 Job Hunt" CSVs). Category label counts:
  LinkedIn 43, ZipRecruiter 14, etc. One row's category is literally
  "2026 Job Hunt" (real data, not a bug).

## The dashboard (app/app.py)

- `load_data` is `@st.cache_data(ttl=60)`; wraps `load_emails`.
- Sidebar **Mode** radio (`app_mode` session key): `Production` / `Development`,
  horizontal buttons at the top of the sidebar, above Filters. `main()` is only
  a dispatcher: `Production` calls `render_production_dashboard()`, which is the
  original dashboard and must stay unchanged for this build. The `Development`
  branch calls `render_development_dashboard()`. Development filter order is
  Label first (multiselect supports multiple; OR match), then date range,
  Search, Important only, Unread only; the Sender multiselect is gone. Put new
  changes in the Development renderer, never in the Production one.
  `_render_filtered_results()` holds the results views shared by Development;
  Production still has its own inline copy, unchanged. Development also has an
  **Overivew** button (exact spelling) beside Refresh data; it toggles
  `st.session_state["show_overview"]` (default False) and `_render_filtered_results`
  renders either the Messages table + Email detail (flag False) OR the Overview
  metrics + three charts (flag True) — never both. The Issues expander is always
  shown. When Overview is active the dataframe, email selectbox, Prev/Next and
  download button are not rendered at all.
- Sidebar filters: date range (From/To), `Search` text box, `Sender`
  multiselect, `Label` multiselect (matches on word boundaries), `Important
  only` checkbox, `Unread only` checkbox.
- Overview metrics: Emails, Unread, Unique senders, Avg size.
- Charts: volume over time (daily bar), Top senders, Top labels (`_labels_top`).
- **Messages** dataframe: `date, sender, subject, snippet (120-char), unread`,
  `use_container_width`, `hide_index`, `selection_mode="single-row"`,
  `on_select="rerun"`. Event → selected rows handled by `_selected_rows`
  (tolerant of dict/object API shape). The table order is `fdf` which is sorted
  **date desc once** — this ordering is load-bearing (see below).
- **Email detail** block:
  - Current row index lives in `st.session_state["detail_idx"]`.
  - Table row click sets it; a `st.selectbox` with `key="detail_idx"`
    ("Select email", options = `range(len(fdf))`, format func shows date-sender-subject) is literally bound to the same key.
  - **◀ Prev / Next ▶** buttons mutate the same key via `on_click=_nav_email(+/-1)`. Callbacks run before the script body, so updating the selectbox's own key there is always legal (avoids Streamlit's "cannot modify widget key after instantiation" error).
  - Guard: if `detail_idx` out of bounds (filters changed), reset to 0.
  - Body rendered by `_markup_body`: HTML-escaped, URLs clickable (long URLs
    truncated to ~85 chars + "…"), lines of `=`, `-`, `*` → `<hr>`, rendered in
    a scrollable `.email-body` div (max-height 440 px).
  - `Download body as .txt` button (mime text/plain).
- **Issues** expander at the bottom lists parse errors (captions).

## UI verification workflow (use this to test changes)

Local machine cannot run streamlit, so tests run inside the container:

```
docker exec daily_email_review_dashboard-dashboard-1 python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('app.py', default_timeout=30).run()
assert not at.exception, at.exception
print('rows:', at.dataframe[0].value.shape[0])
"
```

Gotchas learned the hard way:
- **Re-fetch widget elements after every `at.run()`** — stale element
  references give false results. e.g. after `at.multiselect[0].set_value([...])`,
  grab `at = at.run()` then read `at.dataframe[0]` fresh.
- Find the Label widget by index: `at.multiselect[0]` = Sender,
  `at.multiselect[1]` = Label.
- The `Thread 'MainThread': missing ScriptRunContext` warning is normal in
  bare AppTest runs — ignore it.
- Streamlit/pandas live only in the container; run config-driven tests via
  `docker compose up --build -d` then `docker exec ...`.

Known-good assertion set (post-CSV support): 0 exceptions; 109 rows;
label filter "LinkedIn" → 43 rows, "ZipRecruiter" → 14; detail nav selects
next/prev within bounds.

## Job email parser (Development only, not wired into the UI yet)

- `app/parse_job_emails.py` is stdlib-only and parses raw RFC 822 emails from
  `data/site_emails/` (those files are Git-ignored).
- Every parser returns dicts with the keys `date_of_email`, `job_title`,
  `company`, `salary`, `url`, `subject_from_email`. Missing values are returned
  as `Unknown`, including salary and URL when the source has none.
- Date/Subject come from decoded headers (plain body decoded via
  `part.get_content()`). Site is detected from `From`/`Category` headers by
  `parse_job_emails(text)`, which dispatches to the site-specific parsers and
  falls back to the Indeed parsers.
- Indeed: `parse_indeed_job_email(text)` (single), `parse_indeed_job_alert_email(text)`
  (many), or `parse_indeed_job_emails(text)` (auto-detect; always a list). Job
  alerts split on blank lines and filter on `https://www.indeed.com/rc/clk/dl`;
  title = first line (minus `Compensation: ...`), company = second line before
  ` - `, salary = first `$...` line after company (title compensation fallback),
  URL = full `rc/clk/dl?jk=` link.
- ZipRecruiter: `parse_ziprecruiter_email(text)` splits daily alerts on blank
  lines, normalizes literal `\u2022` to `•`, and reads title / company before
  the first `•` / first `$...` salary line. Verified: 15 jobs (including one
  `Unknown` salary); no URLs are present in the source files.
- David Careers: `parse_david_careers_email(text)` reads the one named job from
  the subject/body (`<title> opportunity at <company>`). Verified:
  `Senior Associate, Systems Engineer 1 | L 3 Harris Technologies | Unknown |
  Unknown` (no salary/URL in source).
- Glassdoor: `parse_glassdoor_email(text)` reads `<title> at <company> and N
  more job` when present, otherwise the company from `... is hiring`. Verified:
  `Business Analyst | Delta Star | Unknown | Unknown` and
  `Unknown | Hitachi Energy | Unknown | Unknown` (no salary/URL in source).
- CLI: `python app/parse_job_emails.py <file> [--json]` prints a numbered list
  or JSON list for every supported site. Verified against single-job Indeed,
  multi-job Indeed (21 jobs), and all four ZipRecruiter/David/Glassdoor files.

## Recent work / changelog

- (current, branch `separate_sites`) Added/expanded `app/parse_job_emails.py`
  for extracting job details from Indeed, ZipRecruiter, David Careers, and
  Glassdoor emails. In the Development view the **Messages** table is populated
  only from `data/site_emails/` and its sub-folders (recursive `*.txt` glob),
  never from the root email dumps/CSVs. Columns are exactly Date of Email,
  Company, Job Title, Salary, URL, Subject of Email; the separate "Job Listings"
  section was removed. Cached `load_job_listings`, Site filter, search, and
  Refresh jobs apply; Email detail/Overview still use the email dumps, and
  Development no longer `st.stop()`s when those are absent. Added
  `parse_subject_only_email(text)` fallback so subject-only alerts (e.g. the two
  ZipRecruiter "TEKsystems has a ... opening" / "may want to hire you" emails)
  still produce a row. Verified with AppTest: 45 files → 79 rows across david,
  glass_door, indeed, kimble_group, linked_in, ziprecruiter; Production unchanged
  (original 6-column Messages table, 638 rows); Site filter works; Overivew still
  hides Messages/Email detail; empty SITE_EMAILS_DIR degrades to a friendly
  info message.
- Added sidebar Production/Development mode
  radio and split `main()` into a dispatcher plus frozen
  `render_production_dashboard()`. Development now has its own renderer: Label
  multiselect first (multiple allowed, OR match), no Sender filter. Verified
  in-container AppTest: 0 exceptions; Production shows `Sender`, `Label` and
  638 rows; Development shows only `Label` and 638 rows unfiltered; selecting
  `LinkedIn` + `ZipRecruiter` gives 337 rows vs 277 for `LinkedIn` alone.
  Also verified the Overivew button: hidden Overview initially, clicking shows
  Overview / Volume over time / Top senders / Top labels, clicking again hides
  them, and Messages / Email detail disappear while Overview is active;
  Production has no such button and still shows all sections together.
- Added CSV ingestion: `parse_email_csv`, `load_emails` now globs
  `*.csv` too, `Category` → labels, Date+Time combined, label/non-email CSVs
  excluded. README + this file updated. Rebuilt and verified 0 exceptions.
- Earlier session: emaail detail Prev/Next + Select email dropdown synced via
  `detail_idx` session key with `on_click` callbacks; fixed table-vs-detail
  index mismatch (single sort); deleted `generate_sample_emails.py`.

## Tests / Failures diagnostics

- `app/data_checks.py` holds the check functions (stdlib + the two parsers):
  `check_data_directory`, `check_site_directory`, `check_email_dumps`,
  `check_email_csvs`, `check_site_emails`, `check_messages_table`, plus
  `run_all_checks` and a `main()` CLI (`python app/data_checks.py [--json]`).
  Each `CheckResult` carries a pass flag, summary, and file-level failures with
  the reason a file cannot be shown. `messages_table(jobs_df)` is the shared
  builder for the exact six-column Development Messages table so the check and
  the UI stay in sync.
- Added a **Tests/Failures** button to the left sidebar in `main()`; clicking
  toggles a report at the top of the page (both modes). `_render_test_report()`
  shows a ✅/❌ expander per check, auto-expands failures, and lists reasons.
  Checks re-read current files on each run, so reopening after Refresh data /
  Refresh jobs verifies the new batch.
- Fixed parser normalization found by the checks: `parse_indeed_job_email` and
  `_parse_job_block` now use `_make_job`, so blank title/company/URL become
  `Unknown` instead of empty cells.
- Current check result: 5/6 pass. The genuinely unparseable
  `Emails 20260904-20260917.txt` dump is reported with its reason
  ("no valid Request/Response JSON found"). All 45 site files → 79 jobs pass.

## Open questions / possible next steps

- Volume chart + Top senders/labels only plot the **filtered** set — fine by
  design, but a future "vs. total" overlay is possible.
- Only 9 txt dumps present; the other ~41 emails exists only as CSV rows
  (id column present) — someone could re-fetch those as dumps if full bodies
  are needed for them.
- CSV rows have mostly-empty `body` (semicolon-separated job digests) and no
  `snippet` — the table's snippet column will look sparse for CSV emails.
- If data volume grows thousands of files, `load_emails` re-scan is the hot
  path (see README "Notes": a lightweight DB cache is the escape hatch).