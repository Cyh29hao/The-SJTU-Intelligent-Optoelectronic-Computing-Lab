# OPTICom Lab Website Ops Automation and Security Plan

This document records pending operations work so the plan survives chat/context loss.

## Current Production Assumption

- The important deployed site is currently `http://111.186.57.166/`.
- Render is currently treated as a free/demo display deployment, not the main production target.
- Runtime analytics should live in Supabase, not in local CSV files.

## 1. Safe Auto Sync From GitHub

Goal: every 3 hours, check whether the production server can safely sync content from GitHub.

Rules:

- Run on the production server, not as part of a normal web request.
- Fetch `origin/main`.
- If there are no incoming commits, do nothing.
- If incoming commits only change `site_content/**`, allow automatic fast-forward sync.
- If any incoming commit changes non-content files, skip automatic sync.
  - Examples: `app.py`, `templates/**`, `static/**`, `requirements.txt`, scripts, config files.
- If local `site_content/**` has unpublished changes, skip automatic sync.
- If sync fails or is skipped, send an email alert to `cyh29hao@sjtu.edu.cn`.
- Optional: successful sync may be logged without email to avoid noise.

Implementation target:

- Add `scripts/auto_sync_site_content.py`.
- Use server cron or systemd timer.
- Send email only when sync fails or is skipped for a reason that needs attention.
- Example cron shape:

```bash
0 */3 * * * cd /path/to/project && /path/to/venv/bin/python scripts/auto_sync_site_content.py
```

## 2. Weekly Analytics Email

Goal: every 7 days, send an analytics report and raw CSV files to:

```text
cyh29hao@sjtu.edu.cn
```

Data source:

- Supabase table: `page_views`
- Supabase table: `resource_opens`

Suggested email content:

- Reporting period.
- Total page views.
- Unique visitors.
- Publication detail views.
- Resource opens.
- View-to-open conversion rate.
- Top pages.
- Top publications by views.
- Top resource opens.
- 7-day trend summary.

Attachments:

- `page_views_weekly.csv`
- `resource_opens_weekly.csv`
- Optional: `weekly_analytics_bundle.zip`

Implementation target:

- Add `scripts/send_weekly_analytics_email.py`.
- Use a shared mail helper module.
- Schedule by server cron/systemd timer.
- Example cron shape:

```bash
0 9 * * 1 cd /path/to/project && /path/to/venv/bin/python scripts/send_weekly_analytics_email.py
```

## 3. Email Provider Direction

Preferred first attempt was Brevo because it has a free tier and can send far more than one weekly report.

Current issue:

- Brevo account shows `Email sending status: Suspended`.
- Brevo asks for additional verification through support.

Possible paths:

1. Resolve Brevo verification.
   - Contact Brevo support.
   - Explain that the account will only send internal weekly website analytics to `cyh29hao@sjtu.edu.cn`.
   - State expected volume: about 1 email/week.
   - No marketing/newsletter use.
   - Sender domain/email belongs to the lab/user.

2. Use SMTP from a trusted mailbox provider.
   - Possible: SJTU mailbox if SMTP/app password is available.
   - Possible: personal mailbox SMTP if policy allows.
   - Risk: provider rate limits or blocks automated server login.

3. Use another transactional email provider.
   - Resend, Mailgun, Postmark, SendGrid, etc.
   - Usually requires sender/domain verification.
   - Free tiers and verification requirements may change.

Recommended next step:

- Try resolving Brevo verification first.
- The scripts can be installed before Brevo is active; they will log email delivery failures until `BREVO_API_KEY`, `MAIL_FROM`, and `WEEKLY_REPORT_TO` are configured.

Environment variables to support:

```text
WEEKLY_REPORT_TO=cyh29hao@sjtu.edu.cn
MAIL_FROM=...
MAIL_FROM_NAME=OPTICom Lab Website

# For Brevo API option:
BREVO_API_KEY=...

# For SMTP fallback option:
MAIL_SMTP_HOST=...
MAIL_SMTP_PORT=587
MAIL_SMTP_USER=...
MAIL_SMTP_PASSWORD=...
```

## 4. Supabase-Only Analytics and Clearing Data

Goal: local CSV files are not used for runtime analytics. Online data is recorded in Supabase only.

Planned cleanup:

- Remove or disable legacy local CSV write paths.
- Ensure local development does not write analytics CSV files.
- Keep CSV downloads generated dynamically from Supabase.
- Remove/ignore old local CSV files:
  - `render_data/data_logs/downloads.csv`
  - `render_data/data_logs/page_views.csv`

Backend button:

- Add Admin Analytics danger-zone section.
- Add button to clear Supabase analytics data.
- Require explicit confirmation, for example typing:

```text
CLEAR
```

Clear targets:

- Supabase `page_views`
- Supabase `resource_opens`

Recommended safety:

- Provide/encourage download backup first.
- Send email notification after clearing, if mail is configured.

## 5. GitHub Security Audit

Goal: determine whether repository history contains secrets or personal analytics data.

Initial audit commands:

```bash
git ls-files
git ls-files .env secret_key.bin render_data/data_logs
git grep -n "SUPABASE_SECRET_KEY\|APP_SECRET_KEY\|password\|secret\|apikey\|Bearer"
git log --all -- .env secret_key.bin render_data/data_logs
git log --all -p -- .env
git log --all -p -- secret_key.bin
git log --all -p -- render_data/data_logs
```

Risk targets:

- `.env`
- `secret_key.bin`
- Supabase service/secret keys.
- Brevo/API/SMTP keys.
- GitHub tokens.
- CSV files containing visitor names, affiliations, and emails.

If history leak is found:

- Prepare a separate destructive-history plan.
- Use `git filter-repo` or BFG.
- Force-push cleaned history.
- Rotate any exposed keys.
- Coordinate re-clone/reset for any collaborators/deployments.

User has approved future Git history rewrite if the audit confirms historical leakage.

## Suggested Execution Order

1. Run read-only security audit and report findings.
2. Implement Supabase-only analytics cleanup and Admin clear-data button.
3. Implement mail helper and weekly analytics script.
4. Implement 12-hour safe sync script and failure/skip email notices.
5. Configure server cron/systemd timers after scripts are manually tested.
6. If needed, run Git history rewrite as a separate explicitly reported operation.
