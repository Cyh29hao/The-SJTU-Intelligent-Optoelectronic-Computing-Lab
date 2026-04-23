"""Shared helpers for OPTICom Lab operations scripts."""

from __future__ import annotations

import base64
import csv
import html
import io
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTENT_REL = "site_content"

load_dotenv(PROJECT_ROOT / ".env")


def run_git(args: list[str], timeout: int = 60) -> dict[str, str | bool]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        }
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}


def parse_status_lines(raw: str) -> list[dict[str, str]]:
    changes = []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        changes.append({"status": (line[:2] or "").strip(), "path": line[2:].strip()})
    return changes


def send_ops_email(subject: str, text: str, attachments: list[dict[str, bytes | str]] | None = None) -> tuple[bool, str]:
    """Send an operations email through Brevo API when configured.

    attachments: [{"name": "file.csv", "content": b"..."}]
    """
    api_key = (os.environ.get("BREVO_API_KEY") or "").strip()
    to_email = (os.environ.get("WEEKLY_REPORT_TO") or os.environ.get("OPS_ALERT_TO") or "").strip()
    from_email = (os.environ.get("MAIL_FROM") or "").strip()
    from_name = (os.environ.get("MAIL_FROM_NAME") or "OPTICom Lab Website").strip()

    if not api_key:
        return False, "BREVO_API_KEY is not configured"
    if not to_email:
        return False, "WEEKLY_REPORT_TO/OPS_ALERT_TO is not configured"
    if not from_email:
        return False, "MAIL_FROM is not configured"

    payload: dict[str, object] = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text,
        "htmlContent": f"<pre>{html.escape(text)}</pre>",
    }
    if attachments:
        payload["attachment"] = [
            {
                "name": str(item["name"]),
                "content": base64.b64encode(item["content"]).decode("ascii"),
            }
            for item in attachments
        ]

    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            return False, f"Brevo API failed: {response.status_code} {response.text[:300]}"
        return True, response.text[:300]
    except Exception as exc:
        return False, str(exc)


def supabase_ready() -> bool:
    return bool(
        (os.environ.get("SUPABASE_LOGS_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
        and (os.environ.get("SUPABASE_URL") or "").strip()
        and (os.environ.get("SUPABASE_SECRET_KEY") or "").strip()
    )


def supabase_headers() -> dict[str, str]:
    secret = (os.environ.get("SUPABASE_SECRET_KEY") or "").strip()
    return {
        "apikey": secret,
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }


def fetch_supabase_rows(table_name: str, columns: str) -> list[dict]:
    if not supabase_ready():
        return []
    root = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    rows: list[dict] = []
    limit = 1000
    offset = 0
    while True:
        response = requests.get(
            f"{root}/rest/v1/{table_name}",
            headers=supabase_headers(),
            params={
                "select": columns,
                "order": "created_at.asc",
                "limit": limit,
                "offset": offset,
            },
            timeout=20,
        )
        response.raise_for_status()
        chunk = response.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def last_n_days(rows: Iterable[dict], days: int = 7) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = []
    for row in rows:
        stamp = parse_datetime(row.get("created_at"))
        if stamp and stamp >= cutoff:
            recent.append(row)
    return recent


def csv_bytes(fieldnames: list[str], rows: Iterable[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return buffer.getvalue().encode("utf-8-sig")
