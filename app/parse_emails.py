"""
parse_emails.py
Parse Gmail API message-dump files like the ones in data/.

Each dump is a plain-text file with this shape:

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
      "plaintextBody": "Your job alert for Data Engineer ...",
      "labelIds": ["UNREAD", "Label_7469349110985185565"],
      ...
    }

The dashboard scans a data directory, parses every *.txt into one row, and
builds a single DataFrame.  Malformed files are skipped and reported so a bad
file never takes the whole dashboard down.

CSV exports dropped next to the dump files are supported too; see
parse_email_csv().
"""
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _iter_json_blocks(text: str):
    """Yield top-level balanced {...} JSON blocks, ignoring braces inside strings."""
    depth = 0
    start = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
            if depth == 0 and start is not None:
                yield text[start : i + 1]
                start = None


def _parse_date(value):
    """Accept an RFC3339 stamp (2026-09-15T15:13:39Z) or epoch millis -> aware datetime."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    if isinstance(value, str) and value.isdigit():
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _sender_domain(sender):
    if not sender:
        return None
    return str(sender).rsplit("@", 1)[-1].lower()


def load_label_names(csv_path):
    """Load a label_id -> label_name mapping from a CSV. Returns {} if absent.

    Tolerates drifted formatting: leading-space header cells and values that
    carry literal double quotes (e.g. `label_id, label_name` + `Label_x, "Name"`).
    """
    mapping = {}
    path = Path(csv_path)
    if not path.exists():
        return mapping
    try:
        import csv as _csv

        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(_csv.reader(fh))
    except OSError:
        return mapping
    if not rows:
        return mapping
    header = [h.strip().lower() for h in rows[0]]
    if "label_id" not in header or "label_name" not in header:
        return mapping
    id_idx = header.index("label_id")
    name_idx = header.index("label_name")
    for r in rows[1:]:
        if len(r) <= max(id_idx, name_idx):
            continue
        label_id = r[id_idx].strip()
        label_name = r[name_idx].strip().strip('"').strip()
        if label_id:
            mapping[label_id] = label_name or label_id
    return mapping


def _resolve_labels(label_ids, label_map):
    """Map label IDs to friendly names; keep the raw value when unknown."""
    return [
        label_map.get(str(l), str(l)) for l in label_ids
    ]


def parse_email_file(path, label_map=None):
    """Parse one dump file into a dict row (or None if it can't be parsed)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    request = None
    response = None
    for block in _iter_json_blocks(text):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if "messageId" in obj and "messageFormat" in obj:
            request = obj
        elif response is None and any(k in obj for k in ("id", "plaintextBody", "subject")):
            response = obj

    if response is None:
        return None

    recipients = response.get("toRecipients") or []
    label_ids = response.get("labelIds") or []
    label_map = label_map or {}
    sender = response.get("sender", "")
    internal = response.get("internalDate")
    date_raw = response.get("date") or internal

    labels = _resolve_labels(label_ids, label_map)

    return {
        "file": Path(path).name,
        "id": response.get("id"),
        "thread_id": response.get("threadId"),
        "subject": response.get("subject") or "(no subject)",
        "sender": sender or "(unknown)",
        "sender_domain": _sender_domain(sender),
        "to_recipients": ", ".join(str(r) for r in recipients),
        "date": _parse_date(date_raw),
        "snippet": (response.get("snippet") or "").strip(),
        "body": response.get("plaintextBody") or "",
        "labels": ", ".join(labels),
        "important": "IMPORTANT" in label_ids,
        "unread": "UNREAD" in label_ids,
        "size_estimate": response.get("sizeEstimate"),
        "history_id": response.get("historyId"),
    }


def _csv_value(row_dict, header_map, *names):
    """Fetch value from a csv row by any of several tolerated column names."""
    for name in names:
        key = header_map.get(_norm_header(name))
        if key is not None:
            value = row_dict.get(key)
            return value if value is not None and str(value).strip() != "" else None
    return None


def _norm_header(name: str) -> str:
    return re.sub(r"[\s_]+", "", str(name).lower())


def _parse_label_list(value):
    """Turn a labels cell (comma/pipe list or JSON array) into label tokens."""
    if value is None:
        return []
    v = str(value).strip()
    if not v:
        return []
    if v[0] in "[{":
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return [str(i).strip() for i in parsed if str(i).strip()]
        except json.JSONDecodeError:
            pass
    return [i.strip() for i in re.split(r"[,|]", v) if i.strip()]


def parse_email_csv(path, label_map=None):
    """Parse an email CSV export into a list of row dicts.

    Recognized columns (case/space/underscore tolerant), mirroring the dump
    files: id/messageId, subject, sender/from, toRecipients/to, date,
    internalDate, snippet, plaintextBody/body, labelIds/labels,
    threadId, historyId, sizeEstimate, unread, important. Returns None when
    the file is not an email CSV (e.g. the label mapping file).
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            fieldnames = [f for f in reader.fieldnames or [] if f]
            if not fieldnames:
                return None
            header_map = {_norm_header(h): h for h in fieldnames}
            records = list(reader)
    except OSError:
        return None

    # label_id/label_name mapping files are not email data
    keys = {_norm_header(h) for h in fieldnames}
    if keys == {"labelid", "labelname"}:
        return None

    has_email_cols = any(_norm_header(h) in KEYS for h in fieldnames)
    if not has_email_cols:
        return None

    label_map = label_map or {}
    rows = []
    for r, record in enumerate(records, start=1):
        subject = _csv_value(record, header_map, "subject")
        sender = _csv_value(record, header_map, "sender", "from")
        snippet = _csv_value(record, header_map, "snippet") or ""
        body = _csv_value(record, header_map, "body", "plaintextBody", "body_text") or ""
        id_raw = _csv_value(record, header_map, "id", "messageId")

        date_raw = _csv_value(record, header_map, "date", "internalDate")
        time_raw = _csv_value(record, header_map, "time")
        if date_raw and time_raw and "T" not in str(date_raw):
            t = str(time_raw).strip()
            if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", t):
                if t.count(":") == 1:
                    t += ":00"
                date_raw = f"{date_raw}T{t}"

        label_ids = _parse_label_list(_csv_value(record, header_map, "labels", "labelIds"))
        category = _csv_value(record, header_map, "category")
        if category:
            label_ids.append(str(category).strip())
        to_recipients = _parse_label_list(
            _csv_value(record, header_map, "toRecipients", "to", "toRecipientsList")
        )

        unread = "UNREAD" in label_ids
        important = "IMPORTANT" in label_ids
        unread_col = _csv_value(record, header_map, "unread")
        if unread_col is not None:
            unread = str(unread_col).strip().lower() in ("1", "true", "yes", "y")

        rows.append(
            {
                "file": f"{Path(path).name}#{r}",
                "id": id_raw,
                "thread_id": _csv_value(record, header_map, "threadId"),
                "subject": subject or "(no subject)",
                "sender": sender or "(unknown)",
                "sender_domain": _sender_domain(sender),
                "to_recipients": ", ".join(to_recipients),
                "date": _parse_date(date_raw),
                "snippet": str(snippet).strip(),
                "body": body,
                "labels": ", ".join(_resolve_labels(label_ids, label_map)),
                "important": important,
                "unread": unread,
                "size_estimate": _csv_value(record, header_map, "sizeEstimate"),
                "history_id": _csv_value(record, header_map, "historyId"),
            }
        )
    return rows


KEYS = {
    _norm_header(h)
    for h in (
        "id", "messageId", "subject", "sender", "from", "toRecipients", "to",
        "date", "internalDate", "snippet", "plaintextBody", "body", "body_text",
        "labelIds", "labels", "threadId", "historyId", "sizeEstimate",
        "unread", "important", "toRecipientsList",
    )
}

def load_emails(data_dir, label_csv=None):
    """Parse every *.txt and *.csv directly inside data_dir -> (rows, errors).

    Both dump files and CSV email exports in the top level of data_dir are
    used; subdirectories (e.g. ``temp data``) and their contents are
    deliberately ignored. label_csv: path to a label_id/label_name CSV used
    to pretty-print label IDs. Falls back to the raw ID when a label is
    unknown.
    """
    rows = []
    errors = []
    directory = Path(data_dir)
    if not directory.exists():
        return None, [f"Data directory not found: {data_dir}"]

    label_map = load_label_names(label_csv) if label_csv else {}

    label_csv_path = Path(label_csv).resolve() if label_csv else None
    txt_files = sorted(
        p for p in directory.glob("*.txt") if p.parent == directory
    )
    csv_files = sorted(
        p
        for p in directory.glob("*.csv")
        if p.parent == directory and p.resolve() != label_csv_path
    )
    if not (txt_files or csv_files):
        return None, [f"No *.txt or *.csv email files found in {data_dir}"]

    for path in txt_files:
        row = parse_email_file(path, label_map)
        if row is None:
            errors.append(f"{path.name}: no valid Request/Response JSON found")
        else:
            rows.append(row)

    for path in csv_files:
        csv_rows = parse_email_csv(path, label_map)
        if not csv_rows:
            errors.append(f"{path.name}: no recognizable email columns found")
        else:
            rows.extend(csv_rows)

    if not rows:
        return None, errors or ["No files could be parsed"]

    return rows, errors