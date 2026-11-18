"""Extract job details from site email alert files.

Supports Indeed (single and multi-job), ZipRecruiter, David Careers, and
Glassdoor emails like the files under data/site_emails/. The input is a raw
RFC 822 email. Only Python's standard library is used.

Run directly:

    python app/parse_job_emails.py <file>
    python app/parse_job_emails.py <file> --json

Or import:

    from parse_job_emails import parse_job_emails
    jobs = parse_job_emails(email_text)
"""
import argparse
import json
import re
from email import policy
from email.parser import Parser
from pathlib import Path

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_JOB_URL_RE = re.compile(r"https://www\.indeed\.com/rc/clk/dl\b")
_SALARY_RE = re.compile(
    r"(?:(?:salary|compensation)\s*:\s*)?"
    r"((?:up\s+to\s+)?\$[\d,]+(?:\.\d+)?"
    r"(?:\s*-\s*\$?[\d,]+(?:\.\d+)?)?"
    r"(?:\s*(?:a\s+year|an\s+hour|per\s+year|per\s+hour|/\s*(?:month|year|hr)))?"
    r")",
    re.IGNORECASE,
)
_TITLE_COMPENSATION_RE = re.compile(r"\s*Compensation\s*:\s*\$[^$]*$", re.IGNORECASE)
_ZIP_SALARY_RE = re.compile(
    r"\$[\d,]+(?:\.\d+)?[KkMm]?"
    r"(?:\s*-\s*\$?[\d,]+(?:\.\d+)?[KkMm]?)?"
    r"\s*(?:/\s*(?:hr|yr|year|hour|month)|a\s+year|an\s+hour)",
    re.IGNORECASE,
)
_DAVID_JOB_RE = re.compile(
    r"(?P<title>.+?)\s+opportunity at\s+(?P<company>.+?)\s*$", re.IGNORECASE
)
_GLASSDOOR_JOB_RE = re.compile(
    r"(?P<title>.+?)\s+at\s+(?P<company>.+?)\s+and\s+\d+\s+more job", re.IGNORECASE
)
_GLASSDOOR_HIRING_RE = re.compile(r"(?P<company>.+?)\s+is hiring", re.IGNORECASE)
_SUBJECT_HAS_OPENING_RE = re.compile(
    r"(?P<company>.+?)\s+has a\s+(?P<title>.+?)\s+opening", re.IGNORECASE
)
_SUBJECT_MAY_HIRE_RE = re.compile(
    r"^[^,]+,\s*(?P<company>.+?)\s+may want to hire you", re.IGNORECASE
)

_UNKNOWN = "Unknown"
_UNKNOWN_SALARY = _UNKNOWN

_FIELDS = (
    "date_of_email",
    "job_title",
    "company",
    "salary",
    "url",
    "subject_from_email",
)


def _message(email_text):
    msg = Parser(policy=policy.default).parsestr(email_text)
    subject = re.sub(r"\s+", " ", str(msg.get("Subject", ""))).strip()
    date = re.sub(r"\s+", " ", str(msg.get("Date", ""))).strip()
    return msg, subject, date


def _plain_text(msg):
    """Return the decoded text/plain body, or an empty string."""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                return part.get_content()
            except (LookupError, UnicodeDecodeError):
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    return ""


def _lines(body):
    return [line.strip() for line in body.splitlines() if line.strip()]


def _split_subject(subject):
    """Indeed subjects look like 'BI Report Developer (100% Remote) @ ClearCaptions, LLC'."""
    if " @ " in subject:
        title, company = subject.split(" @ ", 1)
        return title.strip(), company.strip()
    return "", ""


def _find_salary(lines):
    for line in lines:
        match = _SALARY_RE.search(line)
        if match:
            return match.group(1).strip(" .;")
    return ""


def _salary_or_unknown(value):
    return value.strip() or _UNKNOWN_SALARY


def _unknown_if_blank(value):
    return str(value).strip() or _UNKNOWN


def _make_job(date, subject, title, company, salary, url):
    return {
        "date_of_email": date,
        "job_title": _unknown_if_blank(title),
        "company": _unknown_if_blank(company),
        "salary": _salary_or_unknown(salary),
        "url": _unknown_if_blank(url),
        "subject_from_email": subject,
    }


def _source_site(msg):
    header = f"{msg.get('From', '')} {msg.get('Category', '')}".lower()
    for site, token in (
        ("ziprecruiter", "ziprecruiter"),
        ("david", "davidcareers"),
        ("glassdoor", "glassdoor"),
        ("indeed", "indeed"),
    ):
        if token in header:
            return site
    return ""


def _find_url(lines, job_url_only=False):
    """Prefer a 'View job'/'rc/clk' link; fall back to the first URL in the block."""
    for line in lines:
        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(".,;)")
            if not job_url_only or _JOB_URL_RE.search(url):
                return url
    return ""


def _find_title_company_from_body(lines):
    """Fallback: use the lines just before the Salary line in the job card."""
    salary_index = None
    for index, line in enumerate(lines):
        if _SALARY_RE.search(line):
            salary_index = index
            break
    if salary_index is None or salary_index < 2:
        return "", ""

    # Typical block is: job title, company, location, Salary: ...
    company = lines[salary_index - 2]
    title = lines[salary_index - 3] if salary_index >= 3 else ""
    return title.strip(), company.strip()


