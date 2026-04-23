"""Send weekly Supabase analytics report by email.

Requires Brevo configuration:
BREVO_API_KEY, MAIL_FROM, WEEKLY_REPORT_TO, optional MAIL_FROM_NAME.
"""

from __future__ import annotations

import sys
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from io import BytesIO

from ops_common import csv_bytes, fetch_supabase_rows, last_n_days, send_ops_email, supabase_ready


def main() -> int:
    if not supabase_ready():
        print("Supabase analytics is not configured.")
        return 2

    page_rows_all = fetch_supabase_rows(
        "page_views",
        "created_at,visitor_id,path,page_type,item_id,title,user_name,user_affiliation,user_email",
    )
    open_rows_all = fetch_supabase_rows(
        "resource_opens",
        "created_at,resource_id,open_type,target_url,user_name,user_affiliation,user_email",
    )
    page_rows = last_n_days(page_rows_all, 7)
    open_rows = last_n_days(open_rows_all, 7)

    unique_visitors = {row.get("visitor_id") for row in page_rows if row.get("visitor_id")}
    article_views = [row for row in page_rows if row.get("page_type") == "article_detail"]
    top_pages = Counter(row.get("path") or "" for row in page_rows).most_common(8)
    top_articles = Counter(row.get("item_id") or row.get("title") or "" for row in article_views).most_common(8)
    top_opens = Counter(row.get("resource_id") or "" for row in open_rows).most_common(8)
    conversion = (len(open_rows) / len(article_views) * 100.0) if article_views else 0.0

    end = datetime.utcnow()
    start = end - timedelta(days=7)
    lines = [
        "OPTICom Lab weekly website analytics",
        f"Period (UTC): {start:%Y-%m-%d} to {end:%Y-%m-%d}",
        "",
        f"Total page views: {len(page_rows)}",
        f"Unique visitors: {len(unique_visitors)}",
        f"Publication detail views: {len(article_views)}",
        f"Resource opens: {len(open_rows)}",
        f"View-to-open conversion: {conversion:.1f}%",
        "",
        "Top pages:",
        *[f"- {path or '(unknown)'}: {count}" for path, count in top_pages],
        "",
        "Top publications by views:",
        *[f"- {item or '(unknown)'}: {count}" for item, count in top_articles],
        "",
        "Top resource opens:",
        *[f"- {item or '(unknown)'}: {count}" for item, count in top_opens],
    ]
    report = "\n".join(lines)

    page_csv = csv_bytes(
        ["created_at", "visitor_id", "path", "page_type", "item_id", "title", "user_name", "user_affiliation", "user_email"],
        page_rows,
    )
    opens_csv = csv_bytes(
        ["created_at", "resource_id", "open_type", "target_url", "user_name", "user_affiliation", "user_email"],
        open_rows,
    )
    bundle = BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("page_views_weekly.csv", page_csv)
        archive.writestr("resource_opens_weekly.csv", opens_csv)
        archive.writestr("weekly_summary.txt", report.encode("utf-8"))

    ok, details = send_ops_email(
        "[OPTICom] Weekly website analytics report",
        report,
        attachments=[{"name": "weekly_analytics_bundle.zip", "content": bundle.getvalue()}],
    )
    if not ok:
        print(f"Email send failed: {details}")
        return 2
    print("Weekly analytics email sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
