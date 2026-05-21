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

    def test_supabase_resource_export_formats_created_at_as_beijing_time(self):
        with mock.patch.object(self.module, "_supabase_logs_ready", return_value=True):
            with mock.patch.object(
                self.module,
                "_fetch_supabase_rows",
                return_value=[
                    {
                        "created_at": "2026-05-20T15:05:36.61285+00:00",
                        "resource_id": "art_004",
                        "open_type": "paper",
                        "target_url": "https://example.com/paper",
                        "user_name": "1",
                        "user_affiliation": "1",
                        "user_email": "1@1",
                    },
                    {
                        "created_at": "2026-04-30 04:09:46.37916+00",
                        "resource_id": "art_004",
                        "open_type": "official_free_access",
                        "target_url": "https://example.com/official",
                        "user_name": "陈一彤",
                        "user_affiliation": "上海交通大学",
                        "user_email": "123@sjtu.edu.cn",
                    },
                    {
                        "created_at": "2026/4/24 15:11",
                        "resource_id": "legacy",
                        "open_type": "paper",
                        "target_url": "https://example.com/legacy",
                        "user_name": "Legacy",
                        "user_affiliation": "Local",
                        "user_email": "legacy@example.com",
                    },
                    {
                        "created_at": "unparsed-created-at",
                        "resource_id": "diagnostic",
                        "open_type": "paper",
                        "target_url": "https://example.com/diagnostic",
                        "user_name": "Diagnostic",
                        "user_affiliation": "Local",
                        "user_email": "diagnostic@example.com",
                    }
                ],
            ):
                rows = self.module._load_resource_open_rows()

        self.assertEqual(rows[0]["time"], "2026-05-20 23:05:36")
        self.assertEqual(rows[1]["time"], "2026-04-30 12:09:46")
        self.assertEqual(rows[2]["time"], "2026-04-24 15:11:00")
        self.assertEqual(rows[3]["time"], "unparsed-created-at")
        csv_text = self.module._csv_bytes(
            ["time", "name", "affiliation", "email", "resource_id", "type", "target_url"],
            rows,
        ).getvalue().decode("utf-8-sig")
        self.assertIn("2026-05-20 23:05:36,1,1,1@1,art_004,paper", csv_text)
        self.assertIn("2026-04-30 12:09:46,陈一彤,上海交通大学,123@sjtu.edu.cn,art_004,official_free_access", csv_text)

    def test_publications_page_renders_starred_titles(self):
        response = self.client.get("/articles")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(html.count('class="publication-star"'), 2)

    def test_empty_publication_years_and_people_categories_are_hidden(self):
        articles_response = self.client.get("/articles")
        self.assertEqual(articles_response.status_code, 200)
        articles_html = articles_response.get_data(as_text=True)
        self.assertIn('class="publication-year-heading">2025', articles_html)
        self.assertIn('class="publication-year-heading">2023', articles_html)
        self.assertNotIn('class="publication-year-heading">2024', articles_html)
        self.assertNotIn("No entries yet", articles_html)

        people_response = self.client.get("/team")
        self.assertEqual(people_response.status_code, 200)
        people_html = people_response.get_data(as_text=True)
        self.assertNotIn(">Alumni</h3>", people_html)
        self.assertNotIn("No entries yet", people_html)

    def test_home_omits_professor_link_from_hero(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("portal-hero-subtitle-link", html)

    def test_mobile_branding_targets_full_name_not_header_wordmark(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("site-brand-wordmark", html)
        self.assertIn('<span class="portal-brand-accent">Com</span><span class="portal-brand-accent">p</span>u<span class="portal-brand-accent">t</span>ing', html)

    def test_person_detail_has_wrapping_hooks_for_long_contact_text(self):
        response = self.client.get("/person/person_001")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('class="person-detail-body"', html)
        self.assertIn('class="person-contact-line"', html)

    def test_resource_include_note_and_source_starred_titles(self):
        response = self.client.get("/article/art_002")
        self.assertEqual(response.status_code, 200)
        detail_html = response.get_data(as_text=True)
        self.assertIn("Include:", detail_html)
        self.assertIn("Code &amp; Dataset", detail_html)

        sources_response = self.client.get("/sources")
        self.assertEqual(sources_response.status_code, 200)
        sources_html = sources_response.get_data(as_text=True)
        self.assertEqual(sources_html.count('class="publication-star source-card-star"'), 2)

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

    def test_content_assets_use_long_cache_and_conditional_requests(self):
        response = self.client.get("/assets/site/favicon-32.png")
        self.assertEqual(response.status_code, 200)
        cache_control = response.headers.get("Cache-Control", "")
        self.assertIn("public", cache_control)
        self.assertIn("max-age=31536000", cache_control)
        self.assertIn("immutable", cache_control)
        etag = response.headers.get("ETag")
        self.assertTrue(etag)
        response.close()

        cached_response = self.client.get(
            "/assets/site/favicon-32.png",
            headers={"If-None-Match": etag},
        )
        self.assertEqual(cached_response.status_code, 304)
        self.assertIn("immutable", cached_response.headers.get("Cache-Control", ""))
        cached_response.close()

    def test_asset_url_fingerprint_changes_with_file_stat(self):
        image_path = self.content_root / "images" / "favicon-32.png"

        with self.module.app.test_request_context("/"):
            first_url = self.module.asset_image_url("site", "favicon-32.png")

        image_path.write_bytes(image_path.read_bytes() + b"x")
        os.utime(image_path, None)

        with self.module.app.test_request_context("/"):
            second_url = self.module.asset_image_url("site", "favicon-32.png")

        self.assertIn("/assets/site/favicon-32.png?v=", first_url)
        self.assertNotEqual(first_url, second_url)

    def test_templates_emit_real_image_loading_attributes(self):
        home_response = self.client.get("/")
        self.assertEqual(home_response.status_code, 200)
        home_html = home_response.get_data(as_text=True)
        self.assertIn("/static/css/style.css?v=", home_html)
        self.assertIn('class="portal-hero-brand-image"', home_html)
        self.assertIn('fetchpriority="high"', home_html)
        self.assertIn('decoding="async"', home_html)

        articles_response = self.client.get("/articles")
        self.assertEqual(articles_response.status_code, 200)
        articles_html = articles_response.get_data(as_text=True)
        self.assertIn('class="publication-thumb"', articles_html)
        self.assertIn('loading="lazy"', articles_html)
        self.assertIn('decoding="async"', articles_html)

        detail_response = self.client.get("/article/art_001")
        self.assertEqual(detail_response.status_code, 200)
        detail_html = detail_response.get_data(as_text=True)
        self.assertIn("max-height: 300px; width: auto; height: auto", detail_html)
        self.assertNotIn('width="750" height="300"', detail_html)

    def test_footer_icp_record_is_editable_and_hidden_when_empty(self):
        cfg = self.module.load_site_config()
        self.assertIn("footer_icp_record", cfg)
        self.assertEqual(cfg["footer_icp_record"], "")

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("&copy; 2026 OPTICompt Lab", html)
        self.assertNotIn("2026xxxx", html)

        with self.client.session_transaction() as session:
            session["is_admin"] = True

        settings = self.client.get("/admin/settings")
        self.assertEqual(settings.status_code, 200)
        self.assertIn("footer_icp_record", settings.get_data(as_text=True))

        updated = dict(cfg)
        updated["footer_icp_record"] = "ICP-TEST-2026"
        post_data = {
            "action": "edit_site_branding",
            "item_type": "site",
            "lab_name_short": updated["lab_name_short"],
            "lab_name_short_zh": updated["lab_name_short_zh"],
            "lab_name_full": updated["lab_name_full"],
            "lab_name_full_zh": updated["lab_name_full_zh"],
            "site_version": updated["site_version"],
            "footer_copyright": updated["footer_copyright"],
            "footer_copyright_zh": updated["footer_copyright_zh"],
            "footer_icp_record": updated["footer_icp_record"],
            "_show_external_access_note_present": "1",
        }
        save_response = self.client.post("/admin/settings", data=post_data, follow_redirects=False)
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(self.module.load_site_config()["footer_icp_record"], "ICP-TEST-2026")

        response = self.client.get("/")
        self.assertIn(" | ICP-TEST-2026", response.get_data(as_text=True))

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
