"""
Daily Email Review Dashboard
Scans a data directory for Gmail API message-dump files (Request/Response
text pairs like data/1a0a5a18017c3e70.txt), parses them all, and lets you
review them: headline numbers, volume over time, top senders/labels, a
searchable message table, and a full-body drill-down per email.

Point it at your data with the DATA_DIR env var (default /data in Docker).
"""
import html
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

import data_checks
import parse_job_emails as job_parser
from parse_emails import load_emails

DATA_DIR = os.environ.get("DATA_DIR", "/data")
LABEL_CSV = os.environ.get("LABEL_CSV", os.path.join(DATA_DIR, "label naming.csv"))
SITE_EMAILS_DIR = os.environ.get("SITE_EMAILS_DIR", os.path.join(DATA_DIR, "site_emails"))
NEW_EMAIL_SOURCE_DIR = os.environ.get(
    "NEW_EMAIL_SOURCE_DIR", os.path.join(DATA_DIR, "new_email_source")
)

st.set_page_config(page_title="Daily Email Review", layout="wide")


@st.cache_data(ttl=60, show_spinner="Parsing email dumps...")
def load_data(data_dir: str, label_csv: str):
    rows, errors = load_emails(data_dir, label_csv=label_csv)
    if rows is None:
        return None, errors
    return pd.DataFrame(rows), errors


@st.cache_data(ttl=60, show_spinner="Parsing site job emails...")
def load_job_listings(site_emails_dir: str, extra_dir: str = None):
    """Parse site email folders plus the structured export into one DataFrame."""
    rows, errors = [], []
    roots = []
    primary = Path(site_emails_dir)
    if primary.exists():
        roots.append(primary)
    else:
        errors.append(f"Site email directory not found: {site_emails_dir}")
    if extra_dir and Path(extra_dir).is_dir():
        roots.append(Path(extra_dir))

    for root in roots:
        for path in sorted(root.rglob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                jobs = job_parser.parse_job_emails(text)
                for job in jobs:
                    row = dict(job)
                    # Structured exports carry per-email Site from their Category.
                    row["site"] = job.get("site") or path.parent.name
                    row["source_file"] = path.name
                    rows.append(row)
            except Exception as exc:  # keep one bad file from breaking the whole view
                errors.append(f"{path.name}: {exc}")

    if not rows:
        return None, errors
    return pd.DataFrame(rows), errors


def _render_jobs_table(jobs_df):
    """Render the job table used by Messages (filters live in the sidebar)."""
    table = data_checks.messages_table(jobs_df)
    st.caption(f"{len(table)} job(s)")
    if table.empty:
        st.info(
            "No jobs to show — records with Job Title, Company, and Salary all "
            "Unknown are hidden. See Tests/Failures for details."
        )
    else:
        st.dataframe(table, use_container_width=True, hide_index=True)


def _selected_rows(event):
    """Pull selected row indices from st.dataframe's event, any API shape."""
    if event is None:
        return []
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection") or {}
    if selection is None:
        return []
    rows = getattr(selection, "rows", None)
    if rows is None and isinstance(selection, dict):
        rows = selection.get("rows") or []
    return list(rows or [])


_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_SEPARATOR_RE = re.compile(r"^[=\-_*]{3,}\s*$")


def _markup_line(line):
    """Escape a line and turn URLs into clickable links (long ones truncated)."""
    out = []
    last = 0
    for m in _URL_RE.finditer(line):
        out.append(html.escape(line[last : m.start()]))
        url = m.group(0)
        display = url if len(url) <= 88 else url[:85] + "\u2026"
        out.append(
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(display)}</a>'
        )
        last = m.end()
    out.append(html.escape(line[last:]))
    return "".join(out)


def _markup_body(body):
    """Render a plaintext email body as safe HTML: clickable links, hr rules."""
    if not body:
        return "<em>No body text.</em>"
    parts = []
    for line in str(body).replace("\r\n", "\n").split("\n"):
        if _SEPARATOR_RE.match(line):
            parts.append("<hr>")
        else:
            parts.append(_markup_line(line) or "&nbsp;")
    return "<br>\n".join(parts)


def _email_label(row):
    when = row["date"]
    stamp = f"{when:%Y-%m-%d %H:%M}" if pd.notna(when) else "?"
    return f"{stamp} · {row['sender']} · {row['subject']}"


def _nav_email(delta: int):
    """Move Email detail selection; runs as an on_click callback before rerun."""
    st.session_state["detail_idx"] = st.session_state.get("detail_idx", 0) + delta


