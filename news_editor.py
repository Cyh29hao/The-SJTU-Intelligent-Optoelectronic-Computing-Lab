import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from datetime import date
from functools import wraps
from io import BytesIO

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.datastructures import MultiDict
from werkzeug.security import check_password_hash

from news_editor_store import MAX_IMAGE_BYTES, NewsStore, StoreError


editor = Blueprint('news_editor', __name__, url_prefix='/editor')
SESSION_SECONDS = 8 * 60 * 60
TEXT_FIELDS = ('title', 'title_zh', 'date', 'summary', 'summary_zh', 'content', 'content_zh')
FLAG_FIELDS = ('pinned', 'hide_from_home')
OWNER_ADMIN_READ_ENDPOINTS = frozenset({
    'admin_dashboard', 'admin_content', 'admin_assets', 'admin_settings', 'admin_analytics',
    'download_site_content_zip', 'download_logs_csv', 'download_page_views_csv', 'download_render_data_zip',
    'admin_handoff_backup',
})
EDITOR_ADMIN_READ_ENDPOINTS = {'admin_dashboard', 'admin_content', 'admin_assets'}
CONTENT_ACTIONS = {'add', 'edit', 'edit_site_welcome', 'edit_join_us'}
SITE_WRITES = {'edit_site_branding', 'edit_friend_links', 'edit_research_highlights', 'edit_person_tags'}
ANALYTICS_WRITES = {'publish_content_to_github', 'sync_content_from_github', 'clear_supabase_analytics'}
RECORD_UPLOADS = {'admin_upload_thumbnail', 'admin_upload_person_photo', 'admin_upload_news_image'}
HOME_FIELDS = {'hero_summary', 'hero_summary_zh', 'home_welcome', 'home_welcome_zh', 'home_note', 'home_note_zh'}
JOIN_FIELDS = {
    'admissions_title', 'admissions_title_zh', 'admissions_intro', 'admissions_intro_zh',
    'admissions_lab_profile', 'admissions_lab_profile_zh', 'admissions_apply',
    'admissions_apply_zh', 'admissions_news_id',
}


def configure_news_editor(app, content_root, record_helpers, *, production=False, seed_news=()):
    configured_path = Path(os.environ['NEWS_EDITOR_DB'])
    if production and not configured_path.is_absolute():
        raise RuntimeError('Production requires an absolute NEWS_EDITOR_DB path.')
    store_path = configured_path.resolve()
    content_root = Path(content_root).resolve()
    if store_path == content_root or content_root in store_path.parents:
        raise RuntimeError('Editor state must live outside the published content directory.')
    roles = ('owner', 'editor')
    accounts = {role: os.environ[f'NEWS_EDITOR_{role.upper()}_PASSWORD_HASH'] for role in roles}
    if not all(accounts.values()) or not os.environ.get('APP_SECRET_KEY'):
        raise RuntimeError('The news editor requires separate accounts and a session key.')
    admin_host = os.environ.get('ADMIN_HANDOFF_HOST', '').strip().lower()
    if production and not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', admin_host):
        raise RuntimeError('Production requires ADMIN_HANDOFF_HOST to be the official HTTPS hostname.')
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = NewsStore(store_path)
    store.initialize(seed_news)
    full_admin = production or os.environ.get('ADMIN_HANDOFF_PREVIEW') == '1'
    if full_admin:
        from content_workflow import ContentWorkflow

        records = ContentWorkflow(content_root, store_path.parent / 'record-workflow')
        if not production:
            records.migrate_news_pilot(store)
        app.config['CONTENT_WORKFLOW'] = records
        app.config['CONTENT_RECORD_HELPERS'] = record_helpers or {}
    app.config.update(
        NEWS_EDITOR_STORE=store,
        NEWS_EDITOR_ACCOUNTS=accounts,
        ADMIN_HANDOFF_ENABLED=full_admin,
        ADMIN_HANDOFF_PREVIEW=not production,
        MAX_CONTENT_LENGTH=24 * 1024 * 1024 if full_admin else MAX_IMAGE_BYTES + 1024 * 1024,
        SESSION_COOKIE_NAME='session' if production else 'opticom_news_preview',
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_SAMESITE='Lax',
    )

    @app.before_request
    def authorize_admin_request():
        admin_path = _is_legacy_admin_path()
        editor_path = _is_editor_path()
        host = request.host.partition(':')[0].lower()
        if production and (admin_path or editor_path) and host != admin_host:
            abort(403)
        if not production and (request.remote_addr != '127.0.0.1' or host not in ('localhost', '127.0.0.1')):
            abort(403)
        if not admin_path:
            return None
        entry = request.path in ('/admin', '/admin/') and request.method in ('GET', 'HEAD')
        if not app.config['ADMIN_HANDOFF_ENABLED']:
            if entry:
                return redirect(url_for('news_editor.dashboard'))
            abort(403)
        role = _role()
        if role is None:
            return redirect(url_for('news_editor.login'))
        if request.path == '/admin/' and entry:
            return redirect(url_for('admin_dashboard'))
        if app.config.get('CONTENT_WORKFLOW') is None:
            if role == 'editor' and entry:
                return redirect(url_for('news_editor.dashboard'))
            if (role != 'owner' or request.endpoint not in OWNER_ADMIN_READ_ENDPOINTS
                    or request.method not in ('GET', 'HEAD') or 'action' in request.args):
                abort(403)
        else:
            _authorize_legacy_request(role)
        g.handoff_admin_access = True
        g.handoff_role = role

    @app.after_request
    def protect_legacy_admin_responses(response):
        if _is_legacy_admin_path() or _is_editor_path() or session.get('news_visitor_view'):
            return protect_private_responses(response)
        return response

    @app.context_processor
    def admin_navigation_context():
        return {
            'preview_csrf_token': _csrf_token,
            'preview_visitor_mode': bool(session.get('news_visitor_view') and _account_role()),
            'admin_role': _role(),
        }

    app.register_blueprint(editor)