def _job_blocks(body):
    """Return each job in a job-alert body as a list of non-empty lines."""
    blocks = []
    for block in body.split("\n\n"):
        lines = _lines(block)
        if len(lines) >= 2 and any(_JOB_URL_RE.search(line) for line in lines):
            blocks.append(lines)
    return blocks


def _parse_job_block(lines, date, subject):
    title_line = lines[0]
    title = _TITLE_COMPENSATION_RE.sub("", title_line).strip(" .;")
    company_line = lines[1]
    company = company_line.rsplit(" - ", 1)[0].strip() if " - " in company_line else company_line

    # Salary usually sits right after the company line; the title can also
    # carry "Compensation: ..." in Indeed alert emails.
    salary = _find_salary(lines[2:]) or _find_salary([title_line])

    return _make_job(
        date, subject, title, company, salary, _find_url(lines, job_url_only=True)
    )


def parse_ziprecruiter_email(email_text):
    """Return one detail dict per job in a ZipRecruiter daily alert."""
    msg, subject, date = _message(email_text)
    body = _plain_text(msg).replace("\\u2022", "•")
    jobs = []
    for block in body.split("\n\n"):
        lines = _lines(block)
        if len(lines) < 2:
            continue
        if lines[0].startswith(("Hi ", "Here are", "[Additional jobs")):
            continue
        if "•" not in lines[1] and not any(_ZIP_SALARY_RE.search(l) for l in lines[2:]):
            continue

        company = lines[1].split("•", 1)[0].strip()
        salary = ""
        for line in lines[2:]:
            match = _ZIP_SALARY_RE.search(line)
            if match:
                salary = match.group(0)
                break
        jobs.append(_make_job(date, subject, lines[0], company, salary, _find_url(lines)))
    return jobs


def parse_david_careers_email(email_text):
    """Return the named job from a David Careers alert (the source lists one in detail)."""
    msg, subject, date = _message(email_text)
    match = _DAVID_JOB_RE.search(subject) or _DAVID_JOB_RE.search(_plain_text(msg))
    title = match.group("title") if match else _UNKNOWN
    company = match.group("company") if match else _UNKNOWN
    return [_make_job(date, subject, title, company, "", "")]


def parse_glassdoor_email(email_text):
    """Return the named job(s) from a Glassdoor alert."""
    msg, subject, date = _message(email_text)
    body = _plain_text(msg)
    match = _GLASSDOOR_JOB_RE.search(subject) or _GLASSDOOR_JOB_RE.search(body)
    if match:
        title = match.group("title")
        company = match.group("company")
    else:
        title = _UNKNOWN
        hiring = _GLASSDOOR_HIRING_RE.search(body)
        company = hiring.group("company") if hiring else _UNKNOWN
    return [_make_job(date, subject, title, company, "", "")]


def parse_job_emails(email_text):
    """Auto-detect the source site and return a list of job detail dicts."""
    msg, _, _ = _message(email_text)
    site = _source_site(msg)
    if site == "ziprecruiter":
        jobs = parse_ziprecruiter_email(email_text)
    elif site == "david":
        jobs = parse_david_careers_email(email_text)
    elif site == "glassdoor":
        jobs = parse_glassdoor_email(email_text)
    else:
        jobs = parse_indeed_job_emails(email_text)
    return jobs or parse_subject_only_email(email_text)


def parse_subject_only_email(email_text):
    """Return one best-effort job row from an alert with no structured job blocks."""
    msg, subject, date = _message(email_text)
    title, company = _UNKNOWN, _UNKNOWN
    match = _SUBJECT_HAS_OPENING_RE.search(subject)
    if match:
        company = match.group("company").strip()
        title = match.group("title").strip()
    else:
        match = _SUBJECT_MAY_HIRE_RE.search(subject)
        if match:
            company = match.group("company").strip()
    return [_make_job(date, subject, title, company, "", "")]


def parse_indeed_job_email(email_text):
    """Return the six requested details from a single-job Indeed email."""
    msg, subject, date = _message(email_text)
    lines = _lines(_plain_text(msg))
    subject_title, subject_company = _split_subject(subject)
    body_title, body_company = _find_title_company_from_body(lines)

    return _make_job(
        date,
        subject,
        subject_title or body_title,
        subject_company or body_company,
        _find_salary(lines),
        _find_url(lines),
    )


def parse_indeed_job_alert_email(email_text):
    """Return one detail dict per job found in an Indeed job-alert email."""
    msg, subject, date = _message(email_text)
    return [_parse_job_block(lines, date, subject) for lines in _job_blocks(_plain_text(msg))]


def parse_indeed_job_emails(email_text):
    """Auto-detect the format and return a list of job detail dicts."""
    jobs = parse_indeed_job_alert_email(email_text)
    if jobs:
        return jobs
    return [parse_indeed_job_email(email_text)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="Path to a raw .txt email file")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of readable text")
    args = parser.parse_args(argv)

    email_text = args.path.read_text(encoding="utf-8", errors="replace")
    jobs = parse_job_emails(email_text)

    if args.json:
        print(json.dumps(jobs, indent=2, ensure_ascii=False))
    else:
        for index, job in enumerate(jobs, start=1):
            print(f"Job {index}")
            for field in _FIELDS:
                print(f"  {field}: {job[field]}")
            if index != len(jobs):
                print()
    return jobs


if __name__ == "__main__":
    main()