def _labels_top(fdf):
    return (
        fdf["labels"]
        .fillna("")
        .str.split(",")
        .explode()
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .value_counts()
        .head(10)
    )


_SYSTEM_LABELS = {
    "INBOX",
    "UNREAD",
    "IMPORTANT",
    "STARRED",
    "SENT",
    "DRAFT",
    "SPAM",
    "TRASH",
    "CATEGORY_PERSONAL",
    "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
}


def _gmail_labels(labels):
    """Resolved Gmail labels for display, minus Gmail's system labels."""
    parts = [lab.strip() for lab in str(labels or "").split(",") if lab.strip()]
    kept = [lab for lab in parts if lab.upper() not in _SYSTEM_LABELS]
    return ", ".join(kept) or "—"


def render_production_dashboard():
    """The existing dashboard, kept unchanged as the Production view."""
    st.title("\U0001F4E7 Daily Email Review")
    st.caption(f"Data directory: `{DATA_DIR}`")

    col_a, _ = st.columns([1, 6])
    with col_a:
        if st.button("\U0001F504 Refresh data"):
            load_data.clear()
            st.rerun()

    df, errors = load_data(DATA_DIR, LABEL_CSV)
    if df is None or df.empty:
        st.error("\n".join(errors[:20]))
        st.info(
            "Drop one *.txt dump file per email (like "
            "`data/1a0a5a18017c3e70.txt`) into the data folder and hit Refresh."
        )
        st.stop()

    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    valid_dates = df["date"].dropna()

    # --- Sidebar filters ---
    st.sidebar.header("Filters")
    if valid_dates.empty:
        start = end = None
        st.sidebar.warning("No valid dates in the data.")
    else:
        min_date, max_date = valid_dates.min().date(), valid_dates.max().date()
        start = st.sidebar.date_input("From", min_date, min_value=min_date, max_value=max_date)
        end = st.sidebar.date_input("To", max_date, min_value=min_date, max_value=max_date)

    search = st.sidebar.text_input("Search", placeholder="subject / sender / body")
    senders = sorted(df["sender"].dropna().unique().tolist())
    sel_senders = st.sidebar.multiselect("Sender", senders, default=senders)

    label_names = sorted(
        {
            lab.strip()
            for s in df["labels"].fillna("")
            for lab in s.split(",")
            if lab.strip()
            and lab.strip() not in ("IMPORTANT", "UNREAD")
            and not lab.strip().startswith("Label_")
        }
    )
    sel_labels = st.sidebar.multiselect(
        "Label", label_names, help="Show emails that carry any of the selected label names."
    )
    important_only = st.sidebar.checkbox(
        "Important only", value=False, help="Show emails labeled IMPORTANT."
    )
    unread_only = st.sidebar.checkbox("Unread only", value=False)

    mask = pd.Series(True, index=df.index)
    if start and end:
        mask &= df["date"].dt.date.between(start, end)
    if sel_senders:
        mask &= df["sender"].isin(sel_senders)
    if sel_labels:
        pattern = "|".join(r"\b" + re.escape(lab) + r"\b" for lab in sel_labels)
        mask &= df["labels"].fillna("").str.contains(pattern, regex=True, na=False)
    if important_only:
        mask &= df["important"]
    if unread_only:
        mask &= df["unread"]
    if search:
        q = search.lower()
        mask &= (
            df["subject"].str.lower().str.contains(q, na=False)
            | df["sender"].str.lower().str.contains(q, na=False)
            | df["snippet"].str.lower().str.contains(q, na=False)
            | df["body"].str.lower().str.contains(q, na=False)
        )

    fdf = df[mask].sort_values("date", ascending=False).reset_index(drop=True)

    st.subheader("Messages")
    if not len(fdf):
        st.info("No messages match the current filters.")
        return

    table = fdf[["date", "sender", "subject", "snippet", "unread"]].copy()
    table.insert(1, "label", fdf["labels"].map(_gmail_labels))
    table["snippet"] = table["snippet"].str.slice(0, 120)
    event = st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
    )

    selected_rows = _selected_rows(event)

    # Which email is shown in "Email detail" lives in session state so the
    # table click, the dropdown, and Prev/Next all stay in sync. Prev/Next
    # use on_click callbacks (run before the script body), so updating the
    # selectbox's own key here is always legal.
    if "detail_idx" not in st.session_state:
        st.session_state["detail_idx"] = 0
    if selected_rows:
        st.session_state["detail_idx"] = selected_rows[0]
    if not (0 <= st.session_state["detail_idx"] < len(fdf)):
        st.session_state["detail_idx"] = 0

    st.subheader("Email detail")
    nav_prev, nav_pick, nav_next = st.columns([1, 4, 1])
    with nav_prev:
        st.button(
            "◀ Prev",
            key="prev_email",
            use_container_width=True,
            on_click=_nav_email,
            args=(-1,),
            disabled=st.session_state["detail_idx"] <= 0,
        )
    with nav_pick:
        st.selectbox(
            "Select email",
            range(len(fdf)),
            format_func=lambda i: _email_label(fdf.iloc[i]),
            index=st.session_state["detail_idx"],
            key="detail_idx",
        )
    with nav_next:
        st.button(
            "Next ▶",
            key="next_email",
            use_container_width=True,
            on_click=_nav_email,
            args=(1,),
            disabled=st.session_state["detail_idx"] >= len(fdf) - 1,
        )

    # table and fdf share the same order (date desc), so the current index is
    # the email the user selected — in the table, the dropdown, or via nav.
    row = fdf.iloc[st.session_state["detail_idx"]]
    meta = st.columns(4)
    meta[0].markdown(f"**From**\n\n{row['sender']}")
    meta[1].markdown(f"**Date**\n\n{row['date']}")
    meta[2].markdown(f"**Labels**\n\n{row['labels'] or '—'}")
    meta[3].markdown(f"**File**\n\n`{row['file']}`")

    st.markdown(f"**{row['subject']}**\n\n{row['snippet']}")
    st.markdown("**Body**")
    st.markdown(
        "<style>.email-body{max-height:440px;overflow-y:auto;line-height:1.6;"
        "font-size:0.95rem;}</style>"
        + f"<div class='email-body'>{_markup_body(row['body'])}</div>",
        unsafe_allow_html=True,
    )
    st.download_button(
        "Download body as .txt",
        data=row["body"],
        file_name=f"{row['file']}.body.txt",
        mime="text/plain",
    )

    st.divider()
    st.subheader("Overview")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Emails", len(fdf))
    c2.metric("Unread", int(fdf["unread"].sum()))
    c3.metric("Unique senders", fdf["sender"].nunique())
    avg = fdf["size_estimate"].dropna()
    c4.metric("Avg size", f"{avg.mean() / 1024:.0f} KB" if len(avg) else "—")

    st.subheader("Volume over time")
    daily = fdf.groupby(fdf["date"].dt.normalize()).size()
    st.bar_chart(daily)

    left, right = st.columns(2)
    with left:
        st.subheader("Top senders")
        st.bar_chart(fdf["sender"].value_counts().head(15))
    with right:
        st.subheader("Top labels")
        top_labels = _labels_top(fdf)
        if len(top_labels):
            st.bar_chart(top_labels)
        else:
            st.caption("No label data.")

    if errors:
        st.divider()
        with st.expander(f"Issues ({len(errors)})"):
            for e in errors:
                st.caption(e)


