"""Run the isolated news pilot on this computer. Invoke from the repo root."""

import argparse
import json
import os
import secrets
from pathlib import Path

from werkzeug.security import generate_password_hash

from scripts.preview_content_git import prepare_content_checkout


ROOT = Path(__file__).resolve().parents[1]


def prepare_preview(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    previous_content = state_dir / 'site_content'
    seed = previous_content if (previous_content / 'site.json').is_file() else ROOT / 'site_content'
    content = prepare_content_checkout(state_dir, seed)

    credentials_path = state_dir / 'preview-credentials.json'
    try:
        with credentials_path.open('x', encoding='utf-8') as output:
            credentials = {role: secrets.token_urlsafe(12) for role in ('editor', 'owner')}
            json.dump(credentials, output, indent=2)
    except FileExistsError:
        with credentials_path.open(encoding='utf-8') as source:
            credentials = json.load(source)

    os.environ.update(
        APP_MODE='local',
        LOCAL_HOST='127.0.0.1',
        APP_SECRET_KEY=secrets.token_hex(32),
        CONTENT_ROOT=str(content),
        CONTENT_GIT_ROOT=str(content.parent),
        CONTENT_GIT_OFFLINE='1',
        SUPABASE_LOGS_ENABLED='0',
        SUPABASE_URL='',
        SUPABASE_SECRET_KEY='',
        NEWS_EDITOR_PREVIEW='1',
        NEWS_EDITOR_DB=str(state_dir / 'news.sqlite3'),
    )
    for role, password in credentials.items():
        os.environ[f'NEWS_EDITOR_{role.upper()}_PASSWORD_HASH'] = generate_password_hash(password)
    return credentials_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--handoff', action='store_true', help='Open the full administrator and content workflow')
    parser.add_argument('--state-dir', type=Path, default=(
        Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'OPTICompt Lab' / 'News Preview'
    ))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    os.environ['LOCAL_PORT'] = str(args.port)
    os.environ['ADMIN_HANDOFF_PREVIEW'] = '1' if args.handoff else '0'
    credentials_path = prepare_preview(args.state_dir.expanduser().resolve())

    from app import app

    print(f'Home: http://localhost:{args.port}/', flush=True)
    print(f'News editor: http://localhost:{args.port}/editor/', flush=True)
    print(f'Local preview credentials: {credentials_path}', flush=True)
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
