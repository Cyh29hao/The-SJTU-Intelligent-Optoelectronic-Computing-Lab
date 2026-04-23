# Security Audit Snapshot - 2026-04-23

## Current Findings

- `.env` is ignored and is not currently tracked.
- `secret_key.bin` is ignored and is not currently tracked.
- `render_data/data_logs/` is ignored and current local CSV files are not tracked.
- `site_content/**` is tracked as intended.
- `.venv/` was tracked in Git and should not be. This plan removes it from the Git index and adds `.venv/` to `.gitignore`.

## History Findings

The following sensitive/runtime paths appear in Git history:

- `secret_key.bin`
- root-level `downloads.csv`

Relevant historical commits observed:

- `2452852 Initial deploy to Render`
- `5d03893 Remove sensitive files and add .gitignore`
- `cf62060 Update lab website`
- `8e6252d 0403 1624`

## Risk Assessment

- Because `secret_key.bin` appeared in history, any deployed session secret from that era should be considered exposed.
- Because `downloads.csv` appeared in history, historical visitor/access records may have been exposed if the repository was public or shared.
- No current tracked file appears to contain literal Supabase secret values; current code uses environment variables.

## Recommended Follow-up

1. Rotate `APP_SECRET_KEY` on the production server.
2. Rotate Supabase service/secret keys if there is any chance they were ever committed outside current tracked files.
3. Run a separate Git history cleanup using `git filter-repo` or BFG to remove historical `secret_key.bin` and CSV data.
4. Force-push cleaned history only after coordinating deployments/clones.

History rewrite is intentionally not performed in this change set.