def _render_filtered_results(fdf, errors, show_overview=False, jobs_df=None):
    """Render the shared results views; Development passes parsed jobs for Messages."""
    if "detail_idx" not in st.session_state:
        st.session_state["detail_idx"] = 0

    if not show_overview:
        st.subheader("Messages")
        if jobs_df is not None:
            # Development: Messages shows the parsed site job emails.
            if jobs_df.empty:
                st.info(
                    "No site job emails found. Add `.txt` email files under "
                    "`data/site_emails/<site>/`."
                )
            else:
                _render_jobs_table(jobs_df)
        elif not len(fdf):
            st.info("No messages match the current filters.")
            return
        else:
            table = fdf[["date", "sender", "subject", "snippet", "unread"]].copy()
            table.insert(1, "label", fdf["labels"].map(_gmail_labels))
            table["snippet"] = table["snippet"].str.slice(0, 120)
            event = st.dataframe(
                table,
                use_container_width=True,
                hide_index=True,
                selection_mode="single-row",
                on_select="rerun",
            )

            # Which email is shown in "Email detail" lives in session state so the
            # table click, the dropdown, and Prev/Next all stay in sync. Prev/Next
            # use on_click callbacks (run before the script body), so updating the
            # selectbox's own key here is always legal.
            selected_rows = _selected_rows(event)
            if selected_rows:
                st.session_state["detail_idx"] = selected_rows[0]

        if not len(fdf):
            st.info("No emails available for Email detail.")
        else:
            if not (0 <= st.session_state["detail_idx"] < len(fdf)):
                st.session_state["detail_idx"] = 0

            st.subheader("Email detail")
            nav_prev, nav_pick, nav_next = st.columns([1, 4, 1])
            with nav_prev:
                st.button(
                    "◀ Prev",
                    key="prev_email",
                    use_container_width=True,
                    on_click=_nav_email,
                    args=(-1,),
                    disabled=st.session_state["detail_idx"] <= 0,
                )
            with nav_pick:
                st.selectbox(
                    "Select email",
                    range(len(fdf)),
                    format_func=lambda i: _email_label(fdf.iloc[i]),
                    index=st.session_state["detail_idx"],
                    key="detail_idx",
                )
            with nav_next:
                st.button(
                    "Next ▶",
                    key="next_email",
                    use_container_width=True,
                    on_click=_nav_email,
                    args=(1,),
                    disabled=st.session_state["detail_idx"] >= len(fdf) - 1,
                )

            # table and fdf share the same order (date desc), so the current index
            # is the email the user selected — in the table, the dropdown, or nav.
            row = fdf.iloc[st.session_state["detail_idx"]]
            meta = st.columns(4)
            meta[0].markdown(f"**From**\n\n{row['sender']}")
            meta[1].markdown(f"**Date**\n\n{row['date']}")
            meta[2].markdown(f"**Labels**\n\n{row['labels'] or '—'}")
            meta[3].markdown(f"**File**\n\n`{row['file']}`")

            st.markdown(f"**{row['subject']}**\n\n{row['snippet']}")
            st.markdown("**Body**")
            st.markdown(
                "<style>.email-body{max-height:440px;overflow-y:auto;line-height:1.6;"
                "font-size:0.95rem;}</style>"
                + f"<div class='email-body'>{_markup_body(row['body'])}</div>",
                unsafe_allow_html=True,
            )
            st.download_button(
                "Download body as .txt",
                data=row["body"],
                file_name=f"{row['file']}.body.txt",
                mime="text/plain",
            )

    else:
        st.divider()
        st.subheader("Overview")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Emails", len(fdf))
        c2.metric("Unread", int(fdf["unread"].sum()))
        c3.metric("Unique senders", fdf["sender"].nunique())
        avg = fdf["size_estimate"].dropna()
        c4.metric("Avg size", f"{avg.mean() / 1024:.0f} KB" if len(avg) else "—")

        if not len(fdf):
            st.caption("No email data available for charts.")
        else:
            st.subheader("Volume over time")
            daily = fdf.groupby(fdf["date"].dt.normalize()).size()
            st.bar_chart(daily)

            left, right = st.columns(2)
            with left:
                st.subheader("Top senders")
                st.bar_chart(fdf["sender"].value_counts().head(15))
            with right:
                st.subheader("Top labels")
                top_labels = _labels_top(fdf)
                if len(top_labels):
                    st.bar_chart(top_labels)
                else:
                    st.caption("No label data.")

    if errors:
        st.divider()
        with st.expander(f"Issues ({len(errors)})"):
            for e in errors:
                st.caption(e)