def _is_legacy_admin_path():
    return request.path == '/admin' or request.path.startswith('/admin/')


def _is_editor_path():
    return request.path == '/editor' or request.path.startswith('/editor/')


def _authorize_legacy_request(role):
    endpoint = request.endpoint
    if 'action' in request.args:
        abort(403)
    if request.method in ('GET', 'HEAD'):
        readable = OWNER_ADMIN_READ_ENDPOINTS if role == 'owner' else EDITOR_ADMIN_READ_ENDPOINTS
        if endpoint in readable:
            return
        abort(403)

    if request.method != 'POST':
        abort(405)
    _require_csrf()
    action = request.form.get('action')
    if endpoint == 'admin_content' and action in CONTENT_ACTIONS:
        if action == 'edit_site_welcome':
            _require_fields(HOME_FIELDS)
        elif action == 'edit_join_us':
            directions = {field for field in request.form if re.fullmatch(
                r'admission_direction_(title|summary)_[1-9][0-9]*(_zh)?|admission_direction_(title|summary)_zh_[1-9][0-9]*', field,
            )}
            _require_fields(JOIN_FIELDS | directions)
        elif request.form.get('item_type') not in ('article', 'person', 'news'):
            abort(400)
        return
    if endpoint in RECORD_UPLOADS:
        return
    if role == 'owner' and (
        (endpoint == 'admin_settings' and action in SITE_WRITES)
        or (endpoint == 'admin_analytics' and action in ANALYTICS_WRITES)
        or endpoint in ('admin_upload_site_image', 'admin_upload_file', 'upload_render_data_zip')
    ):
        return
    abort(403)


def _require_fields(fields):
    if set(request.form) - fields - {'action', 'item_type', 'csrf_token'}:
        abort(403, '此表单提交了超出授权范围的站点字段。')


def _store():
    return current_app.config['NEWS_EDITOR_STORE']


def news_for_display(values):
    # The public layout defaults to English. Keep a Chinese-only update readable there.
    story = values.copy()
    for key in ('title', 'summary', 'content'):
        if not story.get(key):
            story[key] = story.get(f'{key}_zh', '')
    return story


