"""Diagnostic checks for the dashboard's data loaders and parsers.

Each check returns a CheckResult with a pass/fail flag, a summary, and a list
of file-level failures that includes the reason a file could not be shown.

Run from the command line (from the app/ folder or with app/ on sys.path):

    python app/data_checks.py
    python app/data_checks.py --data-dir data --site-dir data/site_emails
    python app/data_checks.py --json

Or import ``run_all_checks`` and ``check_messages_table`` from the app.
"""
import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from parse_emails import load_label_names, parse_email_csv, parse_email_file
from parse_job_emails import parse_job_emails, _FIELDS

REQUIRED_EMAIL_FIELDS = ("subject", "sender", "date", "body", "labels")
MESSAGE_FIELD_MAP = {
    "date_of_email": "Date of Email",
    "company": "Company",
    "job_title": "Job Title",
    "salary": "Salary",
    "url": "URL",
    "subject_from_email": "Subject of Email",
}
MESSAGE_COLUMNS = tuple(MESSAGE_FIELD_MAP.values())


def messages_table(jobs_df):
    """Build the exact DataFrame rendered as the Development Messages table."""
    if jobs_df is None:
        return None
    return jobs_df[list(MESSAGE_FIELD_MAP)].rename(columns=MESSAGE_FIELD_MAP)


