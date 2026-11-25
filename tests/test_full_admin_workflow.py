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
PDF_ONE = b'%PDF-1.4\n% first attachment fixture\n%%EOF\n'
PDF_TWO = b'%PDF-1.4\n% second attachment fixture\n%%EOF\n'


def field_value(response, name):
    return re.search(rf'name="{name}" value="([^"]*)"', response.get_data(as_text=True))[1]


def isolated_app(state_dir, module_name):
    with patch.dict(os.environ):
        credentials = prepare_preview(state_dir)
        passwords = json.loads(credentials.read_text(encoding='utf-8'))
        os.environ['ADMIN_HANDOFF_PREVIEW'] = '1'
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

    def test_legacy_panels_and_editor_field_permissions(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        reader = self.app.test_client()
        for url in ('/admin', '/admin/content', '/admin/assets', '/admin/settings', '/admin/analytics'):
            with self.subTest(url=url):
                result = owner.get(url)
                self.assertEqual(result.status_code, 200)
                self.assertIn('no-store', result.headers['Cache-Control'])
                self.assertEqual(reader.get(url).location, '/editor/login')
        for url in ('/admin', '/admin/content', '/admin/assets'):
            self.assertEqual(editor.get(url).status_code, 200)
        for url in ('/admin/settings', '/admin/analytics', '/admin/download-logs.csv', '/editor/access'):
            self.assertEqual(editor.get(url).status_code, 403)
        with ZipFile(BytesIO(owner.get('/admin/download-site-content.zip').data)) as backup:
            self.assertTrue({'articles.json', 'people.json', 'news.json', 'site.json'} <= set(backup.namelist()))
        self.assertEqual(owner.get('/admin/download-logs.csv').status_code, 200)

        config_path = self.content / 'site.json'
        original = config_path.read_bytes()
        try:
            csrf = self.admin_form(editor)
            update = {'action': 'edit_site_welcome', 'home_note': 'Editor-managed home note'}
            self.assertEqual(editor.post('/admin/content', data=update).status_code, 400)
            escalation = dict(update, csrf_token=csrf, lab_name_short='Blocked setting')
            self.assertEqual(editor.post('/admin/content', data=escalation).status_code, 403)
            self.assertEqual(editor.post('/admin/settings', data={
                'action': 'edit_site_branding', 'csrf_token': csrf, 'lab_name_short': 'Blocked setting',
            }).status_code, 403)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertIn(editor.post('/admin/content', data=dict(update, csrf_token=csrf)).status_code, (302, 303))
            self.assertIn(editor.post('/admin/content', data={
                'action': 'edit_join_us', 'csrf_token': csrf, 'admissions_title': 'Editor-managed recruiting',
                'admission_direction_title_1': 'Research assistant',
            }).status_code, (302, 303))
            self.assertEqual(editor.get('/admin/content?action=delete&item_type=news&id=news_003').status_code, 403)

            owner_csrf = self.admin_form(owner, '/admin/settings')
            self.assertIn(owner.post('/admin/settings', data={
                'action': 'edit_site_branding', 'csrf_token': owner_csrf, 'lab_name_short': 'Owner branding',
            }).status_code, (302, 303))
            config = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertEqual(config['home_note'], 'Editor-managed home note')
            self.assertEqual(config['admissions_title'], 'Editor-managed recruiting')
            self.assertEqual(config['lab_name_short'], 'Owner branding')
        finally:
            config_path.write_bytes(original)

    def test_legacy_image_upload_tools_preserve_the_published_record(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        reader = self.app.test_client()
        editor_csrf = self.admin_form(editor, '/admin/assets')
        owner_csrf = self.admin_form(owner, '/admin/assets')
        image = IMAGE.read_bytes()
        logo_url = '/admin/upload-site-image/logo'
        self.assertEqual(editor.post(logo_url, data={
            'csrf_token': editor_csrf, 'image': (BytesIO(image), IMAGE.name),
        }).status_code, 403)
        self.assertIn(owner.post(logo_url, data={
            'csrf_token': owner_csrf, 'image': (BytesIO(image), IMAGE.name),
        }).status_code, (302, 303))
        configured = json.loads((self.content / 'site.json').read_text(encoding='utf-8'))
        self.assert_download(reader, '/assets/site/' + configured['logo_filename'], 'image/jpeg', image)

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
                revision = field_value(owner.get(preview_url), 'revision')
                self.assertEqual(editor.post(transition_url, data={
                    'action': 'restore', 'revision': revision, 'csrf_token': editor_csrf,
                }).status_code, 403)
                self.assertEqual(owner.post(transition_url, data={
                    'action': 'restore', 'revision': revision, 'csrf_token': owner_csrf,
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

    def test_owner_legacy_git_up_and_down_routes_stay_in_the_local_content_checkout(self):
        owner, _ = self.sign_in('owner')
        editor, _ = self.sign_in('editor')
        checkout = self.content.parent
        remote = checkout.parent / 'content-origin.git'

        def git(directory, *args):
            return subprocess.run(['git', *args], cwd=directory, env=offline_git_env(), check=True,
                                  text=True, encoding='utf-8', capture_output=True).stdout.strip()

        csrf = self.admin_form(owner, '/admin/settings')
        self.assertIn(owner.post('/admin/settings', data={
            'csrf_token': csrf, 'action': 'edit_site_branding', 'lab_name_short': 'Owner local sync',
        }).status_code, (302, 303))
        up = {'csrf_token': csrf, 'action': 'publish_content_to_github', 'commit_message': 'Test local content sync'}
        self.assertEqual(editor.post('/admin/analytics', data=dict(up, csrf_token=self.admin_form(editor))).status_code, 403)
        self.assertIn(owner.post('/admin/analytics', data=up).status_code, (302, 303))
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
            self.assertIn(owner.post('/admin/analytics', data=down).status_code, (302, 303))
            self.assertEqual(git(incoming, 'rev-parse', 'HEAD'), git(checkout, 'rev-parse', 'HEAD'))
            actual = json.loads((self.content / 'site.json').read_text(encoding='utf-8'))
            self.assertEqual(actual['home_note'], site['home_note'])


if __name__ == '__main__':
    unittest.main()
