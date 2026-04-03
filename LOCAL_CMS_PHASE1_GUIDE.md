# Local CMS Phase 1 Guide

This guide documents the new Phase 1 workflow:

- `site_content/` is now the git-tracked source of truth for editable content.
- `render_data/` is now runtime-only and currently keeps logs.
- The Flask admin page running on `localhost` acts as a visual CMS.
- Content changes can be published to GitHub from the admin page.

## 1. What Changed

### Editable content now lives here

- `site_content/site.json`
- `site_content/articles.json`
- `site_content/people.json`
- `site_content/news.json`
- `site_content/images/`

### Runtime-only data now lives here

- `render_data/data_logs/`

At the end of Phase 1, `render_data/` should no longer be treated as the place to edit site content.

## 2. Daily Workflow Summary

Use this when you want to update the site:

1. Start the Flask app locally.
2. Open the local admin page.
3. Edit homepage / publications / people / news / images in the admin UI.
4. Check the `Local CMS Workspace` panel.
5. Click `Publish site_content to GitHub`.
6. Wait for your hosting platform to redeploy from GitHub.

## 3. First-Time Local Startup

Open PowerShell in the project folder:

```powershell
cd "D:\桌面\大学科研\课题组网页"
```

If you already have a virtual environment, activate it:

```powershell
.venv\Scripts\Activate.ps1
```

If you do not have one yet:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then start the site:

```powershell
$env:LOCAL_PORT="8000"
python app.py
```

Open:

- Site: `http://127.0.0.1:8000`
- Admin: `http://127.0.0.1:8000/admin`

## 4. How the Admin Now Works

### Publications

Use the `Publications` tab to:

- add new papers
- edit titles / authors / venue / year / abstract
- edit `Paper URL`
- edit `Official Free Access URL`
- edit `Resources URL`
- choose whether a paper joins the homepage carousel
- upload or replace thumbnails

### People

Use the `People` tab to:

- add members
- edit email / bio / links
- upload member photos
- assign tags

### Home Content

Use the `Home Content` tab to:

- edit lab names
- edit hero summary
- edit homepage welcome text
- edit resource/login note
- edit footer copyright
- upload site logo
- upload footer link icons
- edit footer links
- edit research focus entries
- edit people tags
- manage news items

## 5. The New Local CMS Panel

At the top of the admin page there is now a `Local CMS Workspace` panel.

It shows:

- current content root
- current runtime root
- current Git branch
- last Git commit
- remote origin URL
- changed files inside `site_content/`

It also provides:

- `Download site_content backup`
- `Download runtime logs backup`
- `Publish site_content to GitHub`

## 6. Publishing to GitHub

After editing content locally:

1. Scroll to `Local CMS Workspace`.
2. Confirm the changed files look reasonable.
3. Enter a commit message.
4. Click `Publish site_content to GitHub`.

Behind the scenes, the app runs:

```text
git status -- site_content
git add --all -- site_content
git commit -m "your message"
git push
```

If the current branch has no upstream yet, the app tries:

```text
git push -u origin <current-branch>
```

## 6.1 One Important Boundary

The `Publish site_content to GitHub` button is designed for day-to-day content changes only.

That means it publishes:

- `site_content/*.json`
- `site_content/images/**`

It does not publish framework/code changes such as:

- `app.py`
- `templates/*.html`
- `static/css/*.css`

So for this first Phase 1 migration itself, you still need one normal developer push after reviewing the code changes. After that, routine content edits can mostly happen through the local CMS button.

## 7. What Gets Published

Only files under `site_content/` are included in the visual CMS publish flow.

That means:

- homepage text changes are included
- publication edits are included
- people edits are included
- news edits are included
- uploaded content images are included

Runtime logs are not included in that publish flow.

## 8. Backup Strategy Right Now

During Phase 1 you effectively have two kinds of data:

### Content backup

Primary method:

- GitHub commit history

Secondary method:

- `Download site_content backup` from the admin panel

### Runtime/log backup

Use:

- `Download opens.csv`
- `Download page_views.csv`
- `Download runtime_data_bundle.zip`

## 9. Folder Meanings

### `site_content/`

Use this folder for:

- content JSON
- site logo
- footer icons
- people photos
- article thumbnails
- news images

This folder should be committed to Git.

### `render_data/`

Use this folder only for:

- logs
- temporary runtime-only data

This folder should not be treated as your main content store anymore.

## 10. Manual Fallback If the Admin UI Is Down

If the admin page has a temporary issue, you can still update content manually:

### Edit JSON

- `site_content/site.json`
- `site_content/articles.json`
- `site_content/people.json`
- `site_content/news.json`

### Edit images

- `site_content/images/people/`
- `site_content/images/articles/`
- `site_content/images/news/`
- `site_content/images/`

Then publish manually:

```powershell
git add site_content
git commit -m "Manual content update"
git push
```

## 11. Troubleshooting

### A. The admin page opens, but content looks empty

Check whether these files exist:

- `site_content/site.json`
- `site_content/articles.json`
- `site_content/people.json`
- `site_content/news.json`

If any are missing, restore them from Git or from your backup zip.

### B. Publish says “No site_content changes were detected”

This usually means:

- you did not actually save the form yet
- or your last save produced no real content difference

Try:

1. save the section again
2. refresh `/admin`
3. check the changed files list

### C. Publish fails on `git push`

Common causes:

- GitHub remote not configured
- branch has no upstream
- Git credentials expired

Check manually:

```powershell
git remote -v
git branch --show-current
git status
git push
```

### D. Images do not show

Check:

- the uploaded image actually exists in `site_content/images/...`
- the filename is saved into the corresponding JSON
- the page is hard-refreshed with `Ctrl + F5`

## 12. Recommended Local Habit

Use this lightweight routine every time:

1. pull latest code
2. start local Flask
3. edit through admin
4. verify front-end pages locally
5. publish `site_content`
6. confirm GitHub updated
7. confirm deployed site updated

## 13. What Not To Do Now

During Phase 1, avoid these habits:

- do not keep editing `render_data/site.json`
- do not keep editing `render_data/images/`
- do not use runtime bundle upload as your primary content sync method

Those are now backup / legacy paths only.

## 14. Suggested Phase 2

Phase 2 should focus on Supabase for runtime data only:

- page views
- paper opens
- resource opens
- possibly registration/user records

Recommended Phase 2 order:

1. create Supabase project
2. design `page_views` table
3. design `resource_opens` table
4. dual-write from Flask to CSV + Supabase first
5. move admin analytics to read from Supabase
6. later retire CSV as the main analytics source

## 15. Most Important Rule

From now on:

- `site_content/` = editable website content
- `render_data/` = runtime data

If you remember only one thing, remember that.
