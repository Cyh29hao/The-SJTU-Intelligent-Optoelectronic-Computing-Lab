import importlib.util
import io
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from werkzeug.datastructures import MultiDict


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"
SITE_CONTENT_PATH = PROJECT_ROOT / "site_content"


def load_app_module(content_root):
    module_name = f"app_under_test_{os.urandom(4).hex()}"
    spec = importlib.util.spec_from_file_location(module_name, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONTENT_ROOT = str(content_root)
    return module


class CMSFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.content_root = pathlib.Path(self.tempdir.name) / "site_content"
        shutil.copytree(SITE_CONTENT_PATH, self.content_root)

        self.original_env = {
            "CONTENT_ROOT": os.environ.get("CONTENT_ROOT"),
            "SUPABASE_LOGS_ENABLED": os.environ.get("SUPABASE_LOGS_ENABLED"),
            "APP_MODE": os.environ.get("APP_MODE"),
        }
        os.environ["CONTENT_ROOT"] = str(self.content_root)
        os.environ["SUPABASE_LOGS_ENABLED"] = "0"
        os.environ["APP_MODE"] = "local"

        self.module = load_app_module(self.content_root)
        self.client = self.module.app.test_client()

    def tearDown(self):
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tempdir.cleanup()

    def test_articles_schema_and_initial_flags(self):
        articles = self.module.load_articles_data()
        by_id = {item["id"]: item for item in articles}

        for article in articles:
            self.assertIn("home_top_pinned", article)
            self.assertIn("is_starred", article)

        self.assertTrue(by_id["art_001"]["home_top_pinned"])
        self.assertTrue(by_id["art_001"]["is_starred"])
        self.assertTrue(by_id["art_002"]["is_starred"])
        self.assertFalse(by_id["art_004"]["home_top_pinned"])
        self.assertFalse(by_id["art_004"]["is_starred"])
        self.assertFalse(by_id["art_005"]["home_top_pinned"])
        self.assertFalse(by_id["art_005"]["is_starred"])

        featured_articles = sorted(
            [item for item in articles if item.get("featured_on_home")],
            key=self.module._home_carousel_sort_key,
        )
        self.assertEqual(featured_articles[0]["id"], "art_001")

    def test_publications_page_renders_starred_titles(self):
        response = self.client.get("/articles")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(html.count('class="publication-star"'), 2)

    def test_official_free_defaults_respect_manual_overrides(self):
        default_flags = self.module._resolve_article_form_flags(
            MultiDict(
                {
                    "official_free_access_url": "https://example.com/free",
                    "featured_on_home_manual": "0",
                    "home_top_pinned_manual": "0",
                }
            )
        )
        manual_flags = self.module._resolve_article_form_flags(
            MultiDict(
                {
                    "official_free_access_url": "https://example.com/free",
                    "featured_on_home_manual": "1",
                    "home_top_pinned_manual": "1",
                }
            )
        )

        self.assertEqual(default_flags, (True, True, False))
        self.assertEqual(manual_flags, (False, False, False))

    def test_people_photos_section_appears_in_assets(self):
        with self.client.session_transaction() as session:
            session["is_admin"] = True
        response = self.client.get("/admin/assets")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("People Photos", html)
        self.assertIn("/admin/upload-person-photo/person_001", html)

    def test_hero_brand_strip_is_managed_from_assets(self):
        with self.client.session_transaction() as session:
            session["is_admin"] = True

        response = self.client.get("/admin/assets")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Hero Brand Strip", html)
        self.assertIn("/admin/upload-site-image/hero-brand", html)

        upload = self.client.post(
            "/admin/upload-site-image/hero-brand?redirect_module=assets",
            data={"image": (io.BytesIO(b"fake png bytes"), "hero-update.png")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(upload.status_code, 302)

        cfg = self.module.load_site_config()
        self.assertEqual(cfg["hero_brand_filename"], "hero_brand_strip.png")
        self.assertTrue((self.content_root / "images" / "hero_brand_strip.png").exists())

    def test_link_rate_limit_blocks_third_open_and_uses_language(self):
        with self.client.session_transaction() as session:
            session["user_info"] = {
                "name": "Tester",
                "affiliation": "SJTU",
                "email": "tester@example.com",
            }
            session["lang"] = "zh"

        first = self.client.get("/open_link/official_free_access/art_001", follow_redirects=False)
        second = self.client.get("/open_link/official_free_access/art_001", follow_redirects=False)
        third = self.client.get("/open_link/official_free_access/art_001", follow_redirects=False)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(third.status_code, 429)
        self.assertIn("访问次数过多", third.get_data(as_text=True))

    def test_git_sync_summary_maps_expected_states(self):
        synced = self.module._build_git_sync_summary(
            {"has_changes": False},
            {
                "available": True,
                "kind": "success",
                "incoming_code_files": [],
                "incoming_content_files": [],
                "ahead_count": 0,
                "behind_count": 0,
                "can_sync": False,
            },
        )
        need_down = self.module._build_git_sync_summary(
            {"has_changes": False},
            {
                "available": True,
                "kind": "success",
                "incoming_code_files": [],
                "incoming_content_files": ["site_content/articles.json"],
                "ahead_count": 0,
                "behind_count": 1,
                "can_sync": True,
            },
        )
        need_up = self.module._build_git_sync_summary(
            {"has_changes": True},
            {
                "available": True,
                "kind": "info",
                "incoming_code_files": [],
                "incoming_content_files": [],
                "ahead_count": 0,
                "behind_count": 0,
                "can_sync": False,
            },
        )
        conflicts = self.module._build_git_sync_summary(
            {"has_changes": True},
            {
                "available": True,
                "kind": "success",
                "incoming_code_files": [],
                "incoming_content_files": ["site_content/site.json"],
                "ahead_count": 0,
                "behind_count": 1,
                "can_sync": False,
            },
        )

        self.assertEqual(synced["label"], "Already synced")
        self.assertEqual(need_down["label"], "Need sync down")
        self.assertEqual(need_up["label"], "Need sync up")
        self.assertEqual(conflicts["label"], "Conflicts exist")

    def test_admin_home_shows_buttons_and_sync_summary(self):
        with self.client.session_transaction() as session:
            session["is_admin"] = True
        with mock.patch.object(
            self.module,
            "_build_admin_home_context",
            return_value={
                "git_sync_summary": {
                    "label": "Need sync up",
                    "kind": "info",
                    "description": "Local site_content changes or local commits are ahead of GitHub.",
                },
                "sync_down_status": {"branch": "main"},
            },
        ):
            response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("View as User", html)
        self.assertIn("Logout", html)
        self.assertIn("Need sync up", html)


if __name__ == "__main__":
    unittest.main()
