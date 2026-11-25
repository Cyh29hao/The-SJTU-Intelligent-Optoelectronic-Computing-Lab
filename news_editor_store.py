"""SQLite persistence for drafts, published news and their images."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4


MAX_IMAGE_BYTES = 5 * 1024 * 1024
_TEXT_FIELDS = {
    'title': ('英文标题', 220),
    'title_zh': ('中文标题', 220),
    'summary': ('英文摘要', 700),
    'summary_zh': ('中文摘要', 700),
    'content': ('英文正文', 20000),
    'content_zh': ('中文正文', 20000),
    'date': ('日期', 10),
}
_FLAG_FIELDS = ('pinned', 'hide_from_home')


class StoreError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _flag_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        value = str(value).strip().lower()
        if value in ('1', 'true', 'on'):
            return True
        if value in ('0', 'false', 'off', ''):
            return False
    raise StoreError('置顶和首页展示字段应为布尔值。')


def _draft_values(values):
    if not isinstance(values, dict) or values.keys() - _TEXT_FIELDS.keys() - set(_FLAG_FIELDS):
        raise StoreError('新闻草稿包含不支持的字段。')

    draft = {}
    for field, (label, limit) in _TEXT_FIELDS.items():
        value = values.get(field, '')
        if not isinstance(value, str):
            raise StoreError(f'{label}须填写为文本。')
        value = value.strip()
        if len(value) > limit:
            raise StoreError(f'{label}最多可填写 {limit} 个字符。')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise StoreError(f'{label}包含无效字符。') from None
        draft[field] = value

    if not (draft['title'] or draft['title_zh']):
        raise StoreError('请至少填写中文或英文标题。')
    if draft['date']:
        try:
            parsed = date.fromisoformat(draft['date'])
            if parsed.isoformat() != draft['date']:
                raise ValueError
        except ValueError:
            raise StoreError('日期须为有效日期，格式为 YYYY-MM-DD。') from None

    for field in _FLAG_FIELDS:
        draft[field] = _flag_value(values.get(field, False))
    return draft


def _image_type(image):
    if not isinstance(image, bytes) or len(image) > MAX_IMAGE_BYTES:
        raise StoreError('图片不得超过 5 MB。')
    if (
        len(image) >= 45
        and image.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')
        and image.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82')
    ):
        return 'png', 'image/png'
    if len(image) >= 12 and image.startswith(b'\xff\xd8\xff') and image.endswith(b'\xff\xd9'):
        return 'jpg', 'image/jpeg'
    if (
        len(image) >= 20
        and image.startswith(b'RIFF')
        and int.from_bytes(image[4:8], 'little') + 8 == len(image)
        and image[8:16] in (b'WEBPVP8 ', b'WEBPVP8L', b'WEBPVP8X')
    ):
        return 'webp', 'image/webp'
    raise StoreError('请选择有效的 PNG、JPEG 或 WebP 图片。')


def _snapshot(row, column, image_column):
    if row[column] is None:
        return None
    story = json.loads(row[column])
    story['id'] = row['id']
    story['image_filename'] = row[image_column]
    return story


def _entry(row):
    return {
        'id': row['id'],
        'draft': _snapshot(row, 'draft', 'draft_image'),
        'published': _snapshot(row, 'published', 'published_image'),
        'revision': row['revision'],
    }


def _story_order(story):
    return (story['date'], story['last_edited'], story['id'])


class NewsStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _write(self):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection

    def initialize(self, seed_news):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write() as connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS news (
                    id TEXT PRIMARY KEY,
                    draft TEXT NOT NULL,
                    draft_image TEXT NOT NULL,
                    published TEXT,
                    published_image TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    submission_key TEXT UNIQUE,
                    submission_digest TEXT
                )
            ''')
            connection.execute('''
                CREATE TABLE IF NOT EXISTS images (
                    filename TEXT PRIMARY KEY,
                    mime_type TEXT NOT NULL,
                    body BLOB NOT NULL
                )
            ''')
            connection.execute('''
                CREATE TABLE IF NOT EXISTS editor_settings (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            ''')
            if connection.execute("SELECT 1 FROM editor_settings WHERE name = 'seeded'").fetchone():
                return
            for story in seed_news:
                snapshot = {field: story.get(field, '') for field in _TEXT_FIELDS}
                snapshot.update({field: bool(story.get(field)) for field in _FLAG_FIELDS})
                snapshot['last_edited'] = story.get('last_edited', '')
                payload = json.dumps(snapshot, ensure_ascii=False)
                image = story.get('image_filename', '')
                connection.execute('''
                    INSERT INTO news (id, draft, draft_image, published, published_image)
                    VALUES (?, ?, ?, ?, ?)
                ''', (story['id'], payload, image, payload, image))
            connection.execute("INSERT INTO editor_settings (name, value) VALUES ('seeded', 'true')")

    def list_entries(self):
        with closing(self._connect()) as connection:
            entries = [_entry(row) for row in connection.execute('SELECT * FROM news')]
        return sorted(entries, key=lambda entry: _story_order(entry['draft']), reverse=True)

    def get_entry(self, news_id):
        with closing(self._connect()) as connection:
            row = self._find_news(connection, news_id)
        return _entry(row) if row else None

    def list_published(self):
        with closing(self._connect()) as connection:
            stories = [
                _snapshot(row, 'published', 'published_image')
                for row in connection.execute('SELECT * FROM news WHERE published IS NOT NULL')
            ]
        return sorted(stories, key=lambda story: (story['pinned'], *_story_order(story)), reverse=True)

    @staticmethod
    def _find_news(connection, news_id):
        return connection.execute('SELECT * FROM news WHERE id = ?', (news_id,)).fetchone()

    @staticmethod
    def _require_revision(row, expected_revision):
        if row is None:
            raise StoreError('找不到这条新闻。', 404)
        if type(expected_revision) is not int or expected_revision < 1:
            raise StoreError('缺少有效的新闻版本号。请刷新页面后重试。')
        if expected_revision != row['revision']:
            raise StoreError('这条新闻已有新版本。请刷新页面后再操作。', 409)

    @staticmethod
    def _prune_image(connection, filename):
        if filename:
            connection.execute('''
                DELETE FROM images
                WHERE filename = ? AND NOT EXISTS (
                    SELECT 1 FROM news WHERE draft_image = images.filename OR published_image = images.filename
                )
            ''', (filename,))

    def save_draft(self, values, image=None, *, news_id=None, expected_revision=None, submission_key=None):
        draft = _draft_values(values)
        image_format = _image_type(image) if image is not None else None
        digest = None
        if submission_key is not None:
            if news_id is not None or not isinstance(submission_key, str) or not re.fullmatch(r'[!-~]{1,128}', submission_key):
                raise StoreError('提交编号须为 1 至 128 个无空格 ASCII 字符，仅用于新草稿。')
            request_bytes = json.dumps(draft, ensure_ascii=False, sort_keys=True).encode('utf-8')
            digest = hashlib.sha256(request_bytes + b'\x00' + (image or b'')).hexdigest()

        with self._write() as connection:
            if submission_key is not None:
                row = connection.execute('SELECT * FROM news WHERE submission_key = ?', (submission_key,)).fetchone()
                if row is not None:
                    if row['submission_digest'] != digest:
                        raise StoreError('提交编号已用于另一份内容。请使用新的提交编号。', 409)
                    return _entry(row), False

            created = news_id is None
            if created:
                news_id = f'news_{uuid4().hex}'
                old_image = ''
            else:
                row = self._find_news(connection, news_id)
                self._require_revision(row, expected_revision)
                old_image = row['draft_image']
                saved_values = json.loads(row['draft'])
                saved_values.pop('last_edited', None)
                if image_format is None and draft == saved_values:
                    return _entry(row), False

            filename = old_image
            if image_format is not None:
                extension, mime_type = image_format
                filename = f'managed-{uuid4().hex}.{extension}'
                connection.execute('INSERT INTO images VALUES (?, ?, ?)', (filename, mime_type, image))

            draft['last_edited'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            payload = json.dumps(draft, ensure_ascii=False)
            if created:
                connection.execute('''
                    INSERT INTO news (id, draft, draft_image, submission_key, submission_digest)
                    VALUES (?, ?, ?, ?, ?)
                ''', (news_id, payload, filename, submission_key, digest))
            else:
                connection.execute('''
                    UPDATE news SET draft = ?, draft_image = ?, revision = revision + 1 WHERE id = ?
                ''', (payload, filename, news_id))
                if filename != old_image:
                    self._prune_image(connection, old_image)
            return _entry(self._find_news(connection, news_id)), created

    def set_publication(self, news_id, *, publish, expected_revision):
        with self._write() as connection:
            row = self._find_news(connection, news_id)
            self._require_revision(row, expected_revision)
            payload = row['draft'] if publish else None
            image = row['draft_image'] if publish else ''
            if publish:
                draft = json.loads(payload)
                complete_copy = any(draft[f'title{suffix}'] and draft[f'content{suffix}'] for suffix in ('', '_zh'))
                if not draft['date'] or not complete_copy:
                    raise StoreError('发布前请填写日期，以及至少一种语言的标题和正文。')
            if payload == row['published'] and image == row['published_image']:
                return _entry(row)
            connection.execute('''
                UPDATE news SET published = ?, published_image = ?, revision = revision + 1 WHERE id = ?
            ''', (payload, image, news_id))
            if image != row['published_image']:
                self._prune_image(connection, row['published_image'])
            return _entry(self._find_news(connection, news_id))

    def get_image(self, filename, *, include_drafts=False):
        with closing(self._connect()) as connection:
            row = connection.execute('''
                SELECT body, mime_type FROM images
                WHERE filename = ? AND EXISTS (
                    SELECT 1 FROM news WHERE published_image = images.filename
                    OR (? AND draft_image = images.filename)
                )
            ''', (filename, include_drafts)).fetchone()
        return (row['body'], row['mime_type']) if row else None

    def set_token(self, token_digest):
        if not isinstance(token_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', token_digest):
            raise StoreError('脚本凭证摘要格式无效。')
        with self._write() as connection:
            connection.execute("INSERT OR REPLACE INTO editor_settings VALUES ('script_token', ?)", (token_digest,))

    def get_token(self):
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value FROM editor_settings WHERE name = 'script_token'").fetchone()
        return row['value'] if row else None

    def revoke_token(self):
        with self._write() as connection:
            connection.execute("DELETE FROM editor_settings WHERE name = 'script_token'")