def send_news_image(image_data):
    if image_data is None:
        abort(404)
    data, mime_type = image_data
    response = send_file(BytesIO(data), mimetype=mime_type, max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def _fingerprint(password_hash):
    return hashlib.sha256(password_hash.encode('utf-8')).hexdigest()


def _account_role():
    role = session.get('news_role')
    password_hash = current_app.config['NEWS_EDITOR_ACCOUNTS'].get(role)
    if (password_hash and session.get('news_valid_until', 0) > time.time()
            and secrets.compare_digest(session.get('news_login_fingerprint', ''), _fingerprint(password_hash))):
        return role
    return None


def _role():
    return None if session.get('news_visitor_view') else _account_role()


def _editors_only(view):
    @wraps(view)
    def secured(*args, **kwargs):
        if _role() is None:
            if request.endpoint == 'news_editor.preview':
                return redirect(url_for('news_editor.login', preview_id=kwargs['news_id']))
            if request.endpoint == 'news_editor.record_preview':
                return redirect(url_for('news_editor.login', record_kind=kwargs['kind'], record_id=kwargs['record_id']))
            return redirect(url_for('news_editor.login'))
        return view(*args, **kwargs)

    return secured


def _csrf_token():
    if 'news_csrf_token' not in session:
        session['news_csrf_token'] = secrets.token_urlsafe(32)
    return session['news_csrf_token']


def _editor_image_url(filename):
    if filename and filename.startswith('managed-'):
        return url_for('news_editor.draft_image', filename=filename)
    if filename:
        return url_for('asset_news_image', filename=filename)
    return url_for('static', filename='images/placeholder-news.svg')


@editor.context_processor
def editor_helpers():
    return {
        'editor_csrf_token': _csrf_token,
        'editor_role': _role(),
        'editor_image_url': _editor_image_url,
    }


@editor.before_request
def check_form_token():
    if request.method == 'POST' and request.endpoint not in ('news_editor.upload_draft', 'news_editor.upload_paper_draft'):
        _require_csrf()


def _require_csrf():
    provided = request.form.get('csrf_token', '').encode('utf-8')
    expected = session.get('news_csrf_token', '').encode('utf-8')
    if not expected or not secrets.compare_digest(provided, expected):
        abort(400, '表单已失效，请返回后台刷新后重试。')


@editor.after_request
def protect_private_responses(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@editor.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    message = '上传体积过大，请检查附件上限后重试。'
    if request.endpoint in ('news_editor.upload_draft', 'news_editor.upload_paper_draft'):
        return jsonify(error=message), 413
    return message, 413


@editor.route('/login', methods=['GET', 'POST'])
def login():
    preview_id = request.args.get('preview_id', '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', preview_id):
        preview_id = None
    record_kind = request.args.get('record_kind', '')
    record_id = request.args.get('record_id', '')
    next_record = None
    if record_kind in RECORDS and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', record_id):
        next_record = (record_kind, record_id)
    login_target = dict(preview_id=preview_id, record_kind=record_kind if next_record else None,
                        record_id=record_id if next_record else None)
    if request.method != 'POST':
        role = _role()
        if role:
            return redirect(_editor_destination(role, preview_id, next_record))
        return render_template('news_editor/login.html', **login_target)
    username = request.form.get('username', '').strip().lower()
    password = request.form.get('password', '')
    password_hash = current_app.config['NEWS_EDITOR_ACCOUNTS'].get(username)
    if not password_hash or not password or len(password) > 256 or not check_password_hash(password_hash, password):
        return render_template(
            'news_editor/login.html', error='账号或密码有误，请重试。', username=username, **login_target,
        ), 401
    session.pop('news_csrf_token', None)
    session.pop('news_visitor_view', None)
    session.pop('is_admin', None)
    session.pop('admin_shadow', None)
    session['admin_login_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    session['news_role'] = username
    session['news_login_fingerprint'] = _fingerprint(password_hash)
    session['news_valid_until'] = int(time.time()) + SESSION_SECONDS
    return redirect(_editor_destination(username, preview_id, next_record), code=303)


def _editor_destination(role, preview_id, next_record=None):
    if next_record and current_app.config.get('CONTENT_WORKFLOW') is not None:
        return url_for('news_editor.record_preview', kind=next_record[0], record_id=next_record[1])
    if preview_id:
        return url_for('news_editor.preview', news_id=preview_id)
    return url_for('admin_dashboard' if current_app.config.get('CONTENT_WORKFLOW') is not None
                   or role == 'owner' and current_app.config['ADMIN_HANDOFF_ENABLED']
                   else 'news_editor.dashboard')


@editor.post('/logout')
def logout():
    for name in ('news_role', 'news_login_fingerprint', 'news_valid_until', 'news_csrf_token', 'news_visitor_view', 'admin_login_at'):
        session.pop(name, None)
    return redirect(url_for('news_editor.login'), code=303)


@editor.post('/view-as-visitor')
@_editors_only
def view_as_visitor():
    if not current_app.config['ADMIN_HANDOFF_ENABLED']:
        abort(404)
    session['news_visitor_view'] = True
    return redirect(url_for('index'), code=303)


@editor.post('/return-from-visitor')
def return_from_visitor():
    role = _account_role()
    if not current_app.config['ADMIN_HANDOFF_ENABLED'] or not session.get('news_visitor_view') or role is None:
        abort(403)
    session.pop('news_visitor_view', None)
    return redirect(_editor_destination(role, None), code=303)


@editor.get('/')
@_editors_only
def dashboard():
    if current_app.config.get('CONTENT_WORKFLOW') is not None:
        return redirect(url_for('admin_dashboard'))
    return render_template('news_editor/dashboard.html', entries=_store().list_entries())


def _existing_entry(news_id):
    entry = _store().get_entry(news_id)
    if entry is None:
        abort(404, '找不到这条新闻。')
    return entry


def _uploaded_image():
    upload = request.files.get('image')
    if upload is None or not upload.filename:
        return None
    return upload.stream.read(MAX_IMAGE_BYTES + 1)


def _edit_form(entry=None):
    initial = entry['draft'] if entry else {'date': date.today().isoformat()}
    if request.method == 'GET':
        return render_template('news_editor/form.html', values=initial, entry=entry)

    values = {key: request.form.get(key, '') for key in TEXT_FIELDS}
    values.update({key: key in request.form for key in FLAG_FIELDS})
    try:
        saved, _created = _store().save_draft(
            values,
            _uploaded_image(),
            news_id=entry['id'] if entry else None,
            expected_revision=request.form.get('revision', type=int) if entry else None,
        )
    except StoreError as error:
        submitted = {**initial, **values}
        # Keep a stale revision stale. Reopening the draft is an explicit step after a conflict.
        stale_entry = {**entry, 'revision': request.form.get('revision', '')} if entry else None
        return render_template(
            'news_editor/form.html', values=submitted, entry=stale_entry, error=str(error),
        ), error.status
    flash('草稿已保存，可以预览。')
    return redirect(url_for('news_editor.edit', news_id=saved['id']), code=303)


@editor.route('/news/new', methods=['GET', 'POST'])
@_editors_only
def create():
    if current_app.config.get('CONTENT_WORKFLOW') is not None:
        return redirect(url_for('admin_content', section='news', new=1))
    return _edit_form()


@editor.route('/news/<news_id>', methods=['GET', 'POST'])
@_editors_only
def edit(news_id):
    if current_app.config.get('CONTENT_WORKFLOW') is not None:
        return redirect(_record_edit_url('news', news_id))
    return _edit_form(_existing_entry(news_id))


@editor.get('/news/<news_id>/preview')
@_editors_only
def preview(news_id):
    if current_app.config.get('CONTENT_WORKFLOW') is not None:
        return record_preview('news', news_id)
    entry = _existing_entry(news_id)
    item = news_for_display(entry['draft'])
    return render_template('news_detail.html', item=item, preview_entry=entry)


@editor.get('/draft-images/<filename>')
@_editors_only
def draft_image(filename):
    if current_app.config.get('CONTENT_WORKFLOW') is not None:
        abort(404)
    return send_news_image(_store().get_image(filename, include_drafts=True))


@editor.post('/news/<news_id>/publication')
@_editors_only
def change_publication(news_id):
    if current_app.config.get('CONTENT_WORKFLOW') is not None:
        return record_action('news', news_id)
    action = request.form.get('action')
    if action not in ('publish', 'withdraw'):
        abort(400, '未知的发布操作。')
    try:
        _store().set_publication(
            news_id, publish=action == 'publish', expected_revision=request.form.get('revision', type=int),
        )
    except StoreError as error:
        entry = _existing_entry(news_id)
        if action == 'publish':
            return render_template(
                'news_detail.html', item=news_for_display(entry['draft']), preview_entry=entry,
                preview_error=str(error),
            ), error.status
        return render_template('news_editor/form.html', values=entry['draft'], entry=entry, error=str(error)), error.status
    flash('已发布到本机预览站，可以打开公开页面检查。' if action == 'publish'
          else '已撤回。公开页面已下线，草稿仍可编辑。')
    return redirect(url_for('news_editor.edit', news_id=news_id), code=303)


@editor.route('/access', methods=['GET', 'POST'])
@_editors_only
def access():
    if _role() != 'owner':
        abort(403, '只有站点所有者可以管理脚本凭证。')
    issued_token = None
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'issue':
            issued_token = secrets.token_urlsafe(32)
            _store().set_token(_fingerprint(issued_token))
        elif action == 'revoke':
            _store().revoke_token()
            flash('脚本凭证已撤销。')
            return redirect(url_for('news_editor.access'), code=303)
        else:
            abort(400, '未知的凭证操作。')
    return render_template(
        'news_editor/access.html', issued_token=issued_token, token_configured=bool(_store().get_token()),
    )


RECORDS = {
    'article': ('publications', 'articles', 'article_detail.html', 'thumbnail'),
    'person': ('people', 'people', 'person_detail.html', 'photo'),
    'news': ('news', 'news_items', 'news_detail.html', 'image'),
}


def _workflow():
    workflow = current_app.config.get('CONTENT_WORKFLOW')
    if workflow is None:
        abort(404)
    return workflow


def _record_edit_url(kind, record_id):
    return url_for('admin_content', section=RECORDS[kind][0], draft=record_id)


def _record_entry(kind, record_id):
    if kind not in RECORDS:
        abort(404)
    entry = _workflow().get(kind, record_id)
    if entry is None:
        abort(404, '找不到该条内容。')
    return entry


def add_workflow_admin_context(context):
    workflow = _workflow()
    rows = workflow.list()
    target = request.args.get('draft')
    edit = None
    if target:
        kind = next((key for key, value in RECORDS.items() if value[0] == request.args.get('section')), None)
        if kind:
            edit = _record_entry(kind, target)
            collection = RECORDS[kind][1]
            record = edit['draft']
            context[collection] = [record] + [item for item in context[collection] if item['id'] != target]
    context.update(draft_rows=rows, draft_edit=edit, draft_revisions={row['id']: row['revision'] for row in rows})


def _file_input(field, limit):
    upload = request.files.get(field)
    if upload is None or not upload.filename:
        return None
    return upload.filename, upload.stream.read(limit + 1)


def _attachment_inputs():
    return [(file.filename, file.stream.read(20 * 1024 * 1024 + 1))
            for file in request.files.getlist('attachments') if file.filename]


def save_legacy_record():
    from content_workflow import WorkflowError
    from record_forms import FormError, from_form

    kind = request.form['item_type']
    record_id = request.form.get('id') if request.form['action'] == 'edit' else None
    if request.form['action'] == 'edit' and not record_id:
        abort(400, '缺少需要编辑的条目编号。')
    entry = _record_entry(kind, record_id) if record_id else None
    helpers = current_app.config['CONTENT_RECORD_HELPERS']
    try:
        record = from_form(kind, request.form, entry['draft'] if entry else None,
                           resolve_article_flags=helpers['resolve_article_flags'],
                           normalize_person_tags=helpers['normalize_person_tags'])
        result, _created = _workflow().save(
            kind, record, record_id=record_id, expected_revision=request.form.get('revision', 0, type=int),
            image=_file_input(RECORDS[kind][3], MAX_IMAGE_BYTES),
            attachments=_attachment_inputs() if kind == 'article' else (),
        )
    except (FormError, WorkflowError) as error:
        abort(error.status, str(error))
    session['admin_notice'] = {'kind': 'success', 'message': '草稿已保存，可以预览。'}
    return redirect(_record_edit_url(kind, result['id']), code=303)


def save_legacy_asset(kind, record_id, field):
    from content_workflow import WorkflowError

    entry = _record_entry(kind, record_id)
    image = _file_input(field, MAX_IMAGE_BYTES)
    if image is None:
        abort(400, '请选择需要上传的图片。')
    try:
        revision = request.form.get('revision', type=int)
        _workflow().save(kind, entry['draft'], record_id=record_id, expected_revision=revision, image=image)
    except WorkflowError as error:
        abort(error.status, str(error))
    return redirect(url_for('news_editor.record_preview', kind=kind, record_id=record_id), code=303)


def _render_record_preview(entry, error=None):
    kind = entry['kind']
    item = entry['draft']
    if kind == 'news':
        item = news_for_display(item)
    return render_template(RECORDS[kind][2], item=item, person=item, preview_record=entry, preview_error=error, open_count=0)


@editor.get('/records/<kind>/<record_id>/preview')
@_editors_only
def record_preview(kind, record_id):
    return _render_record_preview(_record_entry(kind, record_id))


@editor.get('/records/<kind>/<record_id>/files/<filename>')
@_editors_only
def record_file(kind, record_id, filename):
    if kind not in RECORDS:
        abort(404)
    path = _workflow().private_file(kind, record_id, filename)
    if path is None:
        abort(404)
    return send_file(path, max_age=0)


@editor.post('/records/<kind>/<record_id>/transition')
@_editors_only
def record_action(kind, record_id):
    from content_workflow import WorkflowError
    from record_forms import FormError, validate_for_publish

    entry = _record_entry(kind, record_id)
    action = request.form.get('action')
    messages = {'publish': '已发布到本机预览。', 'withdraw': '已撤回，草稿仍可编辑。',
                'archive': '已移入回收区。', 'restore': '已从回收区恢复为草稿。',
                'rebase': '已确认最新公开版本，可以继续修改草稿。'}
    if action not in messages:
        abort(400)
    if action == 'restore' and _role() != 'owner':
        abort(403)

    def normalize_carousel(items, _record_id):
        current_app.config['CONTENT_RECORD_HELPERS']['normalize_carousel'](items)

    try:
        if action == 'publish':
            validate_for_publish(kind, entry['draft'])
        revision = request.form.get('revision', type=int)
        if action == 'rebase':
            _workflow().rebase(kind, record_id, revision)
        else:
            _workflow().transition(
                kind, record_id, action, revision,
                normalize=normalize_carousel if kind == 'article' and action == 'publish' else None,
                attachments=request.form.getlist('published_attachments') if action == 'publish' else None,
            )
    except (FormError, WorkflowError) as error:
        return _render_record_preview(_record_entry(kind, record_id), str(error)), error.status
    session['admin_notice'] = {'kind': 'success', 'message': messages[action]}
    destination = url_for('admin_content', section=RECORDS[kind][0]) if action == 'archive' else _record_edit_url(kind, record_id)
    return redirect(destination, code=303)


@editor.post('/api/v1/news/drafts')
def upload_draft():
    auth = re.fullmatch(r'Bearer ([A-Za-z0-9_-]{43})', request.headers.get('Authorization', ''), re.IGNORECASE)
    active_digest = _store().get_token()
    if auth is None or not active_digest or not secrets.compare_digest(_fingerprint(auth[1]), active_digest):
        return jsonify(error='请使用有效的草稿凭证。'), 401

    submission_key = request.headers.get('Idempotency-Key', '')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', submission_key):
        return jsonify(error='请提供有效的 Idempotency-Key，同一次提交重试时使用相同的值。'), 400

    image = None
    if request.is_json:
        try:
            values = request.get_json(silent=True)
        except RecursionError:
            return jsonify(error='新闻数据须为一层 JSON 对象。'), 400
    elif request.mimetype == 'multipart/form-data':
        try:
            values = json.loads(request.form.get('payload', ''))
        except (ValueError, RecursionError):
            return jsonify(error='payload 必须是 JSON 对象。'), 400
        image = _uploaded_image()
    else:
        return jsonify(error='请发送 JSON，或用 multipart/form-data 上传 JSON 和图片。'), 415

    if not isinstance(values, dict):
        return jsonify(error='新闻数据必须是 JSON 对象。'), 400
    if current_app.config.get('CONTENT_WORKFLOW') is not None:
        return _upload_workflow_news(values, image, submission_key)
    try:
        entry, created = _store().save_draft(values, image, submission_key=submission_key)
    except StoreError as error:
        return jsonify(error=str(error)), error.status

    news_id = entry['id']
    return jsonify(
        id=news_id, created=created, edit_url=url_for('news_editor.edit', news_id=news_id),
        preview_url=url_for('news_editor.preview', news_id=news_id),
    ), 201 if created else 200


def _request_digest(values, files):
    details = [values, [(name, hashlib.sha256(body).hexdigest()) for name, body in files]]
    joined = json.dumps(details, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return _fingerprint(joined)


def _upload_workflow_news(values, image, submission_key):
    from content_workflow import WorkflowError
    from record_forms import FormError, from_form

    if set(values) - set(TEXT_FIELDS) - set(FLAG_FIELDS):
        return jsonify(error='新闻草稿包含未知字段。'), 400
    if any(not isinstance(values[key], str) for key in values.keys() & set(TEXT_FIELDS)):
        return jsonify(error='新闻文字字段必须是文本。'), 400
    form_values = {key: value for key, value in values.items() if key in TEXT_FIELDS}
    for key in values.keys() & set(FLAG_FIELDS):
        setting = str(values[key]).strip().lower()
        if setting not in ('1', '0', 'on', 'off', 'true', 'false', ''):
            return jsonify(error='置顶和首页展示须为布尔值。'), 400
        if setting in ('1', 'on', 'true'):
            form_values[key] = 'on'
    helpers = current_app.config['CONTENT_RECORD_HELPERS']
    picture = ('agent.png', image) if image is not None else None
    try:
        record = from_form('news', MultiDict(form_values), resolve_article_flags=helpers['resolve_article_flags'],
                           normalize_person_tags=helpers['normalize_person_tags'])
        entry, created = _workflow().save(
            'news', record, image=picture, submission_key='news:' + _fingerprint(submission_key),
            submission_digest=_request_digest(values, [picture] if picture else []),
        )
    except (FormError, WorkflowError, ValueError) as error:
        return jsonify(error=str(error)), getattr(error, 'status', 400)
    news_id = entry['id']
    return jsonify(id=news_id, created=created, edit_url=url_for('news_editor.edit', news_id=news_id),
                   preview_url=url_for('news_editor.preview', news_id=news_id)), 201 if created else 200


@editor.post('/api/v1/papers/drafts')
def upload_paper_draft():
    from content_workflow import WorkflowError
    from record_forms import FormError, from_paper_payload, paper_doi

    workflow = _workflow()
    auth = re.fullmatch(r'Bearer ([A-Za-z0-9_-]{43})', request.headers.get('Authorization', ''), re.IGNORECASE)
    active_digest = _store().get_token()
    if auth is None or not active_digest or not secrets.compare_digest(_fingerprint(auth[1]), active_digest):
        return jsonify(error='请使用有效的草稿凭证。'), 401
    key = request.headers.get('Idempotency-Key', '')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', key):
        return jsonify(error='须提供有效的 Idempotency-Key。'), 400
    existing = None
    try:
        if request.is_json:
            payload = request.get_json(silent=True)
        elif request.mimetype == 'multipart/form-data':
            payload = json.loads(request.form.get('payload', ''))
        else:
            return jsonify(error='请发送 JSON，或通过 multipart/form-data 发送稿件和 PDF。'), 415
        attachments = _attachment_inputs()
        picture = _file_input('thumbnail', MAX_IMAGE_BYTES)
        candidate = from_paper_payload(payload)
        doi = paper_doi(candidate)
        existing = next((item for item in workflow.published('article') if doi and paper_doi(item) == doi), None)
        record = from_paper_payload(payload, existing) if existing else candidate
        entry, created = workflow.save(
            'article', record, record_id=existing['id'] if existing else None, image=picture,
            attachments=attachments, submission_key='paper:' + _fingerprint(key),
            submission_digest=_request_digest(payload, attachments + ([picture] if picture else [])),
            unique_match=(lambda other: paper_doi(other) == doi) if doi else None,
        )
    except (FormError, WorkflowError) as error:
        message = str(error)
        if error.status == 409 and existing:
            message += ' 已登记论文 ' + existing['id'] + '，请到后台核对已有稿件。'
        return jsonify(error=message), error.status
    except (ValueError, RecursionError):
        return jsonify(error='论文数据必须是有效的 JSON 对象。'), 400
    item_id = entry['id']
    return jsonify(id=item_id, created=created, edit_url=_record_edit_url('article', item_id),
                   preview_url=url_for('news_editor.record_preview', kind='article', record_id=item_id),
                   attachments=entry['attachments']), 201 if created else 200