def load_jobs(site_dir):
    """Build the raw jobs DataFrame (with Site) used by the app/CLI."""
    rows = []
    root = Path(site_dir)
    if root.is_dir():
        for path in sorted(root.rglob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                for job in parse_job_emails(text):
                    row = dict(job)
                    row["site"] = path.parent.name
                    row["source_file"] = path.name
                    rows.append(row)
            except Exception:
                pass  # check_site_emails() reports the reason for bad files
    return pd.DataFrame(rows, columns=list(_FIELDS) + ["site", "source_file"])


@dataclass
class CheckResult:
    """One check's outcome, including why individual files could not be shown."""

    name: str
    passed: bool
    summary: str
    failures: list = field(default_factory=list)

    def as_dict(self):
        return {
            "name": self.name,
            "passed": self.passed,
            "summary": self.summary,
            "failures": list(self.failures),
        }


def _label(path, root):
    """Display path relative to its root, so failures are easy to locate."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _reason(exc):
    return f"{type(exc).__name__}: {exc}"


def check_data_directory(data_dir):
    """The root email data folder exists and contains files to display."""
    root = Path(data_dir)
    if not root.is_dir():
        return CheckResult(
            "Data directory", False, f"Not found: {data_dir}",
            [f"{data_dir}: directory does not exist"],
        )
    txt = [p for p in root.glob("*.txt") if p.is_file()]
    csv = [p for p in root.glob("*.csv") if p.is_file()]
    if not txt and not csv:
        return CheckResult(
            "Data directory", False, "No email files",
            [f"{data_dir}: no .txt or .csv files found"],
        )
    return CheckResult(
        "Data directory", True, f"{len(txt)} .txt + {len(csv)} .csv files"
    )


def check_site_directory(site_dir):
    """The site_emails folder exists and has .txt files in its sub-folders."""
    root = Path(site_dir)
    if not root.is_dir():
        return CheckResult(
            "Site email directory", False, f"Not found: {site_dir}",
            [f"{site_dir}: directory does not exist"],
        )
    files = [p for p in root.rglob("*.txt") if p.is_file()]
    if not files:
        return CheckResult(
            "Site email directory", False, "No email files",
            [f"{site_dir}: no .txt files in any sub-folder"],
        )
    folders = sorted({p.parent.name for p in files})
    return CheckResult(
        "Site email directory", True,
        f"{len(files)} files in {len(folders)} folders",
    )


def check_email_dumps(data_dir, label_csv=None):
    """Every root .txt dump parses into a row with the fields the UI needs."""
    root = Path(data_dir)
    files = sorted(p for p in root.glob("*.txt") if p.is_file()) if root.is_dir() else []
    label_map = load_label_names(label_csv) if label_csv else {}
    failures = []
    parsed = 0
    for path in files:
        try:
            row = parse_email_file(path, label_map)
            if row is None:
                failures.append(
                    f"{path.name}: no valid Request/Response JSON found — "
                    "the dump is not shown"
                )
                continue
            missing = [k for k in REQUIRED_EMAIL_FIELDS if k not in row]
            if missing:
                failures.append(
                    f"{path.name}: parsed row is missing {', '.join(missing)} — "
                    "the detail view needs these fields"
                )
                continue
            parsed += 1
        except Exception as exc:
            failures.append(f"{path.name}: {_reason(exc)} — file could not be read")
    passed = not failures and parsed > 0
    return CheckResult(
        "Email dump parsing", passed,
        f"{parsed}/{len(files)} .txt dumps parsed", failures,
    )


def check_email_csvs(data_dir, label_csv=None):
    """Every root email CSV (except the label mapping) parses into rows."""
    root = Path(data_dir)
    label_path = Path(label_csv).resolve() if label_csv else None
    files = sorted(
        p for p in root.glob("*.csv")
        if p.is_file() and (label_path is None or p.resolve() != label_path)
    ) if root.is_dir() else []
    label_map = load_label_names(label_csv) if label_csv else {}
    failures = []
    rows = 0
    for path in files:
        try:
            csv_rows = parse_email_csv(path, label_map)
            if not csv_rows:
                failures.append(
                    f"{path.name}: no recognizable email columns found — "
                    "the file is skipped"
                )
                continue
            rows += len(csv_rows)
        except Exception as exc:
            failures.append(f"{path.name}: {_reason(exc)} — file could not be read")
    passed = not failures and rows > 0
    return CheckResult(
        "Email CSV parsing", passed,
        f"{rows} rows from {len(files)} CSV files", failures,
    )


def check_site_emails(site_dir):
    """Every site_emails .txt file yields at least one job with all six fields."""
    root = Path(site_dir)
    files = sorted(p for p in root.rglob("*.txt") if p.is_file()) if root.is_dir() else []
    failures = []
    jobs = 0
    for path in files:
        label = _label(path, root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_job_emails(text)
            if not parsed:
                failures.append(f"{label}: no jobs extracted — file cannot be shown")
                continue
            for index, job in enumerate(parsed, start=1):
                missing = [key for key in _FIELDS if key not in job]
                if missing:
                    failures.append(
                        f"{label} job {index}: missing field(s) {', '.join(missing)}"
                    )
                    continue
                blank = [
                    key for key in _FIELDS
                    if job[key] is None or not str(job[key]).strip()
                ]
                if blank:
                    failures.append(
                        f"{label} job {index}: blank field(s) {', '.join(blank)}"
                    )
                    continue
                jobs += 1
        except Exception as exc:
            failures.append(f"{label}: {_reason(exc)} — file could not be parsed")
    passed = not failures and jobs > 0
    return CheckResult(
        "Site email parsing", passed,
        f"{jobs} jobs from {len(files)} files", failures,
    )


def check_messages_table(jobs_df):
    """The Development Messages table has exactly the six requested columns."""
    actual = list(jobs_df.columns) if jobs_df is not None else []
    failures = []
    if actual != list(MESSAGE_COLUMNS):
        failures.append(
            f"expected columns {list(MESSAGE_COLUMNS)}, got {actual}"
        )
    rows = len(jobs_df) if jobs_df is not None else 0
    if not rows:
        failures.append("Messages table has no rows — no site email could be shown")
    return CheckResult(
        "Messages table", not failures,
        f"{rows} rows, {len(actual)} columns", failures,
    )


def check_all_unknown_jobs(jobs_df):
    """Report jobs whose Job Title, Company, and Salary are all Unknown, with Site."""
    if jobs_df is None or jobs_df.empty:
        return CheckResult("All-unknown job fields", True, "No jobs to check")
    needed = ("job_title", "company", "salary", "site")
    missing = [col for col in needed if col not in jobs_df.columns]
    if missing:
        return CheckResult(
            "All-unknown job fields", False,
            f"Missing column(s): {', '.join(missing)}",
            [f"jobs DataFrame is missing {', '.join(missing)}"],
        )

    def unknown(col):
        return (
            jobs_df[col].fillna("").astype(str).str.strip().str.lower().eq("unknown")
        )

    matches = jobs_df[unknown("job_title") & unknown("company") & unknown("salary")]
    failures = []
    for _, row in matches.iterrows():
        site = str(row.get("site") or "").strip() or "Unknown"
        subject = str(row.get("subject_from_email") or "").strip() or "Unknown"
        failures.append(f"Site: {site} — Subject: {subject}")
    return CheckResult(
        "All-unknown job fields", not failures,
        f"{len(matches)} job(s) with Job Title, Company, and Salary all Unknown",
        failures,
    )


def run_all_checks(data_dir, label_csv=None, site_dir=None, jobs_df=None):
    """Run every check and return an ordered list of CheckResult objects."""
    results = [
        check_data_directory(data_dir),
        check_site_directory(site_dir),
        check_email_dumps(data_dir, label_csv),
        check_email_csvs(data_dir, label_csv),
        check_site_emails(site_dir),
    ]
    if jobs_df is None or jobs_df.empty:
        jobs_df = load_jobs(site_dir) if site_dir else jobs_df
    if jobs_df is not None:
        results.append(check_messages_table(messages_table(jobs_df)))
        results.append(check_all_unknown_jobs(jobs_df))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default="data", help="Root email data folder")
    parser.add_argument(
        "--site-dir", default="data/site_emails", help="Site alert email folder"
    )
    parser.add_argument("--label-csv", default="data/label naming.csv")
    parser.add_argument("--json", action="store_true", help="Print JSON results")
    args = parser.parse_args(argv)

    results = run_all_checks(args.data_dir, args.label_csv, args.site_dir)
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2))
    else:
        for result in results:
            mark = "PASS" if result.passed else "FAIL"
            print(f"[{mark}] {result.name}: {result.summary}")
            for failure in result.failures:
                print(f"         - {failure}")
    return results


if __name__ == "__main__":
    main()
