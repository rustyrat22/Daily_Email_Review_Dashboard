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

## Recent work / changelog

- (current) Added CSV ingestion: `parse_email_csv`, `load_emails` now globs
  `*.csv` too, `Category` → labels, Date+Time combined, label/non-email CSVs
  excluded. README + this file updated. Rebuilt and verified 0 exceptions.
- Earlier session: emaail detail Prev/Next + Select email dropdown synced via
  `detail_idx` session key with `on_click` callbacks; fixed table-vs-detail
  index mismatch (single sort); deleted `generate_sample_emails.py`.

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