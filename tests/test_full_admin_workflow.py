import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from zipfile import ZipFile

from werkzeug.datastructures import MultiDict

from scripts.preview_content_git import offline_git_env
from scripts.restore_handoff_backup import restore_handoff_backup
from scripts.run_news_preview import prepare_preview


ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / 'site_content/images/news/news_003.jpg'
SVG = ROOT / 'site_content/images/friend_link_mail.svg'
PDF_ONE = b'%PDF-1.4\n% first attachment fixture\n%%EOF\n'
PDF_TWO = b'%PDF-1.4\n% second attachment fixture\n%%EOF\n'


def field_value(response, name):
    return re.search(rf'name="{name}" value="([^"]*)"', response.get_data(as_text=True))[1]


def isolated_app(state_dir, module_name, *, production=False):
    with patch.dict(os.environ):
        credentials = prepare_preview(state_dir)
        passwords = json.loads(credentials.read_text(encoding='utf-8'))
        os.environ['ADMIN_HANDOFF_PREVIEW'] = '1'
        if production:
            os.environ.pop('NEWS_EDITOR_PREVIEW')
            os.environ.pop('ADMIN_HANDOFF_PREVIEW')
            os.environ.update(
                APP_MODE='production',
                ADMIN_HANDOFF_PRODUCTION='1',
                ADMIN_HANDOFF_HOST='lab.example.edu',
            )
        spec = importlib.util.spec_from_file_location(module_name, ROOT / 'app.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    return module, passwords


class FullAdminWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state = tempfile.TemporaryDirectory(prefix='full-admin-test-')
        cls.addClassCleanup(state.cleanup)
        module, cls.passwords = isolated_app(Path(state.name), 'full_admin_test_app')
        cls.module = module
        cls.app = module.app
        cls.content = Path(module.CONTENT_ROOT)

    def sign_in(self, role, target='/editor/login', *, app=None, passwords=None):
        client = (app or self.app).test_client()
        response = client.post(target, data={
            'username': role, 'password': (passwords or self.passwords)[role],
            'csrf_token': field_value(client.get(target), 'csrf_token'),
        })
        self.assertEqual(response.status_code, 303)
        return client, response

    def admin_form(self, client, page='/admin/content'):
        response = client.get(page)
        self.assertEqual(response.status_code, 200)
        return field_value(response, 'csrf_token')

    def assert_download(self, client, path, mime, content=None):
        with client.get(path) as response:
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.mimetype, mime)
            if content is not None:
                self.assertEqual(response.get_data(), content)

    def test_workspaces_share_content_and_analytics_but_not_operations(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        reader = self.app.test_client()
        shared = ('/admin/content', '/admin/assets', '/admin/settings', '/admin/analytics')
        for url in ('/admin', *shared):
            with self.subTest(url=url):
                for client in (owner, editor):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('no-store', response.headers['Cache-Control'])
                self.assertEqual(reader.get(url).location, '/editor/login')

        for role, client in (('owner', owner), ('editor', editor)):
            with self.subTest(role=role):
                dashboard = client.get('/admin').get_data(as_text=True)
                for url in shared:
                    self.assertIn(f'href="{url}"', dashboard)
                self.assertEqual('href="/admin/operations"' in dashboard, role == 'owner')
                self.assertNotIn('href="/admin/download-handoff.zip"', dashboard)

                analytics = client.get('/admin/analytics').get_data(as_text=True)
                for url in ('/admin/download-logs.csv', '/admin/download-page-views.csv', '/admin/download-render-data.zip'):
                    self.assertIn(f'href="{url}"', analytics)
                for action in ('publish_content_to_github', 'sync_content_from_github', 'clear_supabase_analytics'):
                    self.assertNotIn(f'value="{action}"', analytics)

        for url in ('/admin/operations', '/admin/download-site-content.zip', '/admin/download-handoff.zip', '/editor/access'):
            with self.subTest(url=url):
                self.assertEqual(editor.get(url).status_code, 403)
        self.assertEqual(reader.get('/admin/operations').location, '/editor/login')
        operations = owner.get('/admin/operations')
        self.assertEqual(operations.status_code, 200)
        ops_html = operations.get_data(as_text=True)
        self.assertIn('href="/editor/access"', ops_html)
        self.assertIn('href="/admin/download-handoff.zip"', ops_html)
        owner_csrf = field_value(operations, 'csrf_token')
        editor_csrf = self.admin_form(editor)
        for action in ('publish_content_to_github', 'sync_content_from_github', 'clear_supabase_analytics', 'edit_site_version'):
            self.assertIn(f'value="{action}"', ops_html)
            self.assertEqual(editor.post('/admin/operations', data={'action': action, 'csrf_token': editor_csrf}).status_code, 403)
            self.assertEqual(owner.post('/admin/analytics', data={'action': action, 'csrf_token': owner_csrf}).status_code, 403)
        self.assertEqual(editor.post('/editor/access', data={'action': 'issue', 'csrf_token': editor_csrf}).status_code, 403)
        with patch.object(self.module, '_clear_supabase_analytics_tables', return_value={'kind': 'success'}) as clear:
            clear_action = {'action': 'clear_supabase_analytics', 'csrf_token': owner_csrf}
            response = owner.post('/admin/operations', data=clear_action)
            self.assertIn(response.status_code, (302, 303))
            self.assertTrue(response.location.startswith('/admin/operations'))
            clear.assert_not_called()
            response = owner.post('/admin/operations', data=dict(clear_action, clear_confirmation='CLEAR'))
            self.assertIn(response.status_code, (302, 303))
            clear.assert_called_once_with()

        with patch.object(self.module, '_load_resource_open_rows', side_effect=AssertionError('content page loaded analytics')):
            content_page = editor.get('/admin/content').get_data(as_text=True)
        self.assertNotIn('Tracked opens:', content_page)
        self.assertIn('Tracked opens:', owner.get('/admin/content').get_data(as_text=True))
        with ZipFile(BytesIO(owner.get('/admin/download-site-content.zip').data)) as backup:
            self.assertTrue({'articles.json', 'people.json', 'news.json', 'site.json'} <= set(backup.namelist()))

        with patch.object(self.module, '_load_resource_open_rows', return_value=[
            {'time': '2026-11-26 16:40:00', 'name': 'Preview visitor', 'affiliation': 'Local fixture',
             'email': 'visitor@example.test', 'resource_id': 'art_001', 'article_title': 'Fixture paper',
             'type': 'article', 'target_url': 'https://example.test/paper'},
        ]):
            for client in (owner, editor):
                with client.get('/admin/download-logs.csv') as downloaded:
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.mimetype, 'text/csv')
                    self.assertIn('Preview visitor,Local fixture,visitor@example.test', downloaded.get_data(as_text=True))
                self.assert_download(client, '/admin/download-page-views.csv', 'text/csv')
                with client.get('/admin/download-render-data.zip') as downloaded:
                    self.assertEqual(downloaded.status_code, 200)
                    with ZipFile(BytesIO(downloaded.get_data())) as bundle:
                        self.assertEqual(set(bundle.namelist()), {'resource_opens.csv', 'page_views.csv'})
                        self.assertIn(b'visitor@example.test', bundle.read('resource_opens.csv'))
        self.assertEqual(reader.get('/admin/download-logs.csv').location, '/editor/login')

    def test_editor_can_change_display_settings_but_only_owner_can_set_version(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        config_path = self.content / 'site.json'
        original = config_path.read_bytes()
        try:
            csrf = self.admin_form(editor)
            update = {'action': 'edit_site_welcome', 'home_note': 'Editor-managed home note'}
            self.assertEqual(editor.post('/admin/content', data=update).status_code, 400)
            escalation = dict(update, csrf_token=csrf, lab_name_short='Blocked setting')
            self.assertEqual(editor.post('/admin/content', data=escalation).status_code, 403)
            for client in (owner, editor):
                settings = client.get('/admin/settings')
                self.assertEqual(settings.status_code, 200)
                self.assertNotIn('name="site_version"', settings.get_data(as_text=True))
                settings_csrf = field_value(settings, 'csrf_token')
                self.assertEqual(client.post('/admin/settings', data={
                    'action': 'edit_site_branding', 'csrf_token': settings_csrf,
                    'lab_name_short': 'Blocked setting', 'site_version': '8.8.8',
                }).status_code, 403)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertIn(editor.post('/admin/content', data=dict(update, csrf_token=csrf)).status_code, (302, 303))
            self.assertIn(editor.post('/admin/content', data={
                'action': 'edit_join_us', 'csrf_token': csrf, 'admissions_title': 'Editor-managed recruiting',
                'admission_direction_title_1': 'Research assistant',
            }).status_code, (302, 303))
            self.assertEqual(editor.get('/admin/content?action=delete&item_type=news&id=news_003').status_code, 403)

            self.assertIn(editor.post('/admin/settings', data={
                'action': 'edit_site_branding', 'csrf_token': csrf, 'footer_copyright': 'Editor branding',
            }).status_code, (302, 303))
            owner_csrf = self.admin_form(owner, '/admin/operations')
            self.assertIn(owner.post('/admin/settings', data={
                'action': 'edit_site_branding', 'csrf_token': owner_csrf, 'lab_name_short': 'Owner branding',
            }).status_code, (302, 303))
            changed = owner.post('/admin/operations', data={
                'action': 'edit_site_version', 'csrf_token': owner_csrf, 'site_version': '2.9.5',
            })
            self.assertIn(changed.status_code, (302, 303))
            self.assertTrue(changed.location.startswith('/admin/operations'))
            config = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertEqual(config['home_note'], 'Editor-managed home note')
            self.assertEqual(config['admissions_title'], 'Editor-managed recruiting')
            self.assertEqual(config['lab_name_short'], 'Owner branding')
            self.assertEqual(config['footer_copyright'], 'Editor branding')
            self.assertEqual(config['site_version'], '2.9.5')
        finally:
            config_path.write_bytes(original)

    def test_production_editor_uses_the_official_host_and_persists_drafts(self):
        with tempfile.TemporaryDirectory(prefix='handoff-prod-test-') as tmp:
            state = Path(tmp)
            module, passwords = isolated_app(state, 'handoff_production_test', production=True)
            https = 'https://lab.example.edu'
            anon = module.app.test_client()
            self.assertEqual(anon.get('/').status_code, 200)
            self.assertEqual(anon.get('/admin', base_url='http://192.0.2.1').status_code, 403)
            self.assertEqual(anon.get('/editor/login', base_url='https://other.example.edu').status_code, 403)
            self.assertEqual(anon.get('/admin', base_url=https).location, '/editor/login')
            self.assertEqual(anon.head('/admin', base_url=https).location, '/editor/login')
            self.assertEqual(anon.head('/editor/login', base_url=https).status_code, 200)
            for role in ('editor', 'owner'):
                with self.subTest(role=role):
                    client = module.app.test_client()
                    login = client.get('/editor/login', base_url=https)
                    self.assertNotIn('本机预览', login.get_data(as_text=True))
                    signed_in = client.post('/editor/login', base_url=https, data={
                        'username': role, 'password': passwords[role], 'csrf_token': field_value(login, 'csrf_token'),
                    })
                    self.assertEqual(signed_in.location, '/admin')
                    self.assertIn('Secure; HttpOnly;', signed_in.headers['Set-Cookie'])
                    self.assertIn('SameSite=Lax', signed_in.headers['Set-Cookie'])
                    self.assertEqual(client.get('/admin/settings', base_url=https).status_code, 200)
                    self.assertEqual(client.get('/admin/analytics', base_url=https).status_code, 200)
                    self.assertEqual(client.get('/admin/operations', base_url=https).status_code,
                                     200 if role == 'owner' else 403)
                    if role == 'editor':
                        content = client.get('/admin/content', base_url=https)
                        created = client.post('/admin/content', base_url=https, data={
                            'action': 'add', 'item_type': 'news', 'title_zh': '正式模式草稿验证',
                            'csrf_token': field_value(content, 'csrf_token'),
                        })
                        self.assertEqual(created.status_code, 303)
                        self.assertEqual(anon.get(created.location, base_url=https).status_code, 302)
                        record_id = parse_qs(urlsplit(created.location).query)['draft'][0]
                        preview_path = f'/editor/records/news/{record_id}/preview'
                        self.assertEqual(anon.get(preview_path, base_url=https).status_code, 302)
                        public_path = f'/news/{record_id}'
                        self.assertEqual(anon.get(public_path, base_url=https).status_code, 404)
            restarted, _ = isolated_app(state, 'handoff_production_restart_test', production=True)
            draft = restarted.app.config['CONTENT_WORKFLOW'].get('news', record_id)
            self.assertEqual(draft['draft']['title_zh'], '正式模式草稿验证')
            self.assertIsNone(draft['published'])

    def test_legacy_image_upload_tools_preserve_the_published_record(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        reader = self.app.test_client()
        editor_csrf = self.admin_form(editor, '/admin/assets')
        owner_csrf = self.admin_form(owner, '/admin/assets')
        image = IMAGE.read_bytes()
        logo_url = '/admin/upload-site-image/logo'
        self.assertIn(editor.post(logo_url, data={
            'csrf_token': editor_csrf, 'image': (BytesIO(image), IMAGE.name),
        }).status_code, (302, 303))
        configured = json.loads((self.content / 'site.json').read_text(encoding='utf-8'))
        logo_path = '/assets/site/' + configured['logo_filename']
        self.assert_download(reader, logo_path, 'image/jpeg', image)
        svg = SVG.read_bytes()
        for filename, body in ((SVG.name, svg), (IMAGE.name, svg)):
            with self.subTest(filename=filename):
                rejected = editor.post(logo_url, data={'csrf_token': editor_csrf, 'image': (BytesIO(body), filename)})
                self.assertIn(rejected.status_code, (400, 403))
                self.assert_download(reader, logo_path, 'image/jpeg', image)
        self.assertIn(owner.post('/admin/upload-site-image/friend-1', data={
            'csrf_token': owner_csrf, 'image': (BytesIO(svg), SVG.name),
        }).status_code, (302, 303))
        footer_icon = json.loads((self.content / 'site.json').read_text(encoding='utf-8'))['friend_links'][0]['image_filename']
        self.assert_download(reader, '/assets/site/' + footer_icon, 'image/svg+xml', svg)

        article = next(row for row in json.loads((self.content / 'articles.json').read_text(encoding='utf-8'))
                       if row.get('thumbnail_filename'))
        identifier = article['id']
        asset_url = f'/admin/upload-thumb/{identifier}'
        asset_page = editor.get('/admin/assets').get_data(as_text=True)
        upload_form = asset_page.split(f'action="{asset_url}', 1)[1].split('</form>', 1)[0]
        revision = re.search(r'name="revision" value="([^"]*)"', upload_form)[1]
        saved = editor.post(asset_url, data={
            'csrf_token': editor_csrf, 'revision': revision, 'thumbnail': (BytesIO(image), IMAGE.name),
        })
        self.assertEqual(saved.status_code, 303)
        preview = editor.get(saved.location)
        self.assertEqual(preview.status_code, 200)
        private = re.search(rf'/editor/records/article/{identifier}/files/[a-z0-9.-]+\.jpg',
                            preview.get_data(as_text=True))[0]
        self.assert_download(editor, private, 'image/jpeg', image)
        public = f'/assets/articles/{private.rsplit("/", 1)[1]}'
        self.assertEqual(reader.get(public).status_code, 404)
        old_public = reader.get(f'/article/{identifier}').get_data(as_text=True)
        self.assertIn(article['thumbnail_filename'], old_public)
        self.assertNotIn(private.rsplit('/', 1)[1], old_public)

    def test_legacy_forms_save_preview_publish_withdraw_archive_and_restore(self):
        editor, _ = self.sign_in('editor')
        owner, _ = self.sign_in('owner')
        reader = self.app.test_client()
        editor_csrf = self.admin_form(editor)
        owner_csrf = self.admin_form(owner)
        samples = (
            ('article', 'publications', 'articles', 'thumbnail', 'title', {
                'title': 'Draft-only publication', 'authors': 'One Author, Two Author', 'year': '2025',
                'venue': 'Local Journal', 'abstract': 'Abstract for the publish transition.',
            }),
            ('person', 'people', 'people', 'photo', 'name', {
                'name': 'Draft-only researcher', 'category': 'phd', 'bio': 'Research biography.',
            }),
            ('news', 'news', 'news', 'image', 'title', {
                'title': 'Draft-only news', 'date': '2026-11-25', 'content': 'Private publication notes.',
            }),
        )
        for kind, section, asset_folder, file_input, title_field, fields in samples:
            with self.subTest(kind=kind):
                form = editor.get(f'/admin/content?section={section}&new=1')
                self.assertEqual(form.status_code, 200)
                self.assertIn(f'name="{file_input}"', form.get_data(as_text=True))
                saved = editor.post('/admin/content', data={
                    'action': 'add', 'item_type': kind, 'csrf_token': editor_csrf, **fields,
                    file_input: (BytesIO(IMAGE.read_bytes()), IMAGE.name),
                })
                self.assertEqual(saved.status_code, 303)
                record_id = parse_qs(urlsplit(saved.location).query)['draft'][0]
                preview_url = f'/editor/records/{kind}/{record_id}/preview'
                public_url = f'/{kind}/{record_id}'
                transition_url = f'/editor/records/{kind}/{record_id}/transition'
                preview = editor.get(preview_url)
                self.assertEqual(preview.status_code, 200)
                self.assertIn(fields[title_field], preview.get_data(as_text=True))
                self.assertIn(preview_url, editor.get(saved.location).get_data(as_text=True))
                self.assertEqual(reader.get(public_url).status_code, 404)
                login = reader.get(preview_url)
                self.assertEqual(login.status_code, 302)
                _authorized, signed_in = self.sign_in('editor', login.location)
                self.assertEqual(signed_in.location, preview_url)

                private_path = re.search(
                    rf'/editor/records/{kind}/{record_id}/files/[a-z0-9.-]+\.jpg', preview.get_data(as_text=True),
                )[0]
                image_url = f'/assets/{asset_folder}/{private_path.rsplit("/", 1)[1]}'
                self.assert_download(editor, private_path, 'image/jpeg')
                self.assertEqual(reader.get(private_path).status_code, 302)
                self.assertEqual(reader.get(image_url).status_code, 404)

                revision = field_value(preview, 'revision')
                self.assertEqual(editor.post(transition_url, data={'action': 'publish', 'revision': revision}).status_code, 400)
                self.assertEqual(editor.post(transition_url, data={
                    'action': 'publish', 'revision': revision, 'csrf_token': editor_csrf,
                }).status_code, 303)
                self.assertIn(fields[title_field], reader.get(public_url).get_data(as_text=True))
                self.assert_download(reader, image_url, 'image/jpeg')

                revised_title = f'{fields[title_field]} revised'
                live_revision = field_value(editor.get(preview_url), 'revision')
                saved_again = editor.post('/admin/content', data={
                    **fields, title_field: revised_title, 'id': record_id, 'action': 'edit',
                    'item_type': kind, 'csrf_token': editor_csrf, 'revision': live_revision,
                })
                self.assertEqual(saved_again.status_code, 303)
                self.assertNotIn(revised_title, reader.get(public_url).get_data(as_text=True))
                self.assertEqual(editor.post(transition_url, data={
                    'action': 'withdraw', 'revision': live_revision, 'csrf_token': editor_csrf,
                }).status_code, 409)
                new_revision = field_value(editor.get(preview_url), 'revision')
                self.assertEqual(editor.post(transition_url, data={
                    'action': 'withdraw', 'revision': new_revision, 'csrf_token': editor_csrf,
                }).status_code, 303)
                self.assertEqual(reader.get(public_url).status_code, 404)
                self.assertEqual(reader.get(image_url).status_code, 404)

                revision = field_value(editor.get(preview_url), 'revision')
                self.assertEqual(editor.post(transition_url, data={
                    'action': 'archive', 'revision': revision, 'csrf_token': editor_csrf,
                }).status_code, 303)
                restorer, restore_csrf = (owner, owner_csrf) if kind == 'person' else (editor, editor_csrf)
                archived = restorer.get(preview_url)
                self.assertEqual(archived.status_code, 200)
                self.assertIn('value="restore"', archived.get_data(as_text=True))
                revision = field_value(archived, 'revision')
                self.assertEqual(restorer.post(transition_url, data={
                    'action': 'restore', 'revision': revision, 'csrf_token': restore_csrf,
                }).status_code, 303)
                self.assertIn(revised_title, editor.get(preview_url).get_data(as_text=True))
                self.assertEqual(reader.get(public_url).status_code, 404)

    def test_paper_api_private_attachments_idempotency_and_handoff_backup(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        caller = self.app.test_client()
        issued = owner.post('/editor/access', data={
            'action': 'issue', 'csrf_token': field_value(owner.get('/editor/access'), 'csrf_token'),
        })
        token = re.search(r'class="editor-token"[^>]*value="([^\"]+)"', issued.get_data(as_text=True))[1]
        path = '/editor/api/v1/papers/drafts'
        headers = {'Authorization': f'Bearer {token}', 'Idempotency-Key': 'paper-test-key'}

        def payload(title='Staged paper supplement visibility'):
            return MultiDict([
                ('payload', json.dumps({'title': title, 'authors': ['Test Author'], 'venue': 'Test Journal',
                                        'year': 2025, 'abstract': 'Attachment publication coverage.',
                                        'doi': '10.1234/preview-attachment-test'})),
                ('attachments', (BytesIO(PDF_ONE), 'supplement-one.pdf')),
                ('attachments', (BytesIO(PDF_TWO), 'supplement-two.pdf')),
            ])

        self.assertEqual(caller.post(path, data=payload(), headers={'Idempotency-Key': 'paper-test-key'}).status_code, 401)
        first = caller.post(path, data=payload(), headers=headers)
        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        again = caller.post(path, data=payload(), headers=headers)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(first.json['id'], again.json['id'])
        self.assertEqual(caller.post(path, data=payload('Different paper'), headers=headers).status_code, 409)

        record_id = first.json['id']
        preview = editor.get(first.json['preview_url'])
        self.assertEqual(preview.status_code, 200)
        attachments = first.json['attachments']
        self.assertEqual({item['name'] for item in attachments}, {'supplement-one.pdf', 'supplement-two.pdf'})
        selected = next(item['filename'] for item in attachments if item['name'] == 'supplement-one.pdf')
        excluded = next(item['filename'] for item in attachments if item['name'] != 'supplement-one.pdf')
        private = f'/editor/records/article/{record_id}/files/{selected}'
        public_base = '/assets/publication-files/'
        self.assert_download(editor, private, 'application/pdf', PDF_ONE)
        self.assertEqual(caller.get(private).status_code, 302)
        self.assertEqual(caller.get(public_base + selected).status_code, 404)
        transition = f'/editor/records/article/{record_id}/transition'
        self.assertEqual(caller.post(transition, data={'action': 'publish'}, headers=headers).status_code, 400)
        csrf = field_value(preview, 'csrf_token')
        self.assertEqual(editor.post(transition, data={
            'action': 'publish', 'published_attachments': selected,
            'revision': field_value(preview, 'revision'), 'csrf_token': csrf,
        }).status_code, 303)
        self.assert_download(caller, public_base + selected, 'application/pdf', PDF_ONE)
        self.assertEqual(caller.get(public_base + excluded).status_code, 404)
        detail = caller.get(f'/article/{record_id}').get_data(as_text=True)
        self.assertIn('supplement-one.pdf', detail)
        self.assertNotIn('supplement-two.pdf', detail)

        backup_url = '/admin/download-handoff.zip'
        self.assertEqual(editor.get(backup_url).status_code, 403)
        with owner.get(backup_url) as raw_backup:
            self.assertEqual(raw_backup.status_code, 200)
            bundle = raw_backup.get_data()
        with ZipFile(BytesIO(bundle)) as backup:
            self.assertIn('content-workspace/site_content/articles.json', backup.namelist())
            self.assertIn('record-workflow/workflow.sqlite3', backup.namelist())
            self.assertEqual(backup.read(f'record-workflow/draft-files/{excluded}'), PDF_TWO)
            self.assertFalse(any('preview-credentials.json' in name or name.endswith('/news.sqlite3')
                                 for name in backup.namelist()))
        with tempfile.TemporaryDirectory(prefix='full-admin-restored-') as state:
            target = restore_handoff_backup(BytesIO(bundle), Path(state) / 'restored')
            module, passwords = isolated_app(target, 'full_admin_restored_test_app')
            restored_reader = module.app.test_client()
            restored_editor, _ = self.sign_in('editor', app=module.app, passwords=passwords)
            self.assertEqual(restored_reader.get(f'/article/{record_id}').status_code, 200)
            self.assert_download(restored_reader, public_base + selected, 'application/pdf', PDF_ONE)
            self.assertEqual(restored_reader.get(public_base + excluded).status_code, 404)
            self.assert_download(restored_editor, f'/editor/records/article/{record_id}/files/{excluded}',
                                 'application/pdf', PDF_TWO)

        self.assertEqual(editor.post(transition, data={
            'action': 'withdraw', 'revision': field_value(editor.get(first.json['preview_url']), 'revision'),
            'csrf_token': csrf,
        }).status_code, 303)
        self.assertEqual(caller.get(public_base + selected).status_code, 404)
        self.assert_download(editor, private, 'application/pdf', PDF_ONE)

    def test_owner_operations_sync_stays_in_the_local_content_checkout(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        checkout = self.content.parent
        remote = checkout.parent / 'content-origin.git'

        def git(directory, *args):
            return subprocess.run(['git', *args], cwd=directory, env=offline_git_env(), check=True,
                                  text=True, encoding='utf-8', capture_output=True).stdout.strip()

        csrf = self.admin_form(owner, '/admin/operations')
        self.assertIn(owner.post('/admin/settings', data={
            'csrf_token': csrf, 'action': 'edit_site_branding', 'lab_name_short': 'Owner local sync',
        }).status_code, (302, 303))
        up = {'csrf_token': csrf, 'action': 'publish_content_to_github', 'commit_message': 'Test local content sync'}
        self.assertEqual(editor.post('/admin/operations', data=dict(up, csrf_token=self.admin_form(editor))).status_code, 403)
        pushed = owner.post('/admin/operations', data=up)
        self.assertIn(pushed.status_code, (302, 303))
        self.assertTrue(pushed.location.startswith('/admin/operations'))
        self.assertEqual(git(checkout, 'rev-parse', 'HEAD'), git(remote, 'rev-parse', 'refs/heads/preview'))
        self.assertEqual(git(checkout, 'log', '-1', '--format=%s'), up['commit_message'])

        with tempfile.TemporaryDirectory(prefix='preview-local-incoming-') as directory:
            incoming = Path(directory) / 'clone'
            git(directory, 'clone', '--branch', 'preview', remote.resolve().as_uri(), str(incoming))
            config = incoming / 'site_content/site.json'
            site = json.loads(config.read_text(encoding='utf-8'))
            site['home_note'] = 'Local incoming content for the full admin test'
            config.write_text(json.dumps(site, ensure_ascii=False), encoding='utf-8')
            git(incoming, 'add', '--', 'site_content/site.json')
            git(incoming, '-c', 'user.name=Preview Test', '-c', 'user.email=preview@opticom.invalid',
                'commit', '-m', 'Local incoming site content')
            git(incoming, 'push', 'origin', 'preview')
            down = {'csrf_token': csrf, 'action': 'sync_content_from_github'}
            pulled = owner.post('/admin/operations', data=down)
            self.assertIn(pulled.status_code, (302, 303))
            self.assertTrue(pulled.location.startswith('/admin/operations'))
            self.assertEqual(git(incoming, 'rev-parse', 'HEAD'), git(checkout, 'rev-parse', 'HEAD'))
            actual = json.loads((self.content / 'site.json').read_text(encoding='utf-8'))
            self.assertEqual(actual['home_note'], site['home_note'])


if __name__ == '__main__':
    unittest.main()
