"""Safely sync site_content from GitHub when only content files changed.

Intended schedule: every 3 hours on the production server.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from ops_common import CONTENT_REL, parse_status_lines, run_git, send_ops_email


def notify(subject: str, body: str, dry_run: bool) -> None:
    print(subject)
    print(body)
    if dry_run:
        return
    ok, details = send_ops_email(subject, body)
    if not ok:
        print(f"Email notification skipped/failed: {details}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Check status but do not pull or send email.")
    args = parser.parse_args()

    branch_result = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch_result["ok"]:
        notify("[OPTICom] Auto sync failed: branch detection", str(branch_result["stderr"]), args.dry_run)
        return 1
    branch = str(branch_result["stdout"] or "main").strip()

    fetch = run_git(["fetch", "origin", branch, "--prune"], timeout=120)
    if not fetch["ok"]:
        notify("[OPTICom] Auto sync failed: git fetch", str(fetch["stderr"] or fetch["stdout"]), args.dry_run)
        return 1

    local_status = run_git(["status", "--short", "--", CONTENT_REL])
    local_changes = parse_status_lines(str(local_status["stdout"] or ""))
    if local_changes:
        body = "Local site_content changes exist; not syncing down.\n\n" + "\n".join(
            f"{item['status']} {item['path']}" for item in local_changes
        )
        notify("[OPTICom] Auto sync skipped: local content changes", body, args.dry_run)
        return 0

    ahead_behind = run_git(["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"])
    ahead = behind = 0
    if ahead_behind["ok"] and ahead_behind["stdout"]:
        parts = str(ahead_behind["stdout"]).split()
        if len(parts) >= 2:
            ahead = int(parts[0])
            behind = int(parts[1])

    if ahead > 0:
        notify(
            "[OPTICom] Auto sync skipped: server has local commits",
            f"Server branch is ahead of origin/{branch} by {ahead} commit(s).",
            args.dry_run,
        )
        return 0
    if behind == 0:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} already up to date.")
        return 0

    changed = run_git(["diff", "--name-only", f"HEAD..origin/{branch}"])
    files = [line.strip().replace("\\", "/") for line in str(changed["stdout"] or "").splitlines() if line.strip()]
    non_content = [path for path in files if not (path == CONTENT_REL or path.startswith(CONTENT_REL + "/"))]
    if non_content:
        body = "Incoming changes include non-site_content files; use normal deploy flow.\n\n" + "\n".join(non_content)
        notify("[OPTICom] Auto sync skipped: code/template changes", body, args.dry_run)
        return 0

    body = "Incoming content files:\n\n" + "\n".join(files)
    if args.dry_run:
        print("[DRY RUN] Would sync site_content from GitHub")
        print(body)
        return 0

    pull = run_git(["pull", "--ff-only", "origin", branch], timeout=120)
    if not pull["ok"]:
        notify("[OPTICom] Auto sync failed: git pull", str(pull["stderr"] or pull["stdout"]), args.dry_run)
        return 1
    print("Synced site_content from GitHub.")
    print(pull["stdout"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
