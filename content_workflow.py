"""Private CMS drafts with recoverable publication into the existing JSON tree."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from news_editor_store import MAX_IMAGE_BYTES, StoreError, _image_type


MAX_PDF_BYTES = 20 * 1024 * 1024
RECORD_KINDS = {
    'article': ('articles.json', 'articles', 'thumbnail_filename', 'art'),
    'person': ('people.json', 'people', 'photo_filename', 'person'),
    'news': ('news.json', 'news', 'image_filename', 'news'),
}
_MEDIA_NAME = re.compile(r'wf-[a-f0-9]{32}\.(?:png|jpg|gif|webp|pdf)')
_ID = re.compile(r'[A-Za-z0-9_-]{1,96}')


class WorkflowError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _fingerprint(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}-', delete=False) as handle:
            temp = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _validate_kind(kind):
    if kind not in RECORD_KINDS:
        raise WorkflowError('未知的内容类型。', 404)


def _name(value):
    clean = str(value).replace('\\', '/').rsplit('/', 1)[-1]
    clean = ''.join(ch for ch in clean if ch.isprintable()).strip()
    return clean[:160] or 'document.pdf'


class ContentWorkflow:
    def __init__(self, content_root, state_dir):
        self.content_root = Path(content_root).resolve()
        self.state_dir = Path(state_dir).resolve()
        if self.state_dir.is_relative_to(self.content_root):
            raise ValueError('Workflow state must be outside published content.')
        self.file_dir = self.state_dir / 'draft-files'
        self.path = self.state_dir / 'workflow.sqlite3'
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS drafts (
                    kind TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(kind, id)
                );
                CREATE TABLE IF NOT EXISTS submissions (
                    key TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    id TEXT NOT NULL, digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publication_journal (
                    id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS markers (key TEXT PRIMARY KEY);
            ''')
        with self._write():
            pass

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _write(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            self._recover(db)
            yield db

    @contextmanager
    def content_write(self):
        """Use for legacy content writers and Git changes without calling workflow mutators."""
        with self._write() as db:
            yield db

    def _published_path(self, kind):
        _validate_kind(kind)
        path = self.content_root / RECORD_KINDS[kind][0]
        if not path.resolve().is_relative_to(self.content_root):
            raise WorkflowError('公开文件路径超出内容目录。', 409)
        return path

    def published(self, kind):
        try:
            rows = json.loads(self._published_path(kind).read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WorkflowError('公开内容读取失败，请从备份检查原文件。', 409) from error
        if not isinstance(rows, list) or any(not isinstance(entry, dict) for entry in rows):
            raise WorkflowError('公开内容格式无效，请检查原文件。', 409)
        return rows

    @staticmethod
    def _find(records, record_id):
        return next((row for row in records if row.get('id') == record_id), None)

    @staticmethod
    def _state(db, kind, record_id):
        row = db.execute('SELECT state FROM drafts WHERE kind = ? AND id = ?', (kind, record_id)).fetchone()
        return json.loads(row['state']) if row else None

    @staticmethod
    def _put(db, state):
        db.execute('''INSERT INTO drafts(kind, id, state) VALUES (?, ?, ?)
                   ON CONFLICT(kind, id) DO UPDATE SET state = excluded.state''',
                   (state['kind'], state['id'], _json(state)))

    @staticmethod
    def _initial_state(kind, record_id, live):
        filename = live.get(RECORD_KINDS[kind][2], '')
        image = filename if isinstance(filename, str) and _MEDIA_NAME.fullmatch(filename) else None
        return {
            'id': record_id, 'kind': kind, 'draft': live, 'revision': 0,
            'archived': False, 'image': image, 'attachments': live.get('attachments', []),
            'base': _fingerprint(live),
        }

    def _entry(self, kind, record_id, state, live):
        if state is None and live is None:
            return None
        state = state or self._initial_state(kind, record_id, live)
        return {key: value for key, value in state.items() if key != 'base'} | {
            'published': live, 'conflicted': state['base'] != _fingerprint(live),
        }

    def get(self, kind, record_id):
        live = self._find(self.published(kind), record_id)
        with closing(self._connect()) as db:
            return self._entry(kind, record_id, self._state(db, kind, record_id), live)

    def list(self, kind=None):
        with closing(self._connect()) as db:
            if kind is None:
                rows = db.execute('SELECT kind, id, state FROM drafts ORDER BY rowid DESC').fetchall()
            else:
                _validate_kind(kind)
                rows = db.execute('SELECT kind, id, state FROM drafts WHERE kind = ? ORDER BY rowid DESC', (kind,)).fetchall()
        live = {record_kind: self.published(record_kind) for record_kind in {row['kind'] for row in rows}}
        return [self._entry(row['kind'], row['id'], json.loads(row['state']), self._find(live[row['kind']], row['id']))
                for row in rows]

    @staticmethod
    def _check_revision(state, expected):
        actual = state['revision'] if state else 0
        if type(expected) is not int or expected != actual:
            raise WorkflowError('这条内容已有新版本。请刷新页面后再操作。', 409)
        return actual

    @staticmethod
    def _check_base(state, live):
        if state and state['base'] != _fingerprint(live):
            raise WorkflowError('公开快照已改变。请先核对外部同步或其他编辑所作的修改。', 409)

    def rebase(self, kind, record_id, expected_revision):
        """Accept the current public baseline after an editor explicitly reviews a conflict."""
        with self._write() as db:
            state = self._state(db, kind, record_id)
            if state is None or state['archived']:
                raise WorkflowError('找不到可对照的草稿。', 404)
            rev = self._check_revision(state, expected_revision)
            live = self._find(self.published(kind), record_id)
            state.update(base=_fingerprint(live), revision=rev + 1)
            self._put(db, state)
            return self._entry(kind, record_id, state, live)

    def _private_media_path(self, filename):
        path = self.file_dir / filename
        if not path.resolve().is_relative_to(self.state_dir):
            raise WorkflowError('私有文件路径超出草稿目录。', 409)
        return path

    def _store_file(self, extension, body):
        filename = f'wf-{uuid4().hex}.{extension}'
        atomic_write(self._private_media_path(filename), body)
        return filename

    def _store_image(self, body):
        if (isinstance(body, bytes) and 13 <= len(body) <= MAX_IMAGE_BYTES
                and body.startswith((b'GIF87a', b'GIF89a')) and body.endswith(b';')):
            extension = 'gif'
        else:
            try:
                extension, _ = _image_type(body)
            except StoreError as error:
                raise WorkflowError(str(error), error.status) from error
        return self._store_file(extension, body)

    def _store_attachment(self, original_name, body):
        if (not isinstance(body, bytes) or not 15 <= len(body) <= MAX_PDF_BYTES
                or not body.startswith(b'%PDF-') or b'%%EOF' not in body[-2048:]):
            raise WorkflowError('附件须为 20 MB 以内的有效 PDF。')
        return {'filename': self._store_file('pdf', body), 'name': _name(original_name), 'size': len(body)}

    def save(self, kind, record, *, record_id=None, expected_revision=0, image=None,
             attachments=(), submission_key=None, submission_digest=None, unique_match=None):
        _validate_kind(kind)
        if not isinstance(record, dict) or (record_id is not None and (
                not isinstance(record_id, str) or not _ID.fullmatch(record_id))):
            raise WorkflowError('内容数据或编号无效。')
        if attachments and kind != 'article':
            raise WorkflowError('本版只为论文提供 PDF 附件。')
        if submission_key is not None:
            if not isinstance(submission_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', submission_key):
                raise WorkflowError('提交编号格式无效。')
            submission_digest = submission_digest or _fingerprint([
                record_id, record, hashlib.sha256(image[1]).hexdigest() if image else None,
                [(name, hashlib.sha256(body).hexdigest()) for name, body in attachments],
            ])

        with self._write() as db:
            if submission_key is not None:
                previous = db.execute('SELECT kind, id, digest FROM submissions WHERE key = ?', (submission_key,)).fetchone()
                if previous:
                    if previous['kind'] != kind or previous['digest'] != submission_digest:
                        raise WorkflowError('此提交编号已用于另一份内容。', 409)
                    saved_id = previous['id']
                    return self._entry(kind, saved_id, self._state(db, kind, saved_id),
                                       self._find(self.published(kind), saved_id)), False

            is_new = record_id is None
            if is_new:
                if unique_match is not None:
                    for state_row in db.execute('SELECT id, state FROM drafts WHERE kind = ?', (kind,)):
                        if unique_match(json.loads(state_row['state'])['draft']):
                            raise WorkflowError(f"已有相同 DOI 的草稿 {state_row['id']}，请到后台核对。", 409)
                    for published in self.published(kind):
                        if unique_match(published):
                            raise WorkflowError(f"已有相同 DOI 的论文 {published['id']}，请到后台核对。", 409)
                record_id = f'{RECORD_KINDS[kind][3]}_{uuid4().hex}'
            state = self._state(db, kind, record_id)
            live = self._find(self.published(kind), record_id)
            if not is_new and state is None and live is None:
                raise WorkflowError('找不到要修改的内容。', 404)
            revision = self._check_revision(state, expected_revision)
            self._check_base(state, live)
            if state and state['archived']:
                raise WorkflowError('请先从回收区恢复这条内容。', 409)
            prior = state['draft'] if state else live or {}
            draft = dict(prior) | record
            draft.update(id=record_id, last_edited=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            draft.pop('attachments', None)
            media = {file['name']: file for file in (state['attachments'] if state else prior.get('attachments', []))}
            for original_name, body in attachments:
                item = self._store_attachment(original_name, body)
                media[item['name']] = item
            prior_image = state['image'] if state else self._initial_state(kind, record_id, live or {})['image']
            draft_image = self._store_image(image[1]) if image else prior_image
            updated = {
                'kind': kind, 'id': record_id, 'draft': draft, 'revision': revision + 1,
                'archived': False, 'image': draft_image, 'attachments': list(media.values()),
                'base': _fingerprint(live),
            }
            self._put(db, updated)
            if submission_key is not None:
                db.execute('INSERT INTO submissions VALUES (?, ?, ?, ?)', (submission_key, kind, record_id, submission_digest))
            return self._entry(kind, record_id, updated, live), state is None

    def _public_media_path(self, kind, filename):
        if not _MEDIA_NAME.fullmatch(filename):
            raise WorkflowError('无效的公开文件名。', 409)
        folder = self.content_root / 'attachments' if filename.endswith('.pdf') else (
            self.content_root / 'images' / RECORD_KINDS[kind][1])
        path = folder / filename
        if not path.resolve().is_relative_to(self.content_root):
            raise WorkflowError('公开文件路径超出内容目录。', 409)
        return path

    def _copy_to_public(self, kind, filename):
        destination = self._public_media_path(kind, filename)
        if not destination.is_file():
            try:
                body = self._private_media_path(filename).read_bytes()
            except OSError as error:
                raise WorkflowError('公开文件的本地原件缺失，请从备份恢复。', 409) from error
            atomic_write(destination, body)

    @staticmethod
    def _record_files(kind, records):
        field = RECORD_KINDS[kind][2]
        names = [row.get(field) for row in records]
        if kind == 'article':
            names.extend(file['filename'] for row in records for file in row.get('attachments', []))
        return {name for name in names if isinstance(name, str) and _MEDIA_NAME.fullmatch(name)}

    def _stage(self, db, kind, previous, records, states, files=(), marker=None):
        retired = self._record_files(kind, previous) - self._record_files(kind, records)
        payload = _json({'kind': kind, 'before': _fingerprint(previous), 'records': records,
                         'states': states, 'files': sorted(set(files)), 'retired': sorted(retired), 'marker': marker})
        db.execute('INSERT INTO publication_journal VALUES (1, ?)', (payload,))

    def _recover(self, db):
        pending = db.execute('SELECT payload FROM publication_journal WHERE id = 1').fetchone()
        if pending is None:
            return
        change = json.loads(pending['payload'])
        kind, after = change['kind'], change['records']
        current = _fingerprint(self.published(kind))
        if current not in (change['before'], _fingerprint(after)):
            raise WorkflowError('上次写入未完成，公开内容也已变更，请从备份核对后恢复。', 409)
        for filename in change['files']:
            self._copy_to_public(kind, filename)
        if current != _fingerprint(after):
            data = json.dumps(after, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
            atomic_write(self._published_path(kind), data)
        for filename in change.get('retired', ()):
            public = self._public_media_path(kind, filename)
            if public.is_file():
                private = self._private_media_path(filename)
                if not private.is_file():
                    atomic_write(private, public.read_bytes())
                public.unlink()
        for state in change['states']:
            self._put(db, state)
        if change['marker']:
            db.execute('INSERT INTO markers VALUES (?)', (change['marker'],))
        db.execute('DELETE FROM publication_journal WHERE id = 1')

    def transition(self, kind, record_id, action, expected_revision, normalize=None, *, attachments=None):
        if action not in ('publish', 'withdraw', 'archive', 'restore'):
            raise WorkflowError('未知的发布操作。')
        with self._write() as db:
            records = self.published(kind)
            live = self._find(records, record_id)
            state = self._state(db, kind, record_id)
            if state is None and live is None:
                raise WorkflowError('找不到这条内容。', 404)
            revision = self._check_revision(state, expected_revision)
            self._check_base(state, live)
            state = state or self._initial_state(kind, record_id, live)
            if state['archived'] != (action == 'restore'):
                raise WorkflowError('当前内容状态与操作不符。', 409)
            if action == 'withdraw' and live is None:
                raise WorkflowError('这条内容尚未发布。', 409)

            replacement = None
            files = []
            updated = [row for row in records if row.get('id') != record_id]
            if action == 'publish':
                replacement = dict(state['draft'])
                if state['image']:
                    replacement[RECORD_KINDS[kind][2]] = state['image']
                    files.append(state['image'])
                available = {file['filename']: file for file in state['attachments']}
                chosen = set(available) if attachments is None else set(attachments)
                if chosen - available.keys():
                    raise WorkflowError('有附件不存在，请刷新预览。', 409)
                if chosen:
                    replacement['attachments'] = [file for name, file in available.items() if name in chosen]
                    files.extend(chosen)
                else:
                    replacement.pop('attachments', None)
                if live is None:
                    updated.append(replacement)
                else:
                    updated = [replacement if row.get('id') == record_id else row for row in records]
                if normalize:
                    normalize(updated, record_id)
                replacement = self._find(updated, record_id)
                state['draft'] = dict(replacement)
            state.update(revision=revision + 1, archived=action == 'archive', base=_fingerprint(replacement))
            self._stage(db, kind, records, updated, [state], files)

        with self._write() as db:
            return self._entry(kind, record_id, self._state(db, kind, record_id),
                               self._find(self.published(kind), record_id))

    def private_file(self, kind, record_id, filename):
        if not _MEDIA_NAME.fullmatch(filename):
            return None
        entry = self.get(kind, record_id)
        if entry is None:
            return None
        if filename != entry['image'] and filename not in {file['filename'] for file in entry['attachments']}:
            return None
        path = self._private_media_path(filename)
        if path.is_file():
            return path
        path = self._public_media_path(kind, filename)
        return path if path.is_file() else None

    def public_file(self, filename):
        if not _MEDIA_NAME.fullmatch(filename) or not filename.endswith('.pdf'):
            return None
        for entry in self.published('article'):
            if any(file.get('filename') == filename for file in entry.get('attachments', [])):
                path = self._public_media_path('article', filename)
                return path if path.is_file() else None
        return None

    def public_image(self, kind, filename):
        _validate_kind(kind)
        if not _MEDIA_NAME.fullmatch(filename) or filename.endswith('.pdf'):
            return None
        field = RECORD_KINDS[kind][2]
        if any(entry.get(field) == filename for entry in self.published(kind)):
            path = self._public_media_path(kind, filename)
            return path if path.is_file() else None
        return None

    def migrate_news_pilot(self, pilot_store):
        """Preserve ids, published/withdrawn state and the pilot's private image bytes."""
        marker = 'news-pilot-v1'
        with self._write() as db:
            if db.execute('SELECT 1 FROM markers WHERE key = ?', (marker,)).fetchone():
                return
            records = self.published('news')
            collection = {row['id']: row for row in records}
            images = {}
            states = []
            public_copies = []
            for entry in pilot_store.list_entries():
                news_id = entry['id']
                if self._state(db, 'news', news_id):
                    continue
                draft, published = entry['draft'].copy(), entry['published']
                if published:
                    published = collection.get(news_id, {}) | published
                for snapshot in (draft, published):
                    if snapshot is None:
                        continue
                    filename = snapshot.get('image_filename', '')
                    if filename.startswith('managed-'):
                        if filename not in images:
                            image = pilot_store.get_image(filename, include_drafts=True)
                            if not image:
                                raise WorkflowError('新闻试点的配图原件缺失，请先恢复原数据库。', 409)
                            images[filename] = self._store_image(image[0])
                        snapshot['image_filename'] = images[filename]
                if published:
                    collection[news_id] = published
                    if published.get('image_filename') in images.values():
                        public_copies.append(published['image_filename'])
                else:
                    collection.pop(news_id, None)
                if draft != published:
                    draft_image = draft.get('image_filename')
                    states.append({'kind': 'news', 'id': news_id, 'draft': draft, 'revision': entry['revision'],
                                   'archived': False, 'image': draft_image if draft_image in images.values() else None,
                                   'attachments': [], 'base': _fingerprint(published)})
            self._stage(db, 'news', records, list(collection.values()), states, public_copies, marker)
        with self._write():
            pass
