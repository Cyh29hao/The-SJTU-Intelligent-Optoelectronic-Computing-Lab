# Supabase Logs Phase 2.5 Guide

This file documents the current runtime analytics setup after the diskless migration.

## 1. What Lives Where

- `site_content/`
  - homepage content
  - publications
  - people
  - news
  - images
- Supabase
  - `page_views`
  - `resource_opens`

`render_data/data_logs/` is no longer part of the runtime analytics path.

## 2. Current Runtime Behavior

When the Flask app is running with Supabase enabled:

- public page visits are written directly to Supabase `page_views`
- gated paper/resource/free-access opens are written directly to Supabase `resource_opens`
- admin CSV exports are generated on demand from Supabase

So the app is now in a **Supabase-only analytics mode**.

## 3. Environment Variables

The app reads:

```env
SUPABASE_URL=...
SUPABASE_SECRET_KEY=...
SUPABASE_LOGS_ENABLED=1
```

Recommended optional variables:

```env
APP_MODE=local
APP_SECRET_KEY=...
```

Notes:

- use the server-side secret key
- do not use the publishable key for Flask logging writes
- keep `.env` local and private
- in deployed mode, setting `APP_SECRET_KEY` is strongly recommended so admin/user sessions survive restarts

## 4. Current Read Path

The admin dashboard now reads runtime analytics from Supabase only.

If Supabase is not configured:

- the site still renders
- analytics counters will stay empty
- CSV runtime fallback is no longer used

## 5. Analytics Exports

The admin dashboard now offers:

- `resource_opens.csv`
- `page_views.csv`
- `analytics_export_bundle.zip`

These files are generated in memory from Supabase query results at request time.

## 6. Local vs Deployed Mode

You no longer need to edit `IS_LOCAL = 0/1` in source code.

The app now resolves mode in this order:

1. `APP_MODE=local|production` if provided
2. local mode if `LOCAL_PORT` is set
3. deployed mode if hosted platform envs or `PORT` are present
4. local mode by default otherwise

That means:

- local Flask runs can keep using `LOCAL_PORT=5000`
- deployed platforms can rely on `PORT`
- one codebase works for both

## 7. What Was Retired

These old runtime-disk behaviors are no longer the primary path:

- local CSV analytics writes
- local CSV analytics reads
- runtime ZIP upload/replace workflow
- local paper/resource file hosting workflow

Legacy routes still exist only as compatibility shells where needed.

## 8. Recommended Next Step

Now that analytics are diskless, the next natural follow-up is:

- polish the admin analytics view around Supabase data
- optionally add richer per-article charts or date filters
- later evaluate whether the deployed site still needs any write-capable admin routes at all