def render_development_dashboard():
    """Development view: labels first, multi-select, and no Sender filter."""
    st.title("\U0001F4E7 Daily Email Review")
    st.caption(f"Data directory: `{DATA_DIR}` · Development mode")

    if "show_overview" not in st.session_state:
        st.session_state["show_overview"] = False

    col_refresh, col_overview, col_jobs, _ = st.columns([1, 1, 1, 4])
    with col_refresh:
        if st.button("\U0001F504 Refresh data"):
            load_data.clear()
            st.rerun()
    with col_overview:
        if st.button("Overview", help="Show or hide the Overview charts."):
            st.session_state["show_overview"] = not st.session_state["show_overview"]
    with col_jobs:
        if st.button("Refresh jobs", key="refresh_jobs"):
            load_job_listings.clear()
            st.rerun()

    # Messages is populated only from site_emails/ (and its sub-folders).
    # The email-dump data is loaded separately, just for Email detail / Overview.
    jobs_df, job_errors = load_job_listings(SITE_EMAILS_DIR, NEW_EMAIL_SOURCE_DIR)
    if jobs_df is None:
        jobs_df = pd.DataFrame()  # keeps Development in "jobs" mode with a clear message
    df, email_errors = load_data(DATA_DIR, LABEL_CSV)
    errors = list(email_errors or []) + list(job_errors or [])
    if df is None:
        df = pd.DataFrame(
            columns=[
                "file", "id", "thread_id", "subject", "sender", "sender_domain",
                "to_recipients", "date", "snippet", "body", "labels", "important",
                "unread", "size_estimate", "history_id",
            ]
        )
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    valid_dates = df["date"].dropna()

    # --- Sidebar filters (Development: Site + Search Jobs) ---
    st.sidebar.header("Filters")
    sites = (
        sorted(jobs_df["site"].dropna().astype(str).unique().tolist())
        if "site" in jobs_df.columns
        else []
    )
    sel_sites = st.sidebar.multiselect(
        "Site",
        sites,
        default=sites,
        help="Show jobs parsed from the selected site folders.",
    )
    job_search = st.sidebar.text_input(
        "Search Jobs", placeholder="job title / company / subject"
    )
    if valid_dates.empty:
        start = end = None
        st.sidebar.warning("No valid dates in the data.")
    else:
        min_date, max_date = valid_dates.min().date(), valid_dates.max().date()
        start = st.sidebar.date_input("From", min_date, min_value=min_date, max_value=max_date)
        end = st.sidebar.date_input("To", max_date, min_value=min_date, max_value=max_date)

    important_only = st.sidebar.checkbox(
        "Important only", value=False, help="Show emails labeled IMPORTANT."
    )
    unread_only = st.sidebar.checkbox("Unread only", value=False)

    # Site / Search Jobs filter the Messages table (from site_emails only).
    job_mask = pd.Series(True, index=jobs_df.index)
    if not jobs_df.empty:
        if sel_sites:
            job_mask &= jobs_df["site"].isin(sel_sites)
        if job_search:
            q = job_search.lower()
            job_mask &= (
                jobs_df["job_title"].fillna("").astype(str).str.lower().str.contains(q, na=False)
                | jobs_df["company"].fillna("").astype(str).str.lower().str.contains(q, na=False)
                | jobs_df["subject_from_email"].fillna("").astype(str).str.lower().str.contains(q, na=False)
            )
    jobs_df = jobs_df[job_mask].reset_index(drop=True)

    mask = pd.Series(True, index=df.index)
    if start and end:
        mask &= df["date"].dt.date.between(start, end)
    if important_only:
        mask &= df["important"]
    if unread_only:
        mask &= df["unread"]

    fdf = df[mask].sort_values("date", ascending=False).reset_index(drop=True)
    _render_filtered_results(
        fdf,
        errors,
        show_overview=st.session_state["show_overview"],
        jobs_df=jobs_df,
    )


