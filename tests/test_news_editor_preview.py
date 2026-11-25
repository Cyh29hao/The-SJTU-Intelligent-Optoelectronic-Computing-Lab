import importlib.util
import json
import os
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from scripts.run_news_preview import prepare_preview


ROOT = Path(__file__).resolve().parents[1]
PICTURE = ROOT / 'site_content' / 'images' / 'news' / 'news_003.jpg'


def form_value(response, name):
    match = re.search(rf'name="{name}" value="([^"]+)"', response.get_data(as_text=True))
    return match[1]


class NewsEditorPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state = tempfile.TemporaryDirectory(prefix='news-editor-test-')
        cls.addClassCleanup(state.cleanup)
        with patch.dict(os.environ):
            credentials_file = prepare_preview(Path(state.name))
            cls.passwords = json.loads(credentials_file.read_text(encoding='utf-8'))
            spec = importlib.util.spec_from_file_location('news_editor_preview_test_app', ROOT / 'app.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        cls.app = module.app
        cls.app.config['TESTING'] = True

    def sign_in(self, role):
        client = self.app.test_client()
        page = client.get('/editor/login')
        result = client.post('/editor/login', data={
            'username': role, 'password': self.passwords[role], 'csrf_token': form_value(page, 'csrf_token'),
        })
        self.assertEqual(result.status_code, 303)
        return client

    def test_legacy_review_opens_owner_pages_without_authorizing_legacy_writes(self):
        with patch.dict(self.app.config, ADMIN_HANDOFF_ENABLED=True):
            owner = self.sign_in('owner')
            editor = self.sign_in('editor')
            anonymous = self.app.test_client()
            with anonymous.session_transaction() as session:
                session['is_admin'] = True
            self.assertEqual(anonymous.get('/admin/settings').location, '/editor/login')
            self.assertEqual(editor.get('/admin').location, '/editor/')
            for page in ('/admin', '/admin/content', '/admin/assets', '/admin/settings', '/admin/analytics'):
                opened = owner.get(page)
                self.assertEqual(opened.status_code, 200, page)
                self.assertIn('no-store', opened.headers['Cache-Control'])
                if page != '/admin':
                    self.assertEqual(editor.get(page).status_code, 403, page)
            backup = owner.get('/admin/download-site-content.zip')
            with ZipFile(BytesIO(backup.data)) as archive:
                self.assertIn('people.json', archive.namelist())
                self.assertIn('articles.json', archive.namelist())
            self.assertEqual(editor.get('/admin/download-logs.csv').status_code, 403)
            self.assertEqual(owner.post('/admin/settings', data={'action': 'edit_site_branding'}).status_code, 403)
            self.assertEqual(owner.get('/admin/content?action=delete&item_type=news&id=news_003').status_code, 403)

    def test_visitor_preview_returns_to_the_same_role(self):
        with patch.dict(self.app.config, ADMIN_HANDOFF_ENABLED=True):
            for role, dashboard in (('owner', '/admin'), ('editor', '/editor/')):
                with self.subTest(role=role):
                    browser = self.sign_in(role)
                    page = browser.get(dashboard)
                    csrf = form_value(page, 'csrf_token')
                    switched = browser.post('/editor/view-as-visitor', data={'csrf_token': csrf})
                    self.assertEqual(switched.location, '/')
                    visitor_home = browser.get('/')
                    self.assertIn('/editor/return-from-visitor', visitor_home.get_data(as_text=True))
                    self.assertEqual(browser.get(dashboard).location, '/editor/login')
                    self.assertEqual(browser.post('/editor/return-from-visitor').status_code, 400)
                    returned = browser.post('/editor/return-from-visitor', data={'csrf_token': csrf})
                    self.assertEqual(returned.location, dashboard)
                    self.assertEqual(browser.get(dashboard).status_code, 200)
                    self.assertEqual(browser.get('/admin/settings').status_code, 200 if role == 'owner' else 403)
                    self.assertEqual(browser.post('/editor/logout', data={'csrf_token': csrf}).location, '/editor/login')
                    self.assertEqual(browser.get(dashboard).location, '/editor/login')

    def test_editing_and_withdrawal_keep_private_content_out_of_public_routes(self):
        editor = self.sign_in('editor')
        reader = self.app.test_client()
        self.assertEqual(reader.get('/editor/').status_code, 302)
        self.assertEqual(editor.get('/admin/settings').status_code, 403)
        self.assertEqual(editor.get('/editor/access').status_code, 403)

        write_url = '/editor/news/new'
        form = editor.get(write_url)
        fields = {
            'csrf_token': form_value(form, 'csrf_token'), 'title_zh': '预览新闻甲',
            'date': '2026-11-25', 'content_zh': '这是一条预览新闻。',
            'image': (BytesIO(PICTURE.read_bytes()), PICTURE.name),
        }
        created = editor.post(write_url, data=fields)
        self.assertEqual(created.status_code, 303)
        edit_url = created.location
        news_id = edit_url.rsplit('/', 1)[-1]
        public_url = f'/news/{news_id}'
        publish_url = f'{edit_url}/publication'
        preview = editor.get(f'{edit_url}/preview')
        self.assertIn('预览新闻甲', preview.get_data(as_text=True))
        self.assertNotIn('预览新闻甲', reader.get('/news').get_data(as_text=True))
        self.assertEqual(reader.get(public_url).status_code, 404)

        entry = self.app.config['NEWS_EDITOR_STORE'].get_entry(news_id)
        image_path = f"/assets/news/{entry['draft']['image_filename']}"
        self.assertEqual(reader.get(image_path).status_code, 404)
        self.assertEqual(reader.get(f'{edit_url}/preview').status_code, 302)
        self.assertEqual(editor.post(publish_url, data={'action': 'publish'}).status_code, 400)
        edit_csrf = form_value(preview, 'csrf_token')
        revision = form_value(preview, 'revision')
        publication = editor.post(publish_url, data={'action': 'publish', 'csrf_token': edit_csrf, 'revision': revision})
        self.assertEqual(publication.status_code, 303)
        self.assertIn('预览新闻甲', reader.get(public_url).get_data(as_text=True))
        self.assertEqual(reader.get(image_path).mimetype, 'image/jpeg')

        current = editor.get(edit_url)
        revision = form_value(current, 'revision')
        update = editor.post(edit_url, data={
            'csrf_token': edit_csrf, 'revision': revision, 'title_zh': '预览新闻乙',
            'date': '2026-11-25', 'content_zh': '修改后，公开页面应保留上一次发布的文字。',
        })
        self.assertEqual(update.status_code, 303)
        self.assertIn('预览新闻甲', reader.get(public_url).get_data(as_text=True))
        self.assertIn('预览新闻乙', editor.get(f'{edit_url}/preview').get_data(as_text=True))
        self.assertEqual(editor.post(publish_url, data={
            'action': 'withdraw', 'csrf_token': edit_csrf, 'revision': revision,
        }).status_code, 409)
        latest = form_value(editor.get(edit_url), 'revision')
        withdrawn = editor.post(publish_url, data={'action': 'withdraw', 'csrf_token': edit_csrf, 'revision': latest})
        self.assertEqual(withdrawn.status_code, 303)
        self.assertEqual(reader.get(public_url).status_code, 404)
        self.assertEqual(reader.get(image_path).status_code, 404)
        self.assertIn('预览新闻乙', editor.get(f'{edit_url}/preview').get_data(as_text=True))

    def test_api_credential_is_draft_only_idempotent_and_revocable(self):
        owner = self.sign_in('owner')
        form = owner.get('/editor/access')
        csrf = form_value(form, 'csrf_token')
        issued = owner.post('/editor/access', data={'csrf_token': csrf, 'action': 'issue'})
        token = re.search(r'class="editor-token"[^>]*value="([^"]+)"', issued.get_data(as_text=True))[1]
        self.assertIn('no-store', issued.headers['Cache-Control'])
        caller = self.app.test_client()
        headers = {'Authorization': f'Bearer {token}', 'Idempotency-Key': 'test-agent-retry'}
        payload = {'date': '2026-11-25', 'title': 'Agent draft', 'content': 'Upload only.'}
        api = '/editor/api/v1/news/drafts'
        first = caller.post(api, json=payload, headers=headers)
        again = caller.post(api, json=payload, headers=headers)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.json['id'], again.json['id'])
        self.assertEqual(caller.post(api, json={**payload, 'title': 'Other'}, headers=headers).status_code, 409)
        preview_redirect = caller.get(first.json['preview_url'], headers=headers)
        self.assertEqual(preview_redirect.status_code, 302)
        login_form = caller.get(preview_redirect.location)
        signed_in = caller.post(preview_redirect.location, data={
            'username': 'editor', 'password': self.passwords['editor'], 'csrf_token': form_value(login_form, 'csrf_token'),
        })
        self.assertEqual(signed_in.location, first.json['preview_url'])
        self.assertEqual(caller.get(first.json['preview_url']).status_code, 200)
        caller = self.app.test_client()
        self.assertEqual(caller.post(first.json['edit_url'] + '/publication', data={'action': 'publish'}, headers=headers).status_code, 400)
        self.assertEqual(caller.get(f"/news/{first.json['id']}").status_code, 404)

        self.assertEqual(owner.post('/editor/access', data={'csrf_token': csrf, 'action': 'revoke'}).status_code, 303)
        self.assertEqual(caller.post(api, json=payload, headers=headers).status_code, 401)


if __name__ == '__main__':
    unittest.main()
