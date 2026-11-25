import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import content_workflow
from content_workflow import ContentWorkflow, WorkflowError
from news_editor_store import NewsStore


ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / 'site_content' / 'images' / 'news' / 'news_003.jpg'


def _pdf(marker):
    objects = (
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 144 144] /Resources << >> >>',
    )
    body = b'%PDF-1.4\n%' + marker.encode('ascii') + b'\n'
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += f'{index} 0 obj\n'.encode('ascii') + obj + b'\nendobj\n'
    table = len(body)
    body += b'xref\n0 4\n0000000000 65535 f \n'
    for offset in offsets:
        body += f'{offset:010d} 00000 n \n'.encode('ascii')
    return body + f'trailer\n<< /Root 1 0 R /Size 4 >>\nstartxref\n{table}\n%%EOF\n'.encode('ascii')


class ContentWorkflowTests(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory(prefix='workflow-test-')
        self.addCleanup(sandbox.cleanup)
        self.root = Path(sandbox.name)
        self.public = self.root / 'site_content'
        self.state = self.root / 'state'
        self.public.mkdir()
        self.store = ContentWorkflow(self.public, self.state)

    def put_public(self, filename, records):
        (self.public / filename).write_text(json.dumps(records), encoding='utf-8')

    def test_legacy_ids_and_lifecycle_for_every_record_type(self):
        fixtures = (
            ('article', 'articles.json', 'art_001', 'title', 'thumbnail_filename'),
            ('person', 'people.json', 'person_001', 'name', 'photo_filename'),
            ('news', 'news.json', 'news_001', 'title', 'image_filename'),
        )
        photo = IMAGE.read_bytes()
        for kind, filename, record_id, field, image_field in fixtures:
            with self.subTest(kind=kind):
                legacy = {'id': record_id, field: 'Old text', image_field: '', 'custom_field': 'Keep'}
                self.put_public(filename, [legacy])
                self.assertEqual(self.store.get(kind, record_id)['revision'], 0)
                draft, created = self.store.save(kind, {field: 'New text'}, record_id=record_id, image=('photo.jpg', photo))
                self.assertTrue(created)
                self.assertEqual(self.store.published(kind), [legacy])
                image = draft['image']
                self.assertIsNone(self.store.public_image(kind, image))
                self.assertEqual(self.store.private_file(kind, record_id, image).read_bytes(), photo)
                self.assertIsNone(self.store.private_file(kind, 'another', image))

                live = self.store.transition(kind, record_id, 'publish', draft['revision'])
                self.assertEqual(live['published'][field], 'New text')
                self.assertEqual(live['published']['id'], record_id)
                self.assertEqual(live['published']['custom_field'], 'Keep')
                self.assertEqual(live['draft'], live['published'])
                self.assertEqual(self.store.public_image(kind, image).read_bytes(), photo)
                restored = self.store.transition(kind, record_id, 'withdraw', live['revision'])
                self.assertIsNone(self.store.public_image(kind, image))
                self.assertIsNone(restored['published'])
                archived = self.store.transition(kind, record_id, 'archive', restored['revision'])
                self.assertTrue(archived['archived'])
                kept = self.store.transition(kind, record_id, 'restore', archived['revision'])
                self.assertFalse(kept['archived'])
                self.assertIsNone(kept['published'])
                live_again = self.store.transition(kind, record_id, 'publish', kept['revision'])
                self.assertEqual(live_again['published'][image_field], image)
                self.assertEqual(self.store.public_image(kind, image).read_bytes(), photo)

    def test_only_selected_pdfs_leave_private_storage(self):
        first, second = _pdf('first'), _pdf('second')
        row, _ = self.store.save('article', {'title': 'Paper'}, attachments=(('first.pdf', first), ('second.pdf', second)))
        record_id = row['id']
        a, b = (item['filename'] for item in row['attachments'])
        self.assertEqual(self.store.private_file('article', record_id, b).read_bytes(), second)
        self.assertIsNone(self.store.public_file(a))
        self.assertIsNone(self.store.public_file(b))
        live = self.store.transition('article', record_id, 'publish', row['revision'], attachments=[a])
        self.assertEqual(self.store.public_file(a).read_bytes(), first)
        self.assertIsNone(self.store.public_file(b))
        self.assertFalse((self.public / 'attachments' / b).exists())
        self.store.transition('article', record_id, 'publish', live['revision'], attachments=[b])
        self.assertIsNone(self.store.public_file(a))
        self.assertFalse((self.public / 'attachments' / a).exists())
        self.assertEqual(self.store.private_file('article', record_id, a).read_bytes(), first)
        self.assertEqual(self.store.public_file(b).read_bytes(), second)

    def test_same_key_retries_existing_record_and_concurrent_edits_reject_stale_revision(self):
        legacy = {'id': 'art_001', 'title': 'Legacy'}
        self.put_public('articles.json', [legacy])
        first, created = self.store.save('article', {'title': 'API draft'}, record_id='art_001', submission_key='paper-1')
        retry, retried = self.store.save('article', {'title': 'API draft'}, record_id='art_001', submission_key='paper-1')
        self.assertTrue(created)
        self.assertFalse(retried)
        self.assertEqual(first, retry)
        with self.assertRaises(WorkflowError) as rejected:
            self.store.save('article', {'title': 'Different'}, record_id='art_001', submission_key='paper-1')
        self.assertEqual(rejected.exception.status, 409)

        barrier = Barrier(2)

        def update(number):
            barrier.wait()
            try:
                self.store.save('article', {'title': str(number)}, record_id='art_001', expected_revision=first['revision'])
                return 200
            except WorkflowError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertCountEqual(workers.map(update, range(2)), [200, 409])
        self.assertEqual(self.store.published('article'), [legacy])

    def test_external_content_update_requires_explicit_rebase(self):
        self.put_public('articles.json', [{'id': 'art_001', 'title': 'Legacy'}])
        draft, _ = self.store.save('article', {'title': 'My draft'}, record_id='art_001')
        with self.store.content_write():
            self.put_public('articles.json', [{'id': 'art_001', 'title': 'Git update'}])
        self.assertTrue(self.store.get('article', 'art_001')['conflicted'])
        with self.assertRaises(WorkflowError) as stale:
            self.store.transition('article', 'art_001', 'publish', draft['revision'])
        self.assertEqual(stale.exception.status, 409)
        reviewed = self.store.rebase('article', 'art_001', draft['revision'])
        self.assertFalse(reviewed['conflicted'])
        live = self.store.transition('article', 'art_001', 'publish', reviewed['revision'])
        self.assertEqual(live['published']['title'], 'My draft')

    def test_publish_recovers_after_snapshot_replaced_before_sqlite_update(self):
        draft, _ = self.store.save('person', {'name': 'Member'}, image=('avatar.jpg', IMAGE.read_bytes()))
        original = content_workflow.atomic_write

        def interrupted(path, body):
            original(path, body)
            if path.name == 'people.json':
                raise InterruptedError('stopped after snapshot replacement')

        with patch('content_workflow.atomic_write', side_effect=interrupted):
            with self.assertRaises(InterruptedError):
                self.store.transition('person', draft['id'], 'publish', draft['revision'])

        restarted = ContentWorkflow(self.public, self.state)
        live = restarted.get('person', draft['id'])
        self.assertEqual(live['published']['name'], 'Member')
        self.assertEqual(live['revision'], draft['revision'] + 1)
        self.assertEqual(restarted.public_image('person', draft['image']).read_bytes(), IMAGE.read_bytes())

    def test_news_pilot_migration_preserves_live_pending_and_withdrawn_rows(self):
        seed = {'id': 'news_001', 'title': 'Old story', 'content': 'Old body', 'date': '2026-04-20'}
        self.put_public('news.json', [seed])
        pilot = NewsStore(self.root / 'pilot.sqlite3')
        pilot.initialize([seed])
        photo = IMAGE.read_bytes()
        payload = {'title': 'Local demo', 'content': 'The body', 'date': '2026-05-20'}
        published_draft, _ = pilot.save_draft(payload, photo)
        pilot.set_publication(published_draft['id'], publish=True, expected_revision=published_draft['revision'])
        latest = pilot.get_entry(published_draft['id'])
        pilot.save_draft(payload | {'title': 'Pending title'}, news_id=latest['id'], expected_revision=latest['revision'])
        draft, _ = pilot.save_draft(payload | {'title': 'New draft'}, photo)
        legacy = pilot.get_entry(seed['id'])
        pilot.set_publication(seed['id'], publish=False, expected_revision=legacy['revision'])

        self.store.migrate_news_pilot(pilot)
        self.store.migrate_news_pilot(pilot)
        self.assertEqual(len(self.store.published('news')), 1)
        live = self.store.get('news', published_draft['id'])
        self.assertEqual(live['published']['title'], 'Local demo')
        self.assertEqual(live['draft']['title'], 'Pending title')
        self.assertEqual(self.store.public_image('news', live['published']['image_filename']).read_bytes(), photo)
        fresh = self.store.get('news', draft['id'])
        self.assertIsNone(fresh['published'])
        self.assertEqual(self.store.private_file('news', draft['id'], fresh['image']).read_bytes(), photo)
        self.assertIsNone(self.store.get('news', seed['id'])['published'])


if __name__ == '__main__':
    unittest.main()
