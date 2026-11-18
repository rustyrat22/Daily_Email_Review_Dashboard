# Daily Email Review Dashboard

A Docker + Streamlit dashboard that reviews email exports. It scans a data
folder for **Gmail API message-dump files** (one request/response pair per
email, exactly like `data/1a0a5a18017c3e70.txt`) and **email CSV exports**,
parses them all, and lets you filter, search, chart, and drill into full
message bodies.

## Project layout

```
Daily_Email_Review_Dashboard/
├── app/
│   ├── app.py                    # the Streamlit dashboard
│   ├── parse_emails.py           # parser for dump files and email CSVs
│   └── requirements.txt
├── data/
│   ├── 1a0a5a18017c3e70.txt      # example dump (one email per file)
│   ├── label naming.csv          # label_id -> label_name mapping
│   ├── *.txt                     # add as many dumps as you like
│   └── *.csv                     # ...or email CSV exports
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## Input format

Each `data/*.txt` file mirrors the given sample — a plain-text file with a
`Request` block (the `messageId` fetched) and a `Response` block (the full
message):

```
Request

{
  "messageFormat": "PLAIN_TEXT",
  "messageId": "1a0a5a18017c3e70"
}
Response

{
  "id": "1a0a5a18017c3e70",
  "subject": "Data Engineer at Ladders: up to $150K/year",
  "sender": "jobalerts-noreply@linkedin.com",
  "toRecipients": ["hampton.jp@gmail.com"],
  "date": "2026-09-15T15:13:39Z",
  "plaintextBody": "Your job alert for Data Engineer in Appomattox ...",
  "labelIds": ["UNREAD", "Label_7469349110985185565"],
  "threadId": "...", "historyId": "16886680",
  "internalDate": "1789485219000", "sizeEstimate": 110619
}
```

Files that can't be parsed are reported under **Issues** in the app instead of
crashing the dashboard.

### CSV exports

Email CSVs dropped next to the dump files (top level of `data/`) are ingested
too and merged into the same views. Columns are matched case/space/underscore
tolerant; `MessageID`/`id`, `Sender`/`from`, `Subject`, `Date` (`Time` is
combined when present), `Body`/`plaintextBody`, `Snippet`, `Category`,
`labels`/`labelIds`, and boolean `unread`/`important` are recognized. The
`Category` column is added to the label list, so you can filter the existing
Label dropdown by source (LinkedIn, ZipRecruiter, ...). CSVs that aren't email
data are skipped and reported under **Issues**.

### Label names

Label IDs like `Label_7469349110985185565` are opaque, so the app resolves them
against `data/label naming.csv` (columns `label_id, label_name`) and displays
friendly names everywhere (filter, chart, detail view). Unknown labels keep
their raw value. Point `LABEL_CSV` somewhere else if yours lives elsewhere.

## Job email parser (Development)

`app/parse_job_emails.py` extracts `date_of_email`, `job_title`, `company`,
`salary`, `url`, and `subject_from_email` from raw site alert emails. It
auto-detects the source from the `From`/`Category` headers and supports:

- **Indeed** — single-job and multi-job alert emails (full job URLs available)
- **ZipRecruiter** — multi-job daily alerts (titles, companies, salaries; no URLs in the source files)
- **David Careers / Glassdoor** — the one job named in the subject/body (no salary or URL in the source files)

Missing values are returned as `Unknown`. Only the standard library is used,
so it runs locally without Docker:

```bash
python app/parse_job_emails.py data/site_emails/indeed/indeed_multiple_job_email.txt
python app/parse_job_emails.py data/site_emails/ziprecruiter/2026-11-13_135852_ZipRecruiter_69.30.txt
python app/parse_job_emails.py data/site_emails/david/2026-11-14_103454_DavidCareers_Senio.txt
python app/parse_job_emails.py data/site_emails/glass_door/2026-11-14_004327_Glassdoor_Busin.txt
python app/parse_job_emails.py <file> --json
```

Import the generic dispatcher with `from parse_job_emails import parse_job_emails`
(returns a list, one dict per job). Site-specific functions
`parse_indeed_job_emails`, `parse_ziprecruiter_email`,
`parse_david_careers_email`, and `parse_glassdoor_email` are also available.

## Run it

```bash
docker compose up --build
```

Then open http://localhost:8501

`./data` is mounted at `/data` and the app scans only the top level of
`/data` for `*.txt` dump files and email `*.csv` exports. Drop new files in
and hit **🔄 Refresh data** — no rebuild needed.

Only files directly inside `data/` are used; subdirectories (such as
`data/temp data/`) and everything inside them are ignored.

## Using your own data

1. Put one file per email in `data/` (top level only), each a
   Request/Response text pair as shown above (the file name itself is
   irrelevant) — or drop email CSV exports alongside them (see **CSV exports**).
2. Mount your own folder or change the env var:
   `DATA_DIR=/data` in `docker-compose.yml` (or
   `DATA_DIR=/path/to/folder` if running `streamlit run app/app.py` locally).
3. Empty or buggy files show up under **Issues** with their filename.

## What the app shows

- Sidebar **Mode** switch (Production / Development). Production is the original dashboard. Development puts the multi-select **Label** filter first in the sidebar, allows selecting multiple labels, omits the **Sender** filter, and adds an **Overivew** button next to Refresh data that switches between the two views: messages/email detail (default) or the Overview metrics and charts. Only one view is shown at a time.
- Overview metrics: email count, unread count, unique senders, average size
- Volume over time, top senders, and label breakdowns (labels include the
  CSV `Category` column)
- Searchable message table (subject / sender / snippet / body) with date
  range, sender, and label filters, plus "Important only" / "Unread only"
  toggles
- Per-email drill-down: full message body rendered readably (clickable
  links, visual separators, scrollable), labels, header info, a "Select
  email" dropdown, ◀ Prev / Next ▶ navigation, and a download button for
  the raw body

## Notes

- Parsing is cached for 60 s (`ttl=60`) so the dashboard doesn't re-scan the
  folder on every widget interaction; the Refresh button clears the cache.
- The parser extracts balanced JSON objects with a quote-aware scanner, so
  braces inside message text don't confuse it.
- Scaling up: if you collect thousands of dumps, the `load_emails` scan is
  the hot path — it's already file-per-email, so consider moving parsed rows
  to a lightweight database later. The dashboard logic won't need to change.