def _render_test_report(results):
    """Show each data check and the reason any file could not be displayed."""
    failed = [r for r in results if not r.passed]
    problems = sum(len(r.failures) for r in results)
    if failed:
        st.error(
            f"{len(results) - len(failed)}/{len(results)} checks passed — "
            f"{problems} file/field issue(s)"
        )
    else:
        st.success(f"All {len(results)} checks passed")

    for result in results:
        icon = "✅" if result.passed else "❌"
        with st.expander(
            f"{icon} {result.name} — {result.summary}", expanded=not result.passed
        ):
            if result.failures:
                for failure in result.failures:
                    st.caption(f"• {failure}")
            else:
                st.caption("No issues found.")
    st.caption(
        "Checks run against the current files. After adding files, use Refresh "
        "data / Refresh jobs and reopen this panel to re-run them."
    )


def main():
    """Route to Production (frozen current behavior) or Development."""
    mode = st.sidebar.radio(
        "Mode",
        ("Production", "Development"),
        horizontal=True,
        key="app_mode",
        help="Production is the current dashboard. Development is for upcoming changes.",
    )
    st.sidebar.divider()

    if "show_tests" not in st.session_state:
        st.session_state["show_tests"] = False
    if st.sidebar.button(
        "Tests/Failures",
        key="tests_toggle",
        use_container_width=True,
        help="Run data checks and show files that cannot be displayed, with reasons.",
    ):
        st.session_state["show_tests"] = not st.session_state["show_tests"]

    if st.session_state["show_tests"]:
        st.subheader("Tests / Failures")
        with st.spinner("Running data checks..."):
            jobs_df, _ = load_job_listings(SITE_EMAILS_DIR, NEW_EMAIL_SOURCE_DIR)
            results = data_checks.run_all_checks(
                DATA_DIR,
                LABEL_CSV,
                SITE_EMAILS_DIR,
                jobs_df=jobs_df,
                extra_dir=NEW_EMAIL_SOURCE_DIR,
            )
        _render_test_report(results)
        # Tests/Failures is an exclusive view: hide the dashboard behind it.
        return

    if mode == "Production":
        render_production_dashboard()
    else:
        render_development_dashboard()


if __name__ == "__main__":
    main()