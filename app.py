# app.py - English Version (Fully Internationalized)
# Refactored for readability and structure

# ==============================================================================
# 1. Imports
# ==============================================================================
import csv
import io
import json
import logging
import os
import requests
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, send_file, session
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# ==============================================================================
# 2. Configuration & Setup
# ==============================================================================

# --- Load Environment Variables ---
# Load .env file (for local dev only; Render uses env vars directly)
load_dotenv()

# --- Global Settings ---
def _resolve_app_mode():
    explicit_mode = (os.environ.get('APP_MODE') or '').strip().lower()
    if explicit_mode in ('local', 'dev', 'development'):
        return 'local', True
    if explicit_mode in ('prod', 'production', 'deploy', 'deployed', 'hosted'):
        return 'production', False

    hosted_env_hints = any(
        (os.environ.get(name) or '').strip()
        for name in ('RENDER', 'RAILWAY_ENVIRONMENT', 'VERCEL', 'KOYEB_APP_NAME', 'FLY_APP_NAME')
    )
    has_local_port = bool((os.environ.get('LOCAL_PORT') or '').strip())
    has_hosted_port = bool((os.environ.get('PORT') or '').strip())

    if has_local_port:
        return 'local', True
    if hosted_env_hints or has_hosted_port:
        return 'production', False
    return 'local', True

APP_MODE, IS_LOCAL = _resolve_app_mode()
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
CONTENT_ROOT = (os.environ.get('CONTENT_ROOT') or os.path.join(PROJECT_ROOT, 'site_content')).strip()
LEGACY_CONTENT_ROOT = os.path.join(PROJECT_ROOT, 'render_data')
LOCAL_HOST = (os.environ.get('LOCAL_HOST', '127.0.0.1') or '127.0.0.1').strip()
LOCAL_PORT = int(os.environ.get('LOCAL_PORT') or os.environ.get('PORT') or 5000)
APP_SECRET_KEY = (os.environ.get('APP_SECRET_KEY') or '').strip()
PERSISTENT_ROOT = 'No persistent disk (Supabase only)'
PRIVATE_DOWNLOADS_DIR = 'External link mode'
DATA_LOGS_DIR = 'Supabase tables'
PAGE_VIEWS_CSV_PATH = ''
SUPABASE_URL = (os.environ.get('SUPABASE_URL') or '').strip()
SUPABASE_SECRET_KEY = (os.environ.get('SUPABASE_SECRET_KEY') or '').strip()
SUPABASE_LOGS_ENABLED = (os.environ.get('SUPABASE_LOGS_ENABLED') or '0').strip().lower() in ('1', 'true', 'yes', 'on')
SUPABASE_REST_ROOT = f"{SUPABASE_URL.rstrip('/')}/rest/v1" if SUPABASE_URL else ''

# Ensure directories exist (Safety check on every launch)
os.makedirs(CONTENT_ROOT, exist_ok=True)

def _bootstrap_content_root():
    """One-time migration: seed git-tracked site_content/ from legacy render_data/."""
    content_marker = os.path.join(CONTENT_ROOT, 'site.json')
    if os.path.exists(content_marker):
        return
    for filename in ('site.json', 'articles.json', 'people.json', 'news.json'):
        legacy_path = os.path.join(LEGACY_CONTENT_ROOT, filename)
        target_path = os.path.join(CONTENT_ROOT, filename)
        if os.path.exists(legacy_path) and not os.path.exists(target_path):
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(legacy_path, target_path)
    legacy_images = os.path.join(LEGACY_CONTENT_ROOT, 'images')
    target_images = os.path.join(CONTENT_ROOT, 'images')
    if os.path.isdir(legacy_images) and not os.path.exists(target_images):
        shutil.copytree(legacy_images, target_images)

_bootstrap_content_root()

# --- Admin Credentials ---
# In production, these should be set in environment variables
ADMIN_CREDENTIALS = {
    'name': os.environ.get('ADMIN_NAME', 'Admin'),
    'affiliation': os.environ.get('ADMIN_AFFILIATION', 'Your Lab'),
    'email': os.environ.get('ADMIN_EMAIL', 'admin@example.com')
}

# --- Flask App Initialization ---
app = Flask(__name__)
app.config['ENV'] = 'development' if IS_LOCAL else 'production'
app.config['DEBUG'] = bool(IS_LOCAL)

# --- Security Configuration ---
# Generate/Load secret key for session management without requiring persistent disk
SECRET_KEY_FILE = 'secret_key.bin'
if APP_SECRET_KEY:
    app.secret_key = APP_SECRET_KEY.encode('utf-8')
else:
    legacy_secret_path = os.path.join(PROJECT_ROOT, SECRET_KEY_FILE)
    if os.path.exists(legacy_secret_path):
        try:
            with open(legacy_secret_path, 'rb') as f:
                app.secret_key = f.read()
            print("Using legacy secret_key.bin for session compatibility.")
        except Exception:
            app.secret_key = os.urandom(24)
    else:
        app.secret_key = os.urandom(24)
        if not IS_LOCAL:
            print("APP_SECRET_KEY is not set in deployed mode; using an ephemeral session secret for this run.")

# Cloudflare/R2 disabled — local-only storage and download

# --- Logging Configuration ---
# Safely redefine print with flush=True to avoid log buffering
original_print = print
def debug_print(*args, **kwargs):
    """Print with auto-flush to avoid log buffering"""
    kwargs.setdefault('flush', True)
    try:
        original_print(*args, **kwargs)
    except UnicodeEncodeError:
        stream = kwargs.get('file', sys.stdout)
        encoding = getattr(stream, 'encoding', None) or 'utf-8'
        safe_args = []
        for arg in args:
            text = str(arg)
            safe_args.append(text.encode(encoding, errors='replace').decode(encoding))
        original_print(*safe_args, **kwargs)

print = debug_print

if not app.debug:
    app.logger.addHandler(logging.StreamHandler())
    app.logger.setLevel(logging.DEBUG)

print("🚀 Application started, loading routes...")
print(f"📂 Runtime Root: {PERSISTENT_ROOT}")
print(f"📂 Content Root: {CONTENT_ROOT}")
print(f"📂 Downloads Dir: {PRIVATE_DOWNLOADS_DIR}")
print(f"📂 Logs Dir: {DATA_LOGS_DIR}")
print(f"🗄️ Supabase Logs: {'enabled' if SUPABASE_LOGS_ENABLED and SUPABASE_URL and SUPABASE_SECRET_KEY else 'disabled'}")

# Runtime & Lab info
START_TIME = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
LAB_NAME = "OPTICom Lab"
SITE_CONFIG_PATH = os.path.join(CONTENT_ROOT, 'site.json')
VIEW_LOG_COOLDOWN_SECONDS = 30
PAGE_TYPE_LABELS = {
    'home': 'Home',
    'team': 'People',
    'news': 'News',
    'sources': 'Sources',
    'articles': 'Publications',
    'article_detail': 'Publication Detail',
    'news_detail': 'News Detail',
    'person_detail': 'Profile Detail',
    'register': 'Register'
}
DEFAULT_RESEARCH_HIGHLIGHTS = [
    {
        'title': 'Photonic AI',
        'title_zh': '光子智能',
        'summary': 'Exploring photonic hardware and system design for AI workloads, with a focus on efficient optical-domain inference.',
        'summary_zh': '探索面向智能任务的光子硬件与系统设计，强调高效的光学域推理与计算能力。'
    },
    {
        'title': 'Optical Computing',
        'title_zh': '光计算',
        'summary': 'Studying computation schemes that leverage the parallelism and propagation properties of light to process information.',
        'summary_zh': '研究利用光的并行性与传播特性的计算方案，用于高效率的信息处理。'
    },
    {
        'title': 'Diffractive Networks',
        'title_zh': '衍射网络',
        'summary': 'Investigating diffractive deep neural networks and related free-space optical architectures for compact intelligent systems.',
        'summary_zh': '探索衍射深度神经网络及相关自由空间光学架构，用于构建紧凑型智能系统。'
    },
    {
        'title': 'Integrated Photonics',
        'title_zh': '集成光子',
        'summary': 'Connecting algorithms, devices, and chip-level implementation to build practical intelligent optoelectronic platforms.',
        'summary_zh': '打通算法、器件与芯片级实现，构建实用化的智能光电平台。'
    }
]
DEFAULT_PERSON_TAGS = [
    'Algorithms',
    'Optics',
    'Electronics',
    'Systems',
    'Resources'
]
DEFAULT_ADMISSIONS_DIRECTIONS = [
    {
        'title': 'AI acceleration with photonic chips',
        'title_zh': '光芯片加速 AI',
        'summary': 'Algorithm-oriented research for optical and optoelectronic intelligent computing.',
        'summary_zh': '偏 AI 算法，面向光学与光电智能计算。'
    },
    {
        'title': 'Photonic chip design',
        'title_zh': '光芯片设计',
        'summary': 'Physics- and chip-oriented research on optical computing hardware and AI-assisted chip design.',
        'summary_zh': '偏物理/芯片，面向光计算硬件实现与 AI 辅助芯片设计。'
    },
    {
        'title': 'Chip applications',
        'title_zh': '芯片应用',
        'summary': 'Software-hardware co-design for practical intelligent optoelectronic systems.',
        'summary_zh': '软硬件兼顾，面向智能光电系统应用。'
    }
]
DEFAULT_SITE_VERSION = '1.3.4'
DEFAULT_FRIEND_LINKS = [
    {
        'title': 'SJTU',
        'caption': 'Shanghai Jiao Tong University',
        'url': 'https://www.sjtu.edu.cn/',
        'image_filename': 'sjtu_logo.png'
    },
    {
        'title': 'ICISEE',
        'caption': 'School of Integrated Circuits',
        'url': 'https://icisee.sjtu.edu.cn/',
        'image_filename': 'icisee.jpg'
    },
    {
        'title': 'Contact Us',
        'caption': 'Email the lab',
        'url': 'mailto:yitongchen@sjtu.edu.cn',
        'image_filename': 'friend_link_mail.svg'
    },
    {
        'title': 'GitHub',
        'caption': 'Lab project repository',
        'url': 'https://github.com/Cyh29hao/The-SJTU-Intelligent-Optoelectronic-Computing-Lab',
        'image_filename': 'friend_link_github.svg'
    }
]
DEFAULT_SITE_CONFIG = {
    'home_note': 'Record your information once, then paper and source links open immediately.',
    'home_note_zh': '只需记录一次信息，即可立即打开论文和资源链接。',
    'home_welcome': "Our lab focuses on research in all-optical neural networks, diffractive deep learning, and intelligent photonic chips.\n\nThis website provides publicly available publications, code repositories, and dataset links from our group.",
    'home_welcome_zh': "本课题组主要围绕全光神经网络、衍射深度学习与智能光子芯片开展研究。\n\n本网站汇集了课题组公开发表的论文、代码仓库与数据集链接。",
    'admissions_title': 'Join Us',
    'admissions_title_zh': '招生信息',
    'admissions_intro': "We are recruiting motivated PhD and master's students, postdoctoral researchers, and interns for high-speed, low-power optoelectronic AI chips, AI-assisted chip design, and software-hardware co-design applications.",
    'admissions_intro_zh': '课题组长期招收博士及硕士研究生、博士后、实习生，方向覆盖高速、低功耗光电 AI 芯片及其应用、AI 辅助芯片设计和软硬件协同系统。',
    'admissions_lab_profile': 'The group offers advanced optical experimental platforms, sufficient research support, flexible time arrangements, and active domestic and international collaborations.',
    'admissions_lab_profile_zh': '课题组具备先进光学实验平台、充足科研支持、相对自由的时间安排以及丰富的国内外合作。',
    'admissions_apply': 'Read the full recruitment notice for research directions, candidate expectations, and application materials.',
    'admissions_apply_zh': '查看完整招募新闻，了解研究方向、申请要求和材料清单。',
    'admissions_news_id': 'news_003',
    'admissions_directions': DEFAULT_ADMISSIONS_DIRECTIONS,
    'hero_summary': 'Research in photonic neural networks, intelligent photonic integrated circuits, and open academic resources for optical computing.',
    'hero_summary_zh': '聚焦光子神经网络、智能光子集成电路与面向光计算的开放学术资源。',
    'lab_name': LAB_NAME,
    'lab_name_short': 'OPTICom Lab',
    'lab_name_short_zh': 'OPTICom Lab',
    'lab_name_full': 'Optoelectronic and Photonic Technologies for Intelligent Computing',
    'lab_name_full_zh': 'Optoelectronic and Photonic Technologies for Intelligent Computing',
    'site_version': DEFAULT_SITE_VERSION,
    'show_external_access_note': False,
    'footer_copyright': '2026 OPTICom Lab',
    'footer_copyright_zh': '2026 OPTICom Lab',
    'logo_filename': 'site_logo.svg',
    'friend_links': DEFAULT_FRIEND_LINKS,
    'research_highlights': DEFAULT_RESEARCH_HIGHLIGHTS,
    'person_tags': DEFAULT_PERSON_TAGS
}

SUPPORTED_LANGS = ('en', 'zh')
CATEGORY_TRANSLATIONS = {
    'Professor': {'en': 'Professor', 'zh': '导师'},
    'Postdoc & PhD': {'en': 'Postdoc & PhD', 'zh': '博士后与博士生'},
    'Master': {'en': 'Master', 'zh': '硕士生'},
    'Undergraduate': {'en': 'Undergraduate', 'zh': '本科生'},
    'Alumni': {'en': 'Alumni', 'zh': '往届成员'},
    'Visiting': {'en': 'Visiting', 'zh': '访问成员'},
    'PhD': {'en': 'PhD', 'zh': '博士生'}
}
I18N = {
    'nav_home': {'en': 'Home', 'zh': '首页'},
    'nav_sources': {'en': 'Sources', 'zh': '资源'},
    'nav_publications': {'en': 'Publications', 'zh': '论文'},
    'nav_news': {'en': 'News', 'zh': '新闻'},
    'nav_people': {'en': 'People', 'zh': '成员'},
    'sjtu_full_name': {'en': 'Shanghai Jiao Tong University', 'zh': '上海交通大学'},
    'return_to_admin': {'en': 'Return to Admin', 'zh': '返回后台'},
    'about_lab': {'en': 'About the Lab', 'zh': '课题组简介'},
    'resources': {'en': 'Resources', 'zh': '资源'},
    'selected_publications': {'en': 'Selected Publications', 'zh': '代表性论文'},
    'research_focus': {'en': 'Research Focus', 'zh': '研究方向'},
    'latest_news': {'en': 'Latest News', 'zh': '最新动态'},
    'view_all': {'en': 'View All', 'zh': '查看全部'},
    'view_all_news': {'en': 'View All News', 'zh': '查看全部新闻'},
    'read_article': {'en': 'Read Article', 'zh': '查看论文'},
    'read_more': {'en': 'Read More', 'zh': '阅读更多'},
    'publications_count': {'en': 'Publications', 'zh': '论文'},
    'people_count': {'en': 'People', 'zh': '成员'},
    'page_views': {'en': 'Page Views', 'zh': '浏览量'},
    'login_page': {'en': 'Go to LOGIN page', 'zh': '前往登录页'},
    'current_user': {'en': 'Current user', 'zh': '当前用户'},
    'logged_in_open_note': {'en': 'Information recorded. You can now open paper and source links directly.', 'zh': '信息已记录，现在可以直接打开论文和资源链接。'},
    'authors': {'en': 'Authors', 'zh': '作者'},
    'published_in': {'en': 'Published in', 'zh': '发表于'},
    'last_edited': {'en': 'Last edited', 'zh': '最近更新'},
    'opened_times': {'en': 'Opened {count} times', 'zh': '已打开 {count} 次'},
    'paper_link': {'en': 'Paper Link', 'zh': '论文链接'},
    'official_free_access': {'en': 'Official Free Access', 'zh': '官方免费访问'},
    'resources_link': {'en': 'Resources Link', 'zh': '资源链接'},
    'external_access_note': {'en': 'External links are unlocked after login so the lab can keep lightweight access records without hosting the files locally.', 'zh': '登录后可访问外部链接，这样课题组可以在不托管文件的情况下保留轻量级访问记录。'},
    'back_to_news': {'en': 'Back to News', 'zh': '返回新闻列表'},
    'news_intro': {'en': 'Announcements, milestones, and lab updates.', 'zh': '实验室公告、进展与阶段性动态。'},
    'user_registration': {'en': 'Record Information', 'zh': '信息记录'},
    'register_intro': {'en': 'Please record your information once to access downloadable resources immediately.', 'zh': '请记录一次基本信息，即可立即访问和下载论文与资源。'},
    'register_notice': {'en': 'This is only a lightweight access record, not an account review or approval process. After submitting, the requested link opens immediately.', 'zh': '这里只是用于访问记录（record），不是注册审核流程。提交后会立即打开您要访问的论文或资源链接。'},
    'label_name': {'en': 'Name *', 'zh': '姓名 *'},
    'label_affiliation': {'en': 'Affiliation *', 'zh': '单位 / 身份 *'},
    'label_email': {'en': 'Email *', 'zh': '邮箱 *'},
    'register_consent': {'en': 'I confirm the information provided is accurate and all downloads are for academic use only.', 'zh': '我确认所填信息准确无误，且所有访问与下载仅用于学术用途。'},
    'submit_and_access': {'en': 'Submit and Access Resources', 'zh': '提交并访问资源'},
    'sources_intro': {'en': 'Direct links to code, datasets, supplementary materials, and other downloadable resources shared by the lab.', 'zh': '这里集中列出课题组公开共享的代码、数据集、补充材料等可下载资源。'},
    'source_open_resource': {'en': 'Open Resource', 'zh': '打开资源'},
    'source_record_hint': {'en': 'A one-time information record is required before opening external resource links.', 'zh': '打开外部资源前需要先进行一次信息记录。'},
    'privacy_notice_title': {'en': 'Privacy Notice:', 'zh': '隐私说明：'},
    'privacy_notice_body': {'en': 'Your information is used solely for research collaboration tracking and will not be shared or used commercially.', 'zh': '您提供的信息仅用于科研协作与访问记录统计，不会被共享或用于商业用途。'},
    'no_entries_yet': {'en': 'No entries yet.', 'zh': '暂无内容。'},
    'profile_will_be_updated': {'en': 'Profile will be updated.', 'zh': '个人简介将后续补充。'},
    'email': {'en': 'Email', 'zh': '邮箱'},
    'prof_yitong_chen': {'en': 'Prof. Yitong Chen', 'zh': '陈一彤教授'},
    'developed_by': {'en': 'Developed by', 'zh': '开发者'},
    'footer_school_1': {'en': 'School of Integrated Circuits', 'zh': '集成电路学院'},
    'footer_school_2': {'en': '(School of Information Science and Electronic Engineering)', 'zh': '（信息与电子工程学院）'},
    'footer_school_3': {'en': '(ICISEE), Shanghai Jiao Tong University', 'zh': '（ICISEE），上海交通大学'},
    'footer_school_4': {'en': '800 Dong Chuan Road, Minhang District, Shanghai, China 200240', 'zh': '中国上海市闵行区东川路 800 号，200240'},
    'language_en': {'en': 'EN', 'zh': 'EN'},
    'language_zh': {'en': '中文', 'zh': '中文'}
}


# ==============================================================================
# 3. Helper Functions
# ==============================================================================

ALLOWED_FILE_EXTS = ['.pdf', '.zip', '.npz', '.tar.gz', '.h5', '.mat', '.txt']

def _build_key(resource_id, file_type, ext):
    return f"{resource_id}_{file_type}{ext}"

def _find_existing_ext(resource_id, file_type):
    for ext in ALLOWED_FILE_EXTS:
        key = _build_key(resource_id, file_type, ext)
        local_path = os.path.join(PRIVATE_DOWNLOADS_DIR, key)
        if os.path.isfile(local_path):
            return ext
    return None

def _file_info(resource_id, file_type):
    return {
        'exists': False,
        'ext': '',
        'filename': '',
        'size': None,
        'mtime': None,
        'path': ''
    }

def _human_size(n):
    try:
        for unit in ['B','KB','MB','GB','TB']:
            if n < 1024.0:
                return f"{n:.1f} {unit}"
            n /= 1024.0
    except Exception:
        pass
    return None

def _supabase_logs_ready():
    return bool(SUPABASE_LOGS_ENABLED and SUPABASE_REST_ROOT and SUPABASE_SECRET_KEY)

def _supabase_headers(prefer=None):
    headers = {
        'apikey': SUPABASE_SECRET_KEY,
        'Authorization': f'Bearer {SUPABASE_SECRET_KEY}',
        'Content-Type': 'application/json'
    }
    if prefer:
        headers['Prefer'] = prefer
    return headers

def _supabase_request(method, table_name, params=None, payload=None, prefer=None, timeout=10):
    if not _supabase_logs_ready():
        return None
    try:
        response = requests.request(
            method=method,
            url=f"{SUPABASE_REST_ROOT}/{table_name}",
            headers=_supabase_headers(prefer=prefer),
            params=params,
            json=payload,
            timeout=timeout
        )
        if response.status_code >= 400:
            print(f"Supabase {table_name} {method} failed: {response.status_code} {response.text[:240]}")
            return None
        return response
    except Exception as exc:
        print(f"Supabase request failed for {table_name}: {exc}")
        return None

def _parse_log_datetime(raw_value):
    value = (raw_value or '').strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            pass
    try:
        cleaned = value.replace('Z', '+00:00')
        dt = datetime.fromisoformat(cleaned)
        if getattr(dt, 'tzinfo', None):
            return dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None

def _format_log_timestamp(dt):
    if not dt:
        return ''
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def _load_resource_open_rows_from_csv():
    return []

def _load_page_view_rows_from_csv():
    return []

def _fetch_supabase_rows(table_name, columns):
    rows = []
    if not _supabase_logs_ready():
        return rows
    limit = 1000
    offset = 0
    while True:
        response = _supabase_request(
            'GET',
            table_name,
            params={
                'select': columns,
                'order': 'created_at.asc',
                'limit': limit,
                'offset': offset
            },
            timeout=12
        )
        if response is None:
            return []
        try:
            chunk = response.json()
        except Exception:
            return []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows

def _load_resource_open_rows():
    if _supabase_logs_ready():
        rows = _fetch_supabase_rows(
            'resource_opens',
            'created_at,resource_id,open_type,target_url,user_name,user_affiliation,user_email'
        )
        return [{
            'time': _format_log_timestamp(_parse_log_datetime(row.get('created_at'))),
            'name': row.get('user_name', '') or '',
            'affiliation': row.get('user_affiliation', '') or '',
            'email': row.get('user_email', '') or '',
            'resource_id': row.get('resource_id', '') or '',
            'type': row.get('open_type', '') or '',
            'target_url': row.get('target_url', '') or ''
        } for row in rows]
    return []

def _load_page_view_rows():
    if _supabase_logs_ready():
        rows = _fetch_supabase_rows(
            'page_views',
            'created_at,visitor_id,path,page_type,item_id,title,user_name,user_affiliation,user_email'
        )
        return [{
            'time': _format_log_timestamp(_parse_log_datetime(row.get('created_at'))),
            'visitor_id': row.get('visitor_id', '') or '',
            'path': row.get('path', '') or '',
            'page_type': row.get('page_type', '') or '',
            'item_id': row.get('item_id', '') or '',
            'title': row.get('title', '') or '',
            'name': row.get('user_name', '') or '',
            'affiliation': row.get('user_affiliation', '') or '',
            'email': row.get('user_email', '') or ''
        } for row in rows]
    return []

def _write_supabase_page_view(now, visitor_id, page_type, item_id, title, user_info):
    response = _supabase_request(
        'POST',
        'page_views',
        payload={
            'created_at': now.astimezone().isoformat(),
            'visitor_id': visitor_id,
            'path': request.path,
            'page_type': page_type,
            'item_id': item_id or None,
            'title': title or None,
            'user_name': user_info.get('name') or None,
            'user_affiliation': user_info.get('affiliation') or None,
            'user_email': user_info.get('email') or None
        },
        prefer='return=minimal'
    )
    return response is not None

def _write_supabase_resource_open(now, resource_id, open_type, target_url, user_info):
    response = _supabase_request(
        'POST',
        'resource_opens',
        payload={
            'created_at': now.astimezone().isoformat(),
            'resource_id': resource_id,
            'open_type': open_type,
            'target_url': target_url,
            'user_name': user_info.get('name') or None,
            'user_affiliation': user_info.get('affiliation') or None,
            'user_email': user_info.get('email') or None
        },
        prefer='return=minimal'
    )
    return response is not None

# Phase 2.5: runtime analytics now live only in Supabase, so no local backfill marker is required.

def get_lang():
    lang = (request.args.get('lang') or '').strip().lower()
    if lang in SUPPORTED_LANGS:
        session['lang'] = lang
        return lang
    stored = (session.get('lang') or '').strip().lower()
    return stored if stored in SUPPORTED_LANGS else 'en'

def t(key, lang=None, **kwargs):
    target_lang = lang or get_lang()
    translations = I18N.get(key, {})
    text = translations.get(target_lang) or translations.get('en') or key
    try:
        return text.format(**kwargs)
    except Exception:
        return text

def _lang_value(item, key, lang=None):
    if not isinstance(item, dict):
        return ''
    target_lang = lang or get_lang()
    if target_lang == 'zh':
        zh_value = item.get(f'{key}_zh')
        if isinstance(zh_value, str) and zh_value.strip():
            return zh_value
    value = item.get(key)
    return value if isinstance(value, str) else ''

def category_label(category, lang=None):
    target_lang = lang or get_lang()
    translations = CATEGORY_TRANSLATIONS.get(category or '', {})
    return translations.get(target_lang) or translations.get('en') or category or ''

def person_name(person, lang=None):
    return _lang_value(person, 'name', lang=lang)

def secondary_person_name(person, lang=None):
    if not isinstance(person, dict):
        return ''
    target_lang = lang or get_lang()
    primary = person_name(person, lang=target_lang).strip()
    alternate_lang = 'zh' if target_lang == 'en' else 'en'
    alternate = person_name(person, lang=alternate_lang).strip()
    return alternate if alternate and alternate != primary else ''

def current_path_with_query():
    params = []
    for key in request.args:
        if key == 'lang':
            continue
        for value in request.args.getlist(key):
            params.append((key, value))
    query = urlencode(params, doseq=True)
    if query:
        return f"{request.path}?{query}"
    return request.path or '/'

def switch_language_url(lang):
    return url_for('set_language', lang=lang, next=current_path_with_query())

def _normalize_research_highlights(items):
    normalized = []
    source_items = items if isinstance(items, list) else []
    for index, default_item in enumerate(DEFAULT_RESEARCH_HIGHLIGHTS):
        current = source_items[index] if index < len(source_items) and isinstance(source_items[index], dict) else {}
        title = (current.get('title') or '').strip() or default_item['title']
        title_zh = (current.get('title_zh') or '').strip() or default_item.get('title_zh', '')
        summary = (current.get('summary') or '').strip() or default_item['summary']
        summary_zh = (current.get('summary_zh') or '').strip() or default_item.get('summary_zh', '')
        normalized.append({
            'title': title,
            'title_zh': title_zh,
            'summary': summary,
            'summary_zh': summary_zh
        })
    return normalized

def _normalize_admissions_directions(items):
    normalized = []
    source_items = items if isinstance(items, list) else []
    for index, default_item in enumerate(DEFAULT_ADMISSIONS_DIRECTIONS):
        current = source_items[index] if index < len(source_items) and isinstance(source_items[index], dict) else {}
        normalized.append({
            'title': (current.get('title') or '').strip() or default_item['title'],
            'title_zh': (current.get('title_zh') or '').strip() or default_item.get('title_zh', ''),
            'summary': (current.get('summary') or '').strip() or default_item['summary'],
            'summary_zh': (current.get('summary_zh') or '').strip() or default_item.get('summary_zh', '')
        })
    return normalized

def _normalize_friend_links(items):
    normalized = []
    source_items = items if isinstance(items, list) else []
    for index, default_item in enumerate(DEFAULT_FRIEND_LINKS):
        current = source_items[index] if index < len(source_items) and isinstance(source_items[index], dict) else {}
        title_raw = current['title'] if 'title' in current else default_item['title']
        caption_raw = current['caption'] if 'caption' in current else default_item['caption']
        url_raw = current['url'] if 'url' in current else default_item['url']
        image_raw = current['image_filename'] if 'image_filename' in current else default_item.get('image_filename', '')
        normalized.append({
            'title': (title_raw or '').strip(),
            'caption': (caption_raw or '').strip(),
            'url': (url_raw or '').strip(),
            'image_filename': (image_raw or '').strip()
        })
    return normalized

def _normalize_person_tags(items):
    normalized = []
    source_items = items if isinstance(items, list) else []
    for raw in source_items:
        text = (raw or '').strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized or list(DEFAULT_PERSON_TAGS)

def _run_git_command(args):
    """Run a git command in the project root and capture stdout/stderr safely."""
    try:
        result = subprocess.run(
            ['git', *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        return {
            'ok': result.returncode == 0,
            'code': result.returncode,
            'stdout': (result.stdout or '').strip(),
            'stderr': (result.stderr or '').strip()
        }
    except Exception as exc:
        return {
            'ok': False,
            'code': -1,
            'stdout': '',
            'stderr': str(exc)
        }

def _parse_git_status_lines(raw_status_output):
    changed_files = []
    if not raw_status_output:
        return changed_files
    for raw_line in raw_status_output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        changed_files.append({
            'status': (line[:2] or '').strip() or '??',
            'path': line[2:].strip()
        })
    return changed_files

def _get_local_cms_status():
    """Collect a lightweight snapshot of the local CMS + git workspace status."""
    content_rel = os.path.relpath(CONTENT_ROOT, PROJECT_ROOT)
    status_result = _run_git_command(['status', '--short', '--', content_rel])
    branch_result = _run_git_command(['rev-parse', '--abbrev-ref', 'HEAD'])
    last_commit_result = _run_git_command(['log', '-1', '--pretty=format:%h | %ad | %s', '--date=format-local:%Y-%m-%d %H:%M'])
    remote_result = _run_git_command(['remote', 'get-url', 'origin'])

    changed_files = _parse_git_status_lines(status_result['stdout'] if status_result['ok'] else '')

    return {
        'local_mode': bool(IS_LOCAL),
        'app_mode': APP_MODE,
        'content_root': CONTENT_ROOT,
        'runtime_root': 'No persistent disk',
        'analytics_storage': SUPABASE_URL or 'Supabase not configured',
        'branch': branch_result['stdout'] if branch_result['ok'] else 'unknown',
        'remote': remote_result['stdout'] if remote_result['ok'] else '',
        'last_commit': last_commit_result['stdout'] if last_commit_result['ok'] else 'Unavailable',
        'changed_files': changed_files,
        'has_changes': bool(changed_files),
        'allow_publish': branch_result['ok'] and remote_result['ok'],
        'git_ok': status_result['ok'] and branch_result['ok'],
        'git_error': status_result['stderr'] or branch_result['stderr'] or ''
    }

def _publish_site_content(commit_message):
    """Commit and push git-tracked site_content/ changes from the local CMS."""
    content_rel = os.path.relpath(CONTENT_ROOT, PROJECT_ROOT)
    status_result = _run_git_command(['status', '--short', '--', content_rel])
    if not status_result['ok']:
        return {
            'kind': 'error',
            'message': 'Git status failed before publishing.',
            'details': status_result['stderr'] or status_result['stdout']
        }
    if not status_result['stdout']:
        return {
            'kind': 'info',
            'message': 'No site_content changes were detected, so nothing was pushed.',
            'details': ''
        }

    add_result = _run_git_command(['add', '--all', '--', content_rel])
    if not add_result['ok']:
        return {
            'kind': 'error',
            'message': 'Git add failed while preparing site_content for publish.',
            'details': add_result['stderr'] or add_result['stdout']
        }

    configured_identity = []
    git_name = (_run_git_command(['config', '--local', '--get', 'user.name']).get('stdout') or '').strip()
    git_email = (_run_git_command(['config', '--local', '--get', 'user.email']).get('stdout') or '').strip()
    desired_name = (ADMIN_CREDENTIALS.get('name') or 'Admin').strip()
    desired_email = (ADMIN_CREDENTIALS.get('email') or 'admin@example.com').strip()

    if not git_name:
        set_name = _run_git_command(['config', '--local', 'user.name', desired_name])
        if not set_name['ok']:
            return {
                'kind': 'error',
                'message': 'Git author name could not be configured automatically.',
                'details': set_name['stderr'] or set_name['stdout']
            }
        configured_identity.append(f"user.name={desired_name}")

    if not git_email:
        set_email = _run_git_command(['config', '--local', 'user.email', desired_email])
        if not set_email['ok']:
            return {
                'kind': 'error',
                'message': 'Git author email could not be configured automatically.',
                'details': set_email['stderr'] or set_email['stdout']
            }
        configured_identity.append(f"user.email={desired_email}")

    message = (commit_message or '').strip() or f'Update site content {datetime.now().strftime("%Y-%m-%d %H:%M")}'
    commit_result = _run_git_command(['commit', '-m', message, '--', content_rel])
    if not commit_result['ok']:
        combined_output = '\n'.join(part for part in [commit_result['stdout'], commit_result['stderr']] if part)
        if 'nothing to commit' in combined_output.lower():
            return {
                'kind': 'info',
                'message': 'Git reported nothing to commit after staging.',
                'details': combined_output
            }
        return {
            'kind': 'error',
            'message': 'Git commit failed.',
            'details': combined_output
        }

    push_result = _run_git_command(['push'])
    if not push_result['ok']:
        branch_result = _run_git_command(['rev-parse', '--abbrev-ref', 'HEAD'])
        branch_name = branch_result['stdout'] if branch_result['ok'] else 'main'
        push_result = _run_git_command(['push', '-u', 'origin', branch_name])

    if not push_result['ok']:
        return {
            'kind': 'error',
            'message': 'Git push failed after committing site_content.',
            'details': '\n'.join(part for part in [push_result['stdout'], push_result['stderr']] if part)
        }

    return {
        'kind': 'success',
        'message': 'site_content has been committed and pushed to GitHub.',
        'details': '\n'.join(part for part in [
            ('Auto-configured git identity: ' + ', '.join(configured_identity)) if configured_identity else '',
            commit_result['stdout'],
            push_result['stdout']
        ] if part)
    }

def _get_sync_from_github_status(fetch_remote=False):
    content_rel = os.path.relpath(CONTENT_ROOT, PROJECT_ROOT).replace('\\', '/')
    branch_result = _run_git_command(['rev-parse', '--abbrev-ref', 'HEAD'])
    remote_result = _run_git_command(['remote', 'get-url', 'origin'])
    local_status_result = _run_git_command(['status', '--short', '--', content_rel])

    if not branch_result['ok'] or not remote_result['ok']:
        return {
            'available': False,
            'can_sync': False,
            'kind': 'error',
            'message': 'Git branch or remote could not be detected on this server.',
            'details': '\n'.join(part for part in [branch_result.get('stderr', ''), remote_result.get('stderr', '')] if part),
            'incoming_content_files': [],
            'incoming_code_files': [],
            'local_changes': [],
            'branch': branch_result['stdout'] if branch_result['ok'] else 'unknown'
        }

    branch_name = (branch_result['stdout'] or 'main').strip()
    fetch_result = {'ok': True, 'stdout': '', 'stderr': ''}
    if fetch_remote:
        fetch_result = _run_git_command(['fetch', 'origin', branch_name, '--prune'])
        if not fetch_result['ok']:
            return {
                'available': False,
                'can_sync': False,
                'kind': 'error',
                'message': 'Git fetch failed before checking remote content updates.',
                'details': fetch_result['stderr'] or fetch_result['stdout'],
                'incoming_content_files': [],
                'incoming_code_files': [],
                'local_changes': [],
                'branch': branch_name
            }

    local_changes = _parse_git_status_lines(local_status_result['stdout'] if local_status_result['ok'] else '')

    ahead_behind_result = _run_git_command(['rev-list', '--left-right', '--count', f'HEAD...origin/{branch_name}'])
    ahead_count = 0
    behind_count = 0
    if ahead_behind_result['ok']:
        parts = (ahead_behind_result['stdout'] or '').split()
        if len(parts) >= 2:
            try:
                ahead_count = int(parts[0])
                behind_count = int(parts[1])
            except Exception:
                ahead_count = 0
                behind_count = 0

    diff_result = _run_git_command(['diff', '--name-only', f'HEAD..origin/{branch_name}'])
    remote_changed_files = []
    if diff_result['ok'] and diff_result['stdout']:
        remote_changed_files = [line.strip().replace('\\', '/') for line in diff_result['stdout'].splitlines() if line.strip()]

    incoming_content_files = [path for path in remote_changed_files if path == content_rel or path.startswith(content_rel + '/')]
    incoming_code_files = [path for path in remote_changed_files if path not in incoming_content_files]

    if local_changes:
        return {
            'available': True,
            'can_sync': False,
            'kind': 'info',
            'message': 'Local site_content changes detected. Publish or discard them before syncing from GitHub.',
            'details': '',
            'incoming_content_files': incoming_content_files,
            'incoming_code_files': incoming_code_files,
            'local_changes': local_changes,
            'branch': branch_name,
            'ahead_count': ahead_count,
            'behind_count': behind_count
        }

    if ahead_count > 0:
        return {
            'available': True,
            'can_sync': False,
            'kind': 'info',
            'message': 'This server has local commits ahead of GitHub. Push or reconcile them before syncing down.',
            'details': '',
            'incoming_content_files': incoming_content_files,
            'incoming_code_files': incoming_code_files,
            'local_changes': local_changes,
            'branch': branch_name,
            'ahead_count': ahead_count,
            'behind_count': behind_count
        }

    if not remote_changed_files or behind_count == 0:
        return {
            'available': True,
            'can_sync': False,
            'kind': 'info',
            'message': 'GitHub is already in sync with this server.',
            'details': '',
            'incoming_content_files': incoming_content_files,
            'incoming_code_files': incoming_code_files,
            'local_changes': local_changes,
            'branch': branch_name,
            'ahead_count': ahead_count,
            'behind_count': behind_count
        }

    if incoming_code_files:
        return {
            'available': True,
            'can_sync': False,
            'kind': 'warning',
            'message': 'Incoming GitHub updates include code or template files. Use the normal deploy flow instead of a content-only sync.',
            'details': '\n'.join(incoming_code_files),
            'incoming_content_files': incoming_content_files,
            'incoming_code_files': incoming_code_files,
            'local_changes': local_changes,
            'branch': branch_name,
            'ahead_count': ahead_count,
            'behind_count': behind_count
        }

    return {
        'available': True,
        'can_sync': True,
        'kind': 'success',
        'message': 'GitHub has newer content-only changes that can be fast-forwarded into this server.',
        'details': '',
        'incoming_content_files': incoming_content_files,
        'incoming_code_files': incoming_code_files,
        'local_changes': local_changes,
        'branch': branch_name,
        'ahead_count': ahead_count,
        'behind_count': behind_count
    }

def _sync_site_content_from_github():
    sync_status = _get_sync_from_github_status(fetch_remote=True)
    if not sync_status['can_sync']:
        return {
            'kind': 'error' if sync_status['kind'] == 'error' else 'info',
            'message': sync_status['message'],
            'details': sync_status.get('details', '')
        }

    branch_name = sync_status.get('branch') or 'main'
    pull_result = _run_git_command(['pull', '--ff-only', 'origin', branch_name])
    if not pull_result['ok']:
        return {
            'kind': 'error',
            'message': 'Git sync from GitHub failed during fast-forward pull.',
            'details': '\n'.join(part for part in [pull_result['stdout'], pull_result['stderr']] if part)
        }

    return {
        'kind': 'success',
        'message': 'site_content has been synced from GitHub.',
        'details': pull_result['stdout']
    }

def _normalize_selected_tags(items):
    normalized = []
    source_items = items if isinstance(items, list) else []
    for raw in source_items:
        text = (raw or '').strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized

def _normalize_person_record(item):
    if not isinstance(item, dict):
        return None
    links = []
    for link in item.get('links') or []:
        if not isinstance(link, dict):
            continue
        name = (link.get('name') or '').strip()
        url = (link.get('url') or '').strip()
        if name and url:
            links.append({'name': name, 'url': url})
    return {
        'id': item.get('id', ''),
        'name': (item.get('name') or '').strip(),
        'name_zh': (item.get('name_zh') or '').strip(),
        'category': (item.get('category') or '').strip(),
        'email': (item.get('email') or '').strip(),
        'photo_filename': (item.get('photo_filename') or '').strip(),
        'bio': (item.get('bio') or '').strip(),
        'bio_zh': (item.get('bio_zh') or '').strip(),
        'links': links,
        'tags': _normalize_selected_tags(item.get('tags') or []),
        'last_edited': (item.get('last_edited') or '').strip()
    }

def load_people_data():
    people = load_json_data('people.json')
    normalized = []
    changed = False
    for item in people:
        record = _normalize_person_record(item)
        if record:
            normalized.append(record)
            if record != item:
                changed = True
    if changed:
        save_json_data('people.json', normalized)
    return normalized

def _default_news_items():
    today = datetime.now().strftime("%Y-%m-%d")
    return [{
        'id': 'news_001',
        'title': 'OPTICom Lab website is now online',
        'title_zh': 'OPTICom Lab 网站现已上线',
        'date': today,
        'summary': f'Version {DEFAULT_SITE_VERSION} is now available with publications, people profiles, external resource access, and admin analytics.',
        'summary_zh': f'当前版本 {DEFAULT_SITE_VERSION} 已上线，支持论文页面、成员页面、外部资源访问与后台数据统计。',
        'content': (
            f'Our lab website officially went online on {today}.\n\n'
            f'The current release is Version {DEFAULT_SITE_VERSION}. It includes publication pages, people pages, '
            'external paper and resource access, simple analytics, and a lightweight content-management workflow.\n\n'
            'Welcome to browse the site, read our publications, open available resources, and use the shared materials for academic purposes.'
        ),
        'content_zh': (
            f'本课题组网站已于 {today} 正式上线。\n\n'
            f'当前版本为 {DEFAULT_SITE_VERSION}，已支持论文页面、成员页面、外部论文与资源访问、基础统计功能，以及轻量级本地内容管理流程。\n\n'
            '欢迎浏览网站、阅读论文、访问公开资源，并在学术用途范围内使用共享材料。'
        ),
        'image_filename': '',
        'last_edited': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }]

def _normalize_news_record(item):
    if not isinstance(item, dict):
        return None
    return {
        'id': (item.get('id') or '').strip(),
        'title': (item.get('title') or '').strip(),
        'title_zh': (item.get('title_zh') or '').strip(),
        'date': (item.get('date') or '').strip() or datetime.now().strftime("%Y-%m-%d"),
        'summary': (item.get('summary') or '').strip(),
        'summary_zh': (item.get('summary_zh') or '').strip(),
        'content': (item.get('content') or '').strip(),
        'content_zh': (item.get('content_zh') or '').strip(),
        'image_filename': (item.get('image_filename') or '').strip(),
        'pinned': bool(item.get('pinned')),
        'hide_from_home': bool(item.get('hide_from_home')),
        'last_edited': (item.get('last_edited') or '').strip()
    }

def _news_sort_key(item):
    return (1 if item.get('pinned') else 0, (item.get('date') or ''), (item.get('last_edited') or ''), item.get('id', ''))

def load_news_data():
    path = os.path.join(CONTENT_ROOT, 'news.json')
    if not os.path.exists(path):
        seed = _default_news_items()
        save_json_data('news.json', seed)
        return seed
    news_items = load_json_data('news.json')
    normalized = []
    changed = False
    for item in news_items:
        record = _normalize_news_record(item)
        if record:
            normalized.append(record)
            if record != item:
                changed = True
    if not normalized:
        normalized = _default_news_items()
        changed = True
    normalized.sort(key=_news_sort_key, reverse=True)
    if changed:
        save_json_data('news.json', normalized)
    return normalized

def _read_download_log_summary():
    download_counts = {}
    unique_downloaders = {}
    last_download_times = {}
    for row in _load_resource_open_rows():
        item_id = row.get('resource_id') or ''
        if not item_id:
            continue
        download_counts[item_id] = download_counts.get(item_id, 0) + 1
        key_triplet = (row.get('name', ''), row.get('affiliation', ''), row.get('email', ''))
        unique_downloaders.setdefault(item_id, set()).add(key_triplet)
        stamp = _parse_log_datetime(row.get('time', ''))
        if stamp:
            previous = last_download_times.get(item_id)
            if previous is None or stamp > previous:
                last_download_times[item_id] = stamp

    return {
        'download_counts': download_counts,
        'unique_downloaders': unique_downloaders,
        'last_download_times': last_download_times,
        'total_downloads': sum(download_counts.values())
    }

def _read_page_view_log_summary():
    total_views = 0
    article_view_counts = {}
    for row in _load_page_view_rows():
        total_views += 1
        if (row.get('page_type') or '') == 'article_detail':
            article_id = row.get('item_id') or ''
            if article_id:
                article_view_counts[article_id] = article_view_counts.get(article_id, 0) + 1

    return {
        'total_views': total_views,
        'article_view_counts': article_view_counts
    }

def load_site_config():
    os.makedirs(CONTENT_ROOT, exist_ok=True)
    if not os.path.exists(SITE_CONFIG_PATH):
        try:
            with open(SITE_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_SITE_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to init site config: {e}")
    try:
        with open(SITE_CONFIG_PATH, 'r', encoding='utf-8-sig') as f:
            cfg = json.load(f)
        changed = False
        for key in (
            'home_note', 'home_note_zh',
            'home_welcome', 'home_welcome_zh',
            'admissions_title', 'admissions_title_zh',
            'admissions_intro', 'admissions_intro_zh',
            'admissions_lab_profile', 'admissions_lab_profile_zh',
            'admissions_apply', 'admissions_apply_zh',
            'admissions_news_id',
            'hero_summary', 'hero_summary_zh',
            'lab_name',
            'lab_name_short', 'lab_name_short_zh',
            'lab_name_full', 'lab_name_full_zh',
            'logo_filename',
            'footer_copyright', 'footer_copyright_zh',
            'site_version'
        ):
            if key not in cfg or not isinstance(cfg.get(key), str):
                cfg[key] = DEFAULT_SITE_CONFIG[key]
                changed = True
        if 'show_external_access_note' not in cfg or not isinstance(cfg.get('show_external_access_note'), bool):
            cfg['show_external_access_note'] = DEFAULT_SITE_CONFIG['show_external_access_note']
            changed = True
        normalized_highlights = _normalize_research_highlights(cfg.get('research_highlights'))
        if cfg.get('research_highlights') != normalized_highlights:
            cfg['research_highlights'] = normalized_highlights
            changed = True
        normalized_admissions_directions = _normalize_admissions_directions(cfg.get('admissions_directions'))
        if cfg.get('admissions_directions') != normalized_admissions_directions:
            cfg['admissions_directions'] = normalized_admissions_directions
            changed = True
        normalized_friend_links = _normalize_friend_links(cfg.get('friend_links'))
        if cfg.get('friend_links') != normalized_friend_links:
            cfg['friend_links'] = normalized_friend_links
            changed = True
        normalized_person_tags = _normalize_person_tags(cfg.get('person_tags'))
        if cfg.get('person_tags') != normalized_person_tags:
            cfg['person_tags'] = normalized_person_tags
            changed = True
        if changed:
            save_site_config(cfg)
        return cfg
    except Exception as e:
        print(f"⚠️ Failed to load site config: {e}")
        fallback = dict(DEFAULT_SITE_CONFIG)
        fallback['research_highlights'] = _normalize_research_highlights(DEFAULT_SITE_CONFIG.get('research_highlights'))
        fallback['admissions_directions'] = _normalize_admissions_directions(DEFAULT_SITE_CONFIG.get('admissions_directions'))
        fallback['friend_links'] = _normalize_friend_links(DEFAULT_SITE_CONFIG.get('friend_links'))
        fallback['person_tags'] = _normalize_person_tags(DEFAULT_SITE_CONFIG.get('person_tags'))
        return fallback

def save_site_config(cfg):
    try:
        with open(SITE_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save site config: {e}")

def load_json_data(filename):
    """Safely load JSON list from git-tracked site_content/ directory."""
    os.makedirs(CONTENT_ROOT, exist_ok=True)
    path = os.path.join(CONTENT_ROOT, filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load {filename}: {e}")
        return []

def save_json_data(filename, data):
    """Save admin-managed content into git-tracked site_content/."""
    os.makedirs(CONTENT_ROOT, exist_ok=True)
    path = os.path.join(CONTENT_ROOT, filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _latest_content_modified_time():
    latest_ts = None
    for root, dirs, files in os.walk(CONTENT_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for filename in files:
            path = os.path.join(root, filename)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if latest_ts is None or mtime > latest_ts:
                latest_ts = mtime
    if latest_ts is None:
        return 'N/A'
    return datetime.fromtimestamp(latest_ts).strftime("%Y-%m-%d %H:%M:%S")

def _article_sort_key(item):
    last_edited = item.get('last_edited') or ''
    return (
        -(item.get('year') or 0),
        last_edited,
        item.get('id', '')
    )

def _normalize_article_records(items):
    normalized = []
    changed = False
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        current = dict(item)
        for key in ('paper_url', 'resource_url', 'official_free_access_url', 'venue', 'abstract', 'title', 'thumbnail_filename'):
            if key not in current or not isinstance(current.get(key), str):
                current[key] = '' if key != 'venue' and key != 'abstract' and key != 'title' else current.get(key, '') or ''
                changed = True
        if 'resource_kinds' not in current or not isinstance(current.get('resource_kinds'), list):
            current['resource_kinds'] = ['Code']
            changed = True
        if 'authors_display_count' not in current:
            current['authors_display_count'] = 3
            changed = True
        normalized.append(current)
        if current != item:
            changed = True
    return normalized, changed

def _normalize_home_carousel_flags(articles):
    changed = False
    explicit_flag_count = sum(1 for item in articles if 'featured_on_home' in item)
    ordered_articles = sorted(articles, key=_article_sort_key)

    if explicit_flag_count == 0:
        selected_ids = {item['id'] for item in ordered_articles[:3]}
        for item in articles:
            desired = item.get('id') in selected_ids
            if item.get('featured_on_home') is not desired:
                item['featured_on_home'] = desired
                changed = True
    else:
        for item in articles:
            if 'featured_on_home' not in item:
                item['featured_on_home'] = False
                changed = True

    selected = [item for item in ordered_articles if item.get('featured_on_home')]
    if len(selected) > 5:
        keep_ids = {item['id'] for item in selected[:5]}
        for item in articles:
            if item.get('featured_on_home') and item['id'] not in keep_ids:
                item['featured_on_home'] = False
                changed = True

    return changed

def load_articles_data():
    articles = load_json_data('articles.json')
    articles, changed = _normalize_article_records(articles)
    if _normalize_home_carousel_flags(articles):
        changed = True
    if changed:
        save_json_data('articles.json', articles)
    return articles

@app.context_processor
def inject_site_globals():
    return {
        'site_cfg_global': load_site_config(),
        'current_lang': get_lang(),
        't': t,
        'lang_value': _lang_value,
        'person_name': person_name,
        'secondary_person_name': secondary_person_name,
        'category_label': category_label,
        'switch_language_url': switch_language_url
    }

def _ensure_visitor_id():
    visitor_id = session.get('visitor_id')
    if not visitor_id:
        visitor_id = os.urandom(8).hex()
        session['visitor_id'] = visitor_id
    return visitor_id

def _should_skip_view_log(page_key, now):
    last_key = session.get('last_view_key')
    last_time = session.get('last_view_time')
    if not last_key or not last_time:
        return False
    try:
        last_dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False
    return last_key == page_key and (now - last_dt).total_seconds() < VIEW_LOG_COOLDOWN_SECONDS

def log_page_view(page_type, item_id='', title=''):
    """Append a lightweight page-view record for public pages."""
    if request.method != 'GET':
        return
    now = datetime.now()
    page_key = f"{page_type}:{item_id or request.path}"
    if _should_skip_view_log(page_key, now):
        return

    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    visitor_id = _ensure_visitor_id()
    user_info = session.get('user_info') or {}

    if _supabase_logs_ready():
        _write_supabase_page_view(now, visitor_id, page_type, item_id, title, user_info)
    session['last_view_key'] = page_key
    session['last_view_time'] = timestamp

@app.route('/set-language/<lang>')
def set_language(lang):
    lang = (lang or '').strip().lower()
    if lang in SUPPORTED_LANGS:
        session['lang'] = lang
    next_url = (request.args.get('next') or '').strip()
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = url_for('index')
    return redirect(next_url)

def get_file_status(item_id):
    return {'paper': False, 'resource': False}

def _add_item(item_type, form_data):
    """Helper to add new article"""
    # We only support articles now (merged resources)
    filename = 'articles.json'
    data = load_articles_data()
    
    # Generate new ID
    ids = [int(item['id'].split('_')[-1]) for item in data if '_' in item['id']]
    new_id_num = max(ids) + 1 if ids else 1
    new_id = f"art_{new_id_num:03d}"

    # Parse authors
    authors_str = form_data.get('authors', '').strip()
    authors = [a.strip() for a in authors_str.split(',')] if authors_str else []

    new_item = {
        'id': new_id,
        'title': form_data.get('title', '').strip(),
        'authors': authors,
        'venue': form_data.get('venue', '').strip(),
        'year': int(form_data.get('year', 2025)),
        'abstract': form_data.get('abstract', '').strip(),
        'paper_url': form_data.get('paper_url', '').strip(),
        'official_free_access_url': form_data.get('official_free_access_url', '').strip(),
        'resource_url': form_data.get('resource_url', '').strip(),
        'authors_display_count': int(form_data.get('authors_display_count', 3)),
        'resource_kinds': form_data.getlist('resource_kinds') if hasattr(form_data, 'getlist') and form_data.getlist('resource_kinds') else ['Code'],
        'featured_on_home': (form_data.get('featured_on_home') == 'on'),
        'last_edited': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    data.append(new_item)
    _normalize_home_carousel_flags(data)
    save_json_data(filename, data)
    print(f"✅ Added article: {new_id}")

def _update_item(item_type, item_id, form_data):
    """Helper to update existing article"""
    filename = 'articles.json'
    data = load_articles_data()
    for item in data:
        if item['id'] == item_id:
            item['title'] = form_data.get('title', item['title']).strip()
            authors_str = form_data.get('authors', ', '.join(item['authors'])).strip()
            item['authors'] = [a.strip() for a in authors_str.split(',')] if authors_str else []
            item['year'] = int(form_data.get('year', item['year']))
            item['venue'] = form_data.get('venue', item.get('venue', '')).strip()
            item['abstract'] = form_data.get('abstract', item.get('abstract', '')).strip()
            adc = form_data.get('authors_display_count')
            if adc is not None:
                try:
                    item['authors_display_count'] = int(adc)
                except Exception:
                    pass
            # Optional source links
            paper_url_in = form_data.get('paper_url')
            official_free_access_url_in = form_data.get('official_free_access_url')
            resource_url_in = form_data.get('resource_url')
            if paper_url_in is not None:
                item['paper_url'] = paper_url_in.strip()
            if official_free_access_url_in is not None:
                item['official_free_access_url'] = official_free_access_url_in.strip()
            if resource_url_in is not None:
                item['resource_url'] = resource_url_in.strip()
            # Resource kinds (multi-select)
            if hasattr(form_data, 'getlist'):
                kinds = form_data.getlist('resource_kinds')
                if kinds:
                    item['resource_kinds'] = kinds
            item['featured_on_home'] = (form_data.get('featured_on_home') == 'on')
            item['last_edited'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    _normalize_home_carousel_flags(data)
    save_json_data(filename, data)
    print(f"✏️ Updated article: {item_id}")

def _delete_item(item_type, item_id):
    """Helper to delete article"""
    filename = 'articles.json'
    data = load_json_data(filename)
    data = [item for item in data if item['id'] != item_id]
    save_json_data(filename, data)
    print(f"🗑️ Deleted article: {item_id}")

PEOPLE_IMAGES_DIR = os.path.join(CONTENT_ROOT, 'images', 'people')
os.makedirs(PEOPLE_IMAGES_DIR, exist_ok=True)
ARTICLE_IMAGES_DIR = os.path.join(CONTENT_ROOT, 'images', 'articles')
os.makedirs(ARTICLE_IMAGES_DIR, exist_ok=True)
NEWS_IMAGES_DIR = os.path.join(CONTENT_ROOT, 'images', 'news')
os.makedirs(NEWS_IMAGES_DIR, exist_ok=True)
SITE_IMAGES_DIR = os.path.join(CONTENT_ROOT, 'images')
os.makedirs(SITE_IMAGES_DIR, exist_ok=True)

def _add_person(form_data, photo_file=None):
    filename = 'people.json'
    data = load_people_data()
    ids = [int(item['id'].split('_')[-1]) for item in data if '_' in item['id']]
    new_id_num = max(ids) + 1 if ids else 1
    new_id = f"person_{new_id_num:03d}"
    photo_filename = ''
    if photo_file and photo_file.filename:
        ext = os.path.splitext(secure_filename(photo_file.filename))[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.gif']:
            photo_filename = f"{new_id}{ext}"
            save_path = os.path.join(PEOPLE_IMAGES_DIR, photo_filename)
            photo_file.save(save_path)
    # Extra links (up to 3)
    links = []
    for i in range(1, 4):
        name = (form_data.get(f'link_name_{i}') or '').strip()
        url = (form_data.get(f'link_url_{i}') or '').strip()
        if name and url:
            links.append({'name': name, 'url': url})
    item = {
        'id': new_id,
        'name': form_data.get('name', '').strip(),
        'name_zh': form_data.get('name_zh', '').strip(),
        'category': form_data.get('category', '').strip(),
        'email': form_data.get('email', '').strip(),
        'photo_filename': photo_filename,
        'bio': form_data.get('bio', '').strip(),
        'bio_zh': form_data.get('bio_zh', '').strip(),
        'links': links,
        'tags': _normalize_selected_tags(form_data.getlist('person_tags') if hasattr(form_data, 'getlist') else []),
        'last_edited': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    data.append(item)
    save_json_data(filename, data)
    print(f"✅ Added person: {new_id}")

def _update_person(person_id, form_data, photo_file=None):
    filename = 'people.json'
    data = load_people_data()
    for item in data:
        if item['id'] == person_id:
            item['name'] = form_data.get('name', item.get('name', '')).strip()
            item['name_zh'] = form_data.get('name_zh', item.get('name_zh', '')).strip()
            item['category'] = form_data.get('category', item.get('category', '')).strip()
            item['email'] = form_data.get('email', item.get('email', '')).strip()
            if photo_file and photo_file.filename:
                # remove previous files with any common image ext
                for ext in ['.png', '.jpg', '.jpeg', '.gif']:
                    p = os.path.join(PEOPLE_IMAGES_DIR, f"{person_id}{ext}")
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                ext = os.path.splitext(secure_filename(photo_file.filename))[1].lower()
                if ext in ['.png', '.jpg', '.jpeg', '.gif']:
                    photo_filename = f"{person_id}{ext}"
                    save_path = os.path.join(PEOPLE_IMAGES_DIR, photo_filename)
                    photo_file.save(save_path)
                    item['photo_filename'] = photo_filename
            item['bio'] = form_data.get('bio', item.get('bio', '')).strip()
            item['bio_zh'] = form_data.get('bio_zh', item.get('bio_zh', '')).strip()
            # Update links (up to 3)
            links = []
            for i in range(1, 4):
                name = (form_data.get(f'link_name_{i}') or '').strip()
                url = (form_data.get(f'link_url_{i}') or '').strip()
                if name and url:
                    links.append({'name': name, 'url': url})
            item['links'] = links
            item['tags'] = _normalize_selected_tags(form_data.getlist('person_tags') if hasattr(form_data, 'getlist') else [])
            item['last_edited'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    save_json_data(filename, data)
    print(f"✏️ Updated person: {person_id}")

def _delete_person(person_id):
    filename = 'people.json'
    data = load_people_data()
    data = [item for item in data if item['id'] != person_id]
    save_json_data(filename, data)
    print(f"🗑️ Deleted person: {person_id}")


def _add_news(form_data, image_file=None):
    filename = 'news.json'
    data = load_news_data()
    ids = [int(item['id'].split('_')[-1]) for item in data if '_' in item['id']]
    new_id_num = max(ids) + 1 if ids else 1
    new_id = f"news_{new_id_num:03d}"
    image_filename = ''
    if image_file and image_file.filename:
        ext = os.path.splitext(secure_filename(image_file.filename))[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
            image_filename = f"{new_id}{ext}"
            image_file.save(os.path.join(NEWS_IMAGES_DIR, image_filename))
    item = {
        'id': new_id,
        'title': (form_data.get('title') or '').strip(),
        'title_zh': (form_data.get('title_zh') or '').strip(),
        'date': (form_data.get('date') or '').strip() or datetime.now().strftime("%Y-%m-%d"),
        'summary': (form_data.get('summary') or '').strip(),
        'summary_zh': (form_data.get('summary_zh') or '').strip(),
        'content': (form_data.get('content') or '').strip(),
        'content_zh': (form_data.get('content_zh') or '').strip(),
        'image_filename': image_filename,
        'pinned': form_data.get('pinned') == 'on',
        'hide_from_home': form_data.get('hide_from_home') == 'on',
        'last_edited': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    data.append(item)
    data.sort(key=_news_sort_key, reverse=True)
    save_json_data(filename, data)
    print(f"Added news: {new_id}")

def _update_news(news_id, form_data, image_file=None):
    filename = 'news.json'
    data = load_news_data()
    for item in data:
        if item['id'] != news_id:
            continue
        item['title'] = (form_data.get('title') or item.get('title') or '').strip()
        item['title_zh'] = (form_data.get('title_zh') or item.get('title_zh') or '').strip()
        item['date'] = (form_data.get('date') or item.get('date') or '').strip() or datetime.now().strftime("%Y-%m-%d")
        item['summary'] = (form_data.get('summary') or item.get('summary') or '').strip()
        item['summary_zh'] = (form_data.get('summary_zh') or item.get('summary_zh') or '').strip()
        item['content'] = (form_data.get('content') or item.get('content') or '').strip()
        item['content_zh'] = (form_data.get('content_zh') or item.get('content_zh') or '').strip()
        item['pinned'] = form_data.get('pinned') == 'on'
        item['hide_from_home'] = form_data.get('hide_from_home') == 'on'
        if image_file and image_file.filename:
            for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                path = os.path.join(NEWS_IMAGES_DIR, f"{news_id}{ext}")
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            ext = os.path.splitext(secure_filename(image_file.filename))[1].lower()
            if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                image_filename = f"{news_id}{ext}"
                image_file.save(os.path.join(NEWS_IMAGES_DIR, image_filename))
                item['image_filename'] = image_filename
        item['last_edited'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        break
    data.sort(key=_news_sort_key, reverse=True)
    save_json_data(filename, data)
    print(f"Updated news: {news_id}")

def _delete_news(news_id):
    filename = 'news.json'
    data = [item for item in load_news_data() if item['id'] != news_id]
    for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
        path = os.path.join(NEWS_IMAGES_DIR, f"{news_id}{ext}")
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    save_json_data(filename, data)
    print(f"Deleted news: {news_id}")


# ==============================================================================
# 4. Route Definitions - Public Pages
# ==============================================================================

@app.route('/')
def index():
    site_cfg = load_site_config()
    articles = load_articles_data()
    people = load_people_data()
    news_items = load_news_data()
    latest_news = [item for item in news_items if not item.get('hide_from_home')][:3]
    featured_articles = sorted(
        [item for item in articles if item.get('featured_on_home')],
        key=_article_sort_key
    )[:5]
    log_page_view('home', title='Home')
    page_view_summary = _read_page_view_log_summary()
    return render_template(
        'index.html',
        site_cfg=site_cfg,
        article_count=len(articles),
        people_count=len(people),
        featured_articles=featured_articles,
        total_page_views=page_view_summary['total_views'],
        latest_news=latest_news
    )

@app.route('/team')
def team():
    people = load_people_data()
    site_cfg = load_site_config()
    log_page_view('team', title='People')
    return render_template('team.html', people=people, person_tags=site_cfg.get('person_tags', []))

@app.route('/news')
def news():
    news_items = load_news_data()
    log_page_view('news', title='News')
    return render_template('news.html', news_items=news_items)

@app.route('/sources')
def sources():
    site_cfg = load_site_config()
    articles = load_articles_data()
    source_items = [
        item for item in sorted(articles, key=_article_sort_key)
        if (item.get('resource_url') or '').strip()
    ]
    log_page_view('sources', title='Sources')
    return render_template('sources.html', source_items=source_items, site_cfg=site_cfg)

@app.route('/articles')
def articles():
    site_cfg = load_site_config()
    ARTICLES = load_articles_data()
    sorted_articles = sorted(ARTICLES, key=lambda x: x['year'], reverse=True)
    current_year = datetime.now().year
    min_year = min((item.get('year', current_year) for item in ARTICLES), default=current_year)
    year_groups = []
    for year in range(current_year, min_year - 1, -1):
        year_groups.append({
            'year': year,
            'articles': [item for item in sorted_articles if item.get('year') == year]
        })
    log_page_view('articles', title='Publications')
    return render_template('articles.html', articles=sorted_articles, year_groups=year_groups, site_cfg=site_cfg)

@app.route('/article/<id>')
def article_detail(id):
    ARTICLES = load_articles_data()
    article = next((a for a in ARTICLES if a['id'] == id), None)
    if not article:
        return "Article not found", 404

    download_summary = _read_download_log_summary()
    log_page_view('article_detail', item_id=id, title=article.get('title', id))
    return render_template(
        'article_detail.html',
        item=article,
        open_count=download_summary['download_counts'].get(id, 0)
    )

@app.route('/person/<id>')
def person_detail(id):
    people = load_people_data()
    person = next((p for p in people if p['id'] == id), None)
    if not person:
        return "Person not found", 404
    log_page_view('person_detail', item_id=id, title=person.get('name', id))
    return render_template('person_detail.html', person=person)

@app.route('/news/<id>')
def news_detail(id):
    news_items = load_news_data()
    item = next((n for n in news_items if n['id'] == id), None)
    if not item:
        return "News not found", 404
    log_page_view('news_detail', item_id=id, title=item.get('title', id))
    return render_template('news_detail.html', item=item)
# Resources route removed/merged into articles

@app.route('/test')
def test():
    print("\n🎉 /test page accessed! Flask is running.\n")
    return "✅ Test OK! Check console for real-time output."

# Cloudflare test route removed


# ==============================================================================
# 5. Route Definitions - Authentication
# ==============================================================================

@app.route('/register')
def register():
    """User Login/Register page"""
    session.pop('user_info', None)
    session.pop('registered_at', None)
    next_url = request.args.get('next', '')
    log_page_view('register', title='Register')
    return render_template('register.html', next=next_url)

@app.route('/submit_register', methods=['POST'])
def submit_register():
    """Handle login/register form submission"""
    name = request.form.get('name', '').strip()
    affiliation = request.form.get('affiliation', '').strip()
    email = request.form.get('email', '').strip()
    consent = request.form.get('consent')
    next_url = request.form.get('next', '')

    if not name or not affiliation or not email:
        return "❌ Please fill in all required fields.", 400
    if consent is None:
        return "❌ Please confirm the academic-use consent to proceed.", 400

    # === Admin verification ===
    if (name == ADMIN_CREDENTIALS['name'] and
        affiliation == ADMIN_CREDENTIALS['affiliation'] and
        email == ADMIN_CREDENTIALS['email']):
        session['is_admin'] = True
        session['admin_login_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("✅ Admin login successful")
        return redirect(url_for('admin_dashboard'))

    # === User session ===
    session['user_info'] = {
        'name': name,
        'affiliation': affiliation,
        'email': email
    }
    session['registered_at'] = datetime.now().isoformat()
    print(f"✅ User registered: {name} | {affiliation} | {email}")
    
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    """User logout"""
    session.pop('user_info', None)
    session.pop('registered_at', None)
    session.pop('resource_id', None)  # if exists
    return redirect(url_for('index'))

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('is_admin', None)
    session.pop('admin_login_at', None)
    session.pop('admin_shadow', None)
    session.pop('user_info', None)
    session.pop('registered_at', None)
    session.pop('resource_id', None)
    return redirect(url_for('index'))


# ==============================================================================
# 6. Route Definitions - Protected/User Features
# ==============================================================================

@app.route('/download_file/<file_type>/<resource_id>')
def download_file(file_type, resource_id):
    """Secure download endpoint with logging
       file_type: 'paper' or 'resource'
    """
    user_info = session.get('user_info')
    if not user_info:
        print(f"⚠️ Unregistered user attempted to download {resource_id}, redirecting to login")
        return redirect(url_for('register'))
    
    # Validate file_type
    if file_type not in ['paper', 'resource']:
        return "Invalid file type", 400

    # Locate file locally + throttle checks
    ext = _find_existing_ext(resource_id, file_type)
    if not ext:
        return "❌ Requested resource not found.", 404

    # === Throttle & daily quota ===
    csv_file = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    os.makedirs(DATA_LOGS_DIR, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(csv_file)

    last_same_download = None
    today_total = 0
    if file_exists:
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        t = datetime.strptime(row.get('time',''), "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        continue
                    same_user = (row.get('name') == user_info['name'] and
                                 row.get('affiliation') == user_info['affiliation'] and
                                 row.get('email') == user_info['email'])
                    if same_user:
                        if row.get('resource_id') == resource_id and row.get('type') == file_type:
                            if (now - t).total_seconds() < 60:
                                last_same_download = t
                        if t.date() == now.date():
                            today_total += 1
        except Exception:
            pass
    if today_total >= 10:
        return f"<script>alert('You have reached today\\'s limit of 10 downloads. Please try again tomorrow.'); history.back();</script>", 429
    if last_same_download:
        wait_sec = int(60 - (now - last_same_download).total_seconds())
        if wait_sec < 0: wait_sec = 0
        return f"<script>alert('You\\'ve downloaded this resource within the past minute. Please retry in {wait_sec} seconds.'); history.back();</script>", 429

    # === Safe CSV Logging ===
    try:
        with open(csv_file, 'a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['time', 'name', 'affiliation', 'email', 'resource_id', 'type'])
            writer.writerow([
                timestamp,
                user_info['name'],
                user_info['affiliation'],
                user_info['email'],
                resource_id,
                file_type
            ])
        print(f"📥 Download logged: {user_info['name']} -> {resource_id} ({file_type})")
    except Exception as e:
        print(f"🔴 CSV write failed: {e}")

    key = _build_key(resource_id, file_type, ext)
    actual_path = os.path.join(PRIVATE_DOWNLOADS_DIR, key)
    filename = os.path.basename(actual_path)
    if _supabase_logs_ready():
        _write_supabase_resource_open(now, resource_id, file_type, filename, user_info)
    return send_file(actual_path, as_attachment=True, download_name=filename)

@app.route('/open_link/<link_type>/<resource_id>')
def open_link(link_type, resource_id):
    """Gate external article/resource links behind login while preserving access analytics."""
    user_info = session.get('user_info')
    if not user_info:
        print(f"Unregistered user attempted to open {resource_id}:{link_type}, redirecting to login")
        return redirect(url_for('register', next=request.path))

    if link_type not in ['paper', 'resource', 'official_free_access']:
        return "Invalid link type", 400

    article = next((a for a in load_articles_data() if a.get('id') == resource_id), None)
    if not article:
        return "Article not found", 404

    target_url = ''
    if link_type == 'paper':
        target_url = (article.get('paper_url') or '').strip()
    elif link_type == 'resource':
        target_url = (article.get('resource_url') or '').strip()
    elif link_type == 'official_free_access':
        target_url = (article.get('official_free_access_url') or '').strip()

    if not target_url:
        return "Requested link is not available", 404

    csv_file = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    os.makedirs(DATA_LOGS_DIR, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(csv_file)
    try:
        with open(csv_file, 'a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['time', 'name', 'affiliation', 'email', 'resource_id', 'type'])
            writer.writerow([
                timestamp,
                user_info['name'],
                user_info['affiliation'],
                user_info['email'],
                resource_id,
                link_type
            ])
        print(f"Link open logged: {user_info['name']} -> {resource_id} ({link_type})")
    except Exception as e:
        print(f"Link-open CSV write failed: {e}")

    if _supabase_logs_ready():
        _write_supabase_resource_open(now, resource_id, link_type, target_url, user_info)
    return redirect(target_url)


# ==============================================================================
# 7. Route Definitions - Admin Dashboard
# ==============================================================================

def _admin_module_endpoint(module_name):
    mapping = {
        'home': 'admin_dashboard',
        'content': 'admin_content',
        'analytics': 'admin_analytics',
        'assets': 'admin_assets',
        'settings': 'admin_settings',
    }
    return mapping.get(module_name, 'admin_dashboard')

def _admin_module_url(module_name, anchor='', section=''):
    url = url_for(_admin_module_endpoint(module_name))
    if section:
        joiner = '&' if '?' in url else '?'
        url = f"{url}{joiner}section={section}"
    if anchor:
        return f"{url}{anchor}"
    return url

def _admin_text_value(form, key, current_value, default_value=''):
    if key in form:
        value = (form.get(key) or '').strip()
        return value if value else default_value
    return current_value if current_value is not None else default_value

def _build_admin_common_context(pop_notice=False):
    site_cfg = load_site_config()
    local_cms_status = _get_local_cms_status()
    return {
        'start_time': START_TIME,
        'admin_login_time': session.get('admin_login_at') or START_TIME,
        'content_last_modified': _latest_content_modified_time(),
        'lab_name': site_cfg.get('lab_name_full', LAB_NAME),
        'site_cfg': site_cfg,
        'local_cms_status': local_cms_status,
        'analytics_backend': 'Supabase only' if _supabase_logs_ready() else 'Supabase not configured',
        'admin_notice': session.pop('admin_notice', None) if pop_notice else session.get('admin_notice'),
    }

def _build_admin_analytics_context():
    now = datetime.now()
    today = now.date()
    trend_dates = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    trend_keys = {d.strftime("%Y-%m-%d"): {'date': d, 'views': 0, 'downloads': 0} for d in trend_dates}

    # Load download counts and unique downloaders
    download_counts = {}
    unique_downloaders = {}
    last_download_times = {}
    downloads_last_7_days = 0
    downloads_today = 0
    for row in _load_resource_open_rows():
        item_id = row.get('resource_id')
        if item_id:
            download_counts[item_id] = download_counts.get(item_id, 0) + 1
            key_triplet = (row.get('name',''), row.get('affiliation',''), row.get('email',''))
            s = unique_downloaders.get(item_id)
            if s is None:
                s = set()
                unique_downloaders[item_id] = s
            s.add(key_triplet)
            t = _parse_log_datetime(row.get('time',''))
            if t:
                date_key = t.strftime("%Y-%m-%d")
                if date_key in trend_keys:
                    trend_keys[date_key]['downloads'] += 1
                    downloads_last_7_days += 1
                if t.date() == today:
                    downloads_today += 1
                prev = last_download_times.get(item_id)
                if (prev is None) or (t > prev):
                    last_download_times[item_id] = t

    articles = load_articles_data()
    article_titles = {item['id']: item.get('title', item['id']) for item in articles}

    # Load page view stats
    page_view_counts = {}
    page_view_meta = {}
    article_view_counts = {}
    article_last_view_times = {}
    unique_visitors = set()
    unique_visitors_last_7_days = set()
    views_last_7_days = 0
    views_today = 0
    article_detail_views = 0
    cutoff = now - timedelta(days=7)
    for row in _load_page_view_rows():
        path = row.get('path') or ''
        page_type = row.get('page_type') or ''
        item_id = row.get('item_id') or ''
        title = row.get('title') or PAGE_TYPE_LABELS.get(page_type, path or page_type or 'Unknown')
        visitor_id = row.get('visitor_id') or ''
        stamp = row.get('time') or ''

        if visitor_id:
            unique_visitors.add(visitor_id)

        page_view_counts[path] = page_view_counts.get(path, 0) + 1
        meta = page_view_meta.get(path)
        if meta is None:
            meta = {
                'path': path,
                'label': title,
                'page_type': page_type,
                'count': 0,
                'last_viewed': None
            }
            page_view_meta[path] = meta
        meta['count'] += 1

        viewed_at = _parse_log_datetime(stamp)
        if viewed_at:
            if viewed_at >= cutoff:
                views_last_7_days += 1
                if visitor_id:
                    unique_visitors_last_7_days.add(visitor_id)
            if viewed_at.date() == today:
                views_today += 1
            date_key = viewed_at.strftime("%Y-%m-%d")
            if date_key in trend_keys:
                trend_keys[date_key]['views'] += 1
            prev = meta.get('last_viewed')
            if prev is None or viewed_at > prev:
                meta['last_viewed'] = viewed_at

        if page_type == 'article_detail':
            article_detail_views += 1
            article_view_counts[item_id] = article_view_counts.get(item_id, 0) + 1
            prev = article_last_view_times.get(item_id)
            if viewed_at and (prev is None or viewed_at > prev):
                article_last_view_times[item_id] = viewed_at

    top_pages = sorted(
        page_view_meta.values(),
        key=lambda x: (-x['count'], x['label'])
    )[:5]
    top_articles_by_views = []
    for article_id, count in sorted(article_view_counts.items(), key=lambda x: (-x[1], x[0]))[:5]:
        download_total = download_counts.get(article_id, 0)
        conversion_rate = (download_total / count * 100.0) if count else None
        top_articles_by_views.append({
            'id': article_id,
            'title': article_titles.get(article_id, article_id),
            'count': count,
            'last_viewed': article_last_view_times.get(article_id),
            'downloads': download_total,
            'conversion_rate': conversion_rate
        })

    trend_data = []
    max_views_in_trend = max((item['views'] for item in trend_keys.values()), default=0)
    max_downloads_in_trend = max((item['downloads'] for item in trend_keys.values()), default=0)
    for key in sorted(trend_keys.keys()):
        item = trend_keys[key]
        views = item['views']
        downloads = item['downloads']
        trend_data.append({
            'date': key,
            'label': item['date'].strftime("%m-%d"),
            'views': views,
            'downloads': downloads,
            'views_width': 0 if max_views_in_trend == 0 else max(8, round(views / max_views_in_trend * 100)),
            'downloads_width': 0 if max_downloads_in_trend == 0 else max(8, round(downloads / max_downloads_in_trend * 100))
        })

    article_metrics = []
    for article in articles:
        article_id = article['id']
        view_total = article_view_counts.get(article_id, 0)
        download_total = download_counts.get(article_id, 0)
        unique_user_total = len(unique_downloaders.get(article_id, set()))
        conversion_rate = (download_total / view_total * 100.0) if view_total else None
        article_metrics.append({
            'id': article_id,
            'title': article.get('title', article_id),
            'views': view_total,
            'downloads': download_total,
            'unique_users': unique_user_total,
            'conversion_rate': conversion_rate,
            'last_viewed': article_last_view_times.get(article_id),
            'last_downloaded': last_download_times.get(article_id)
        })
    article_metrics.sort(key=lambda item: (-item['views'], -item['downloads'], item['title']))

    total_downloads = sum(download_counts.values())
    publication_conversion_rate = (total_downloads / article_detail_views * 100.0) if article_detail_views else None
    page_view_stats = {
        'total_views': sum(page_view_counts.values()),
        'unique_visitors': len(unique_visitors),
        'views_last_7_days': views_last_7_days,
        'views_today': views_today,
        'unique_visitors_last_7_days': len(unique_visitors_last_7_days),
        'article_detail_views': article_detail_views,
        'downloads_last_7_days': downloads_last_7_days,
        'downloads_today': downloads_today,
        'publication_conversion_rate': publication_conversion_rate
    }

    sync_down_status = _get_sync_from_github_status(fetch_remote=True)

    return {
        'total_downloads': total_downloads,
        'download_counts': download_counts,
        'last_download_times': last_download_times,
        'page_view_stats': page_view_stats,
        'trend_data': trend_data,
        'top_pages': top_pages,
        'top_articles_by_views': top_articles_by_views,
        'article_view_counts': article_view_counts,
        'article_metrics': article_metrics,
        'sync_down_status': sync_down_status,
    }

def _build_people_photo_context(people):
    people_photo_status = {}
    people_photo_info = {}
    for p in people:
        fname = (p.get('photo_filename') or '').strip()
        if not fname:
            people_photo_status[p['id']] = False
            people_photo_info[p['id']] = {'exists': False}
            continue
        path = os.path.join(PEOPLE_IMAGES_DIR, fname)
        exists = os.path.exists(path)
        people_photo_status[p['id']] = exists
        info = {'exists': exists}
        if exists:
            try:
                st = os.stat(path)
                info['size'] = st.st_size
                info['mtime'] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                info['filename'] = fname
            except Exception:
                pass
        people_photo_info[p['id']] = info
    return {
        'people_photo_status': people_photo_status,
        'people_photo_info': people_photo_info
    }

def _build_admin_content_context():
    articles = load_articles_data()
    people = load_people_data()
    news_items = load_news_data()
    analytics = _build_admin_analytics_context()
    unique_downloaders = {}
    for row in _load_resource_open_rows():
        item_id = row.get('resource_id')
        if item_id:
            unique_downloaders.setdefault(item_id, set()).add((row.get('name',''), row.get('affiliation',''), row.get('email','')))
    return {
        'articles': articles,
        'people': people,
        'news_items': news_items,
        'download_counts': analytics['download_counts'],
        'total_downloads': analytics['total_downloads'],
        'last_download_times': analytics['last_download_times'],
        'unique_counts': {aid: len(unique_downloaders.get(aid, set())) for aid in analytics['download_counts'].keys()},
        'article_view_counts': analytics['article_view_counts'],
        **_build_people_photo_context(people)
    }

def _build_admin_assets_context():
    articles = load_articles_data()
    people = load_people_data()
    return {
        'articles': articles,
        'people': people,
        **_build_people_photo_context(people)
    }

def _build_admin_settings_context():
    return {
        'news_items': load_news_data()
    }

def _handle_admin_actions(default_module):
    action = request.form.get('action') or request.args.get('action')
    item_type = request.form.get('item_type') or request.args.get('item_type')
    if not action:
        return None

    if action == 'publish_content_to_github':
        publish_result = _publish_site_content(request.form.get('commit_message', ''))
        session['admin_notice'] = publish_result
        return redirect(_admin_module_url(default_module, '#git-sync'))

    if action == 'sync_content_from_github':
        sync_result = _sync_site_content_from_github()
        session['admin_notice'] = sync_result
        return redirect(_admin_module_url(default_module, '#git-sync-down'))

    if action == 'add':
        if item_type == 'person':
            _add_person(request.form, request.files.get('photo'))
            return redirect(_admin_module_url('content', '#people-section', 'people'))
        if item_type == 'news':
            _add_news(request.form, request.files.get('image'))
            return redirect(_admin_module_url('content', '#news-section', 'news'))
        _add_item(item_type, request.form)
        return redirect(_admin_module_url('content', '#publications-section', 'publications'))

    if action == 'edit':
        item_id = request.form.get('id')
        if item_type == 'person':
            _update_person(item_id, request.form, request.files.get('photo'))
            return redirect(_admin_module_url('content', f'#person-{item_id}', 'people'))
        if item_type == 'news':
            _update_news(item_id, request.form, request.files.get('image'))
            return redirect(_admin_module_url('content', f'#news-{item_id}', 'news'))
        _update_item(item_type, item_id, request.form)
        return redirect(_admin_module_url('content', f'#article-{item_id}', 'publications'))

    if action == 'delete':
        item_id = request.args.get('id')
        if item_type == 'person':
            _delete_person(item_id)
            return redirect(_admin_module_url('content', '#people-section', 'people'))
        if item_type == 'news':
            _delete_news(item_id)
            return redirect(_admin_module_url('content', '#news-section', 'news'))
        _delete_item(item_type, item_id)
        return redirect(_admin_module_url('content', '#publications-section', 'publications'))

    if action in ('edit_site_welcome', 'edit_site_content', 'edit_site_branding', 'edit_friend_links', 'edit_research_highlights', 'edit_person_tags', 'edit_join_us'):
        cfg = load_site_config()
        if action in ('edit_site_welcome', 'edit_site_content', 'edit_site_branding', 'edit_join_us'):
            cfg['home_welcome'] = _admin_text_value(request.form, 'home_welcome', cfg.get('home_welcome', ''), DEFAULT_SITE_CONFIG['home_welcome'])
            cfg['home_welcome_zh'] = _admin_text_value(request.form, 'home_welcome_zh', cfg.get('home_welcome_zh', ''), DEFAULT_SITE_CONFIG['home_welcome_zh'])
            cfg['home_note'] = _admin_text_value(request.form, 'home_note', cfg.get('home_note', ''), DEFAULT_SITE_CONFIG['home_note'])
            cfg['home_note_zh'] = _admin_text_value(request.form, 'home_note_zh', cfg.get('home_note_zh', ''), DEFAULT_SITE_CONFIG['home_note_zh'])
            cfg['hero_summary'] = _admin_text_value(request.form, 'hero_summary', cfg.get('hero_summary', ''), DEFAULT_SITE_CONFIG['hero_summary'])
            cfg['hero_summary_zh'] = _admin_text_value(request.form, 'hero_summary_zh', cfg.get('hero_summary_zh', ''), DEFAULT_SITE_CONFIG['hero_summary_zh'])
            cfg['lab_name_short'] = _admin_text_value(request.form, 'lab_name_short', cfg.get('lab_name_short', ''), DEFAULT_SITE_CONFIG['lab_name_short'])
            cfg['lab_name_short_zh'] = _admin_text_value(request.form, 'lab_name_short_zh', cfg.get('lab_name_short_zh', ''), DEFAULT_SITE_CONFIG['lab_name_short_zh'])
            cfg['lab_name_full'] = _admin_text_value(request.form, 'lab_name_full', cfg.get('lab_name_full', ''), DEFAULT_SITE_CONFIG['lab_name_full'])
            cfg['lab_name_full_zh'] = _admin_text_value(request.form, 'lab_name_full_zh', cfg.get('lab_name_full_zh', ''), DEFAULT_SITE_CONFIG['lab_name_full_zh'])
            cfg['site_version'] = _admin_text_value(request.form, 'site_version', cfg.get('site_version', ''), DEFAULT_SITE_CONFIG['site_version'])
            if '_show_external_access_note_present' in request.form:
                cfg['show_external_access_note'] = (request.form.get('show_external_access_note') == 'on')
            cfg['footer_copyright'] = _admin_text_value(request.form, 'footer_copyright', cfg.get('footer_copyright', ''), DEFAULT_SITE_CONFIG['footer_copyright'])
            cfg['footer_copyright_zh'] = _admin_text_value(request.form, 'footer_copyright_zh', cfg.get('footer_copyright_zh', ''), DEFAULT_SITE_CONFIG['footer_copyright_zh'])
            cfg['lab_name'] = cfg['lab_name_full']
        if action in ('edit_site_content', 'edit_join_us'):
            cfg['admissions_title'] = _admin_text_value(request.form, 'admissions_title', cfg.get('admissions_title', ''), DEFAULT_SITE_CONFIG['admissions_title'])
            cfg['admissions_title_zh'] = _admin_text_value(request.form, 'admissions_title_zh', cfg.get('admissions_title_zh', ''), DEFAULT_SITE_CONFIG['admissions_title_zh'])
            cfg['admissions_intro'] = _admin_text_value(request.form, 'admissions_intro', cfg.get('admissions_intro', ''), DEFAULT_SITE_CONFIG['admissions_intro'])
            cfg['admissions_intro_zh'] = _admin_text_value(request.form, 'admissions_intro_zh', cfg.get('admissions_intro_zh', ''), DEFAULT_SITE_CONFIG['admissions_intro_zh'])
            cfg['admissions_lab_profile'] = _admin_text_value(request.form, 'admissions_lab_profile', cfg.get('admissions_lab_profile', ''), DEFAULT_SITE_CONFIG['admissions_lab_profile'])
            cfg['admissions_lab_profile_zh'] = _admin_text_value(request.form, 'admissions_lab_profile_zh', cfg.get('admissions_lab_profile_zh', ''), DEFAULT_SITE_CONFIG['admissions_lab_profile_zh'])
            cfg['admissions_apply'] = _admin_text_value(request.form, 'admissions_apply', cfg.get('admissions_apply', ''), DEFAULT_SITE_CONFIG['admissions_apply'])
            cfg['admissions_apply_zh'] = _admin_text_value(request.form, 'admissions_apply_zh', cfg.get('admissions_apply_zh', ''), DEFAULT_SITE_CONFIG['admissions_apply_zh'])
            cfg['admissions_news_id'] = _admin_text_value(request.form, 'admissions_news_id', cfg.get('admissions_news_id', ''), DEFAULT_SITE_CONFIG['admissions_news_id'])
            directions = []
            current_directions = cfg.get('admissions_directions') or []
            for index, default_item in enumerate(DEFAULT_ADMISSIONS_DIRECTIONS, start=1):
                current = current_directions[index - 1] if index - 1 < len(current_directions) and isinstance(current_directions[index - 1], dict) else {}
                directions.append({
                    'title': _admin_text_value(request.form, f'admission_direction_title_{index}', current.get('title', ''), default_item['title']),
                    'title_zh': _admin_text_value(request.form, f'admission_direction_title_zh_{index}', current.get('title_zh', ''), default_item.get('title_zh', '')),
                    'summary': _admin_text_value(request.form, f'admission_direction_summary_{index}', current.get('summary', ''), default_item['summary']),
                    'summary_zh': _admin_text_value(request.form, f'admission_direction_summary_zh_{index}', current.get('summary_zh', ''), default_item.get('summary_zh', ''))
                })
            cfg['admissions_directions'] = _normalize_admissions_directions(directions)
        if action in ('edit_site_content', 'edit_friend_links'):
            friend_links = []
            for index, default_item in enumerate(DEFAULT_FRIEND_LINKS, start=1):
                current_links = cfg.get('friend_links') or []
                current = current_links[index - 1] if index - 1 < len(current_links) and isinstance(current_links[index - 1], dict) else {}
                friend_links.append({
                    'title': _admin_text_value(request.form, f'friend_title_{index}', current.get('title', ''), ''),
                    'caption': _admin_text_value(request.form, f'friend_caption_{index}', current.get('caption', ''), ''),
                    'url': _admin_text_value(request.form, f'friend_url_{index}', current.get('url', ''), ''),
                    'image_filename': (current.get('image_filename') or '').strip()
                })
            cfg['friend_links'] = _normalize_friend_links(friend_links)
        if action in ('edit_site_content', 'edit_research_highlights'):
            highlights = []
            current_highlights = cfg.get('research_highlights') or []
            for index, default_item in enumerate(DEFAULT_RESEARCH_HIGHLIGHTS, start=1):
                current = current_highlights[index - 1] if index - 1 < len(current_highlights) and isinstance(current_highlights[index - 1], dict) else {}
                highlights.append({
                    'title': _admin_text_value(request.form, f'highlight_title_{index}', current.get('title', ''), default_item['title']),
                    'title_zh': _admin_text_value(request.form, f'highlight_title_zh_{index}', current.get('title_zh', ''), default_item.get('title_zh', '')),
                    'summary': _admin_text_value(request.form, f'highlight_summary_{index}', current.get('summary', ''), default_item['summary']),
                    'summary_zh': _admin_text_value(request.form, f'highlight_summary_zh_{index}', current.get('summary_zh', ''), default_item.get('summary_zh', ''))
                })
            cfg['research_highlights'] = _normalize_research_highlights(highlights)
        if action in ('edit_site_content', 'edit_person_tags') and 'person_tags_text' in request.form:
            raw_text = request.form.get('person_tags_text', '')
            cfg['person_tags'] = _normalize_person_tags(raw_text.splitlines())
        save_site_config(cfg)

        if action == 'edit_site_welcome':
            return redirect(_admin_module_url('content', '#homepage-copy-section', 'homepage'))
        if action == 'edit_site_branding':
            return redirect(_admin_module_url('settings', '#branding-section', 'branding'))
        if action == 'edit_friend_links':
            return redirect(_admin_module_url('settings', '#friendly-links-section', 'friendly-links'))
        if action == 'edit_research_highlights':
            return redirect(_admin_module_url('settings', '#research-focus-section', 'research-focus'))
        if action == 'edit_person_tags':
            return redirect(_admin_module_url('settings', '#person-tags-section', 'person-tags'))
        if action == 'edit_join_us':
            return redirect(_admin_module_url('content', '#join-us-section', 'join-us'))
        return redirect(_admin_module_url(default_module))

    return None

@app.route('/admin', methods=['GET', 'POST'])
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('register'))
    response = _handle_admin_actions('home')
    if response:
        return response
    return render_template(
        'admin_home.html',
        active_module='home',
        **_build_admin_common_context(pop_notice=True)
    )

@app.route('/admin/content', methods=['GET', 'POST'])
def admin_content():
    if not session.get('is_admin'):
        return redirect(url_for('register'))
    response = _handle_admin_actions('content')
    if response:
        return response
    return render_template(
        'admin_content.html',
        active_module='content',
        module_endpoint='admin_content',
        **_build_admin_common_context(pop_notice=True),
        **_build_admin_content_context()
    )

@app.route('/admin/analytics', methods=['GET', 'POST'])
def admin_analytics():
    if not session.get('is_admin'):
        return redirect(url_for('register'))
    response = _handle_admin_actions('analytics')
    if response:
        return response
    return render_template(
        'admin_analytics.html',
        active_module='analytics',
        **_build_admin_common_context(pop_notice=True),
        **_build_admin_analytics_context()
    )

@app.route('/admin/assets')
def admin_assets():
    if not session.get('is_admin'):
        return redirect(url_for('register'))
    return render_template(
        'admin_assets.html',
        active_module='assets',
        **_build_admin_common_context(pop_notice=True),
        **_build_admin_assets_context()
    )

@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if not session.get('is_admin'):
        return redirect(url_for('register'))
    response = _handle_admin_actions('settings')
    if response:
        return response
    return render_template(
        'admin_settings.html',
        active_module='settings',
        module_endpoint='admin_settings',
        **_build_admin_common_context(pop_notice=True),
        **_build_admin_settings_context()
    )

@app.route('/admin/view-as-user')
def admin_view_as_user():
    if not session.get('is_admin'):
        return redirect(url_for('register'))
    session['admin_shadow'] = True
    session['is_admin'] = False
    session['user_info'] = {
        'name': ADMIN_CREDENTIALS.get('name','Admin'),
        'affiliation': ADMIN_CREDENTIALS.get('affiliation','OPTICom Lab'),
        'email': ADMIN_CREDENTIALS.get('email','admin@example.com')
    }
    return redirect(url_for('index'))

@app.route('/admin/return')
def admin_return():
    if session.get('admin_shadow'):
        session['is_admin'] = True
        session['admin_shadow'] = False
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('index'))
@app.route('/admin/upload/<file_type>/<resource_id>', methods=['POST'])
def admin_upload_file(file_type, resource_id):
    """Admin file upload handler"""
    if not session.get('is_admin'):
        return "Unauthorized", 403

    if file_type not in ['paper', 'resource']:
        return "Invalid file type", 400

    # Check if article exists
    articles = load_articles_data()
    target = next((a for a in articles if a['id'] == resource_id), None)

    if not target:
        return f"ID '{resource_id}' not found", 404

    file = request.files.get('file')
    if not file or not file.filename:
        return "No file selected", 400

    # Allow only safe extensions
    ALLOWED_EXTENSIONS = set(ALLOWED_FILE_EXTS)
    filename = file.filename
    ext = ''
    for allowed in sorted(ALLOWED_EXTENSIONS, key=len, reverse=True):
        if filename.lower().endswith(allowed):
            ext = allowed
            break
    if not ext:
        return f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}", 400

    key = _build_key(resource_id, file_type, ext)
    os.makedirs(PRIVATE_DOWNLOADS_DIR, exist_ok=True)
    base_name = f"{resource_id}_{file_type}"
    for e in ALLOWED_EXTENSIONS:
        p = os.path.join(PRIVATE_DOWNLOADS_DIR, base_name + e)
        if os.path.exists(p):
            os.remove(p)
    save_path = os.path.join(PRIVATE_DOWNLOADS_DIR, key)
    file.save(save_path)
    print(f"✅ Admin uploaded {file_type} for {resource_id} -> {save_path}")

    target['last_edited'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Save back
    # We need to find index
    for i, item in enumerate(articles):
        if item['id'] == resource_id:
            articles[i] = target
            break
    save_json_data('articles.json', articles)

    redirect_module = request.args.get('redirect_module') or request.form.get('redirect_module') or 'content'
    return redirect(_admin_module_url(redirect_module, '#publications-section'))

@app.route('/admin/upload-thumb/<article_id>', methods=['POST'])
def admin_upload_thumbnail(article_id):
    if not session.get('is_admin'):
        return "Unauthorized", 403
    articles = load_articles_data()
    target = next((a for a in articles if a['id'] == article_id), None)
    if not target:
        return "Article not found", 404
    thumb = request.files.get('thumbnail')
    if not thumb or not thumb.filename:
        return "No file selected", 400
    os.makedirs(ARTICLE_IMAGES_DIR, exist_ok=True)
    # remove previous variants
    for ext in ['.png','.jpg','.jpeg','.gif']:
        p = os.path.join(ARTICLE_IMAGES_DIR, f"{article_id}{ext}")
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    ext = os.path.splitext(secure_filename(thumb.filename))[1].lower()
    if ext not in ['.png','.jpg','.jpeg','.gif']:
        return "File type not allowed for thumbnail", 400
    filename = f"{article_id}{ext}"
    save_path = os.path.join(ARTICLE_IMAGES_DIR, filename)
    thumb.save(save_path)
    target['thumbnail_filename'] = filename
    target['last_edited'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i, a in enumerate(articles):
        if a['id'] == article_id:
            articles[i] = target
            break
    save_json_data('articles.json', articles)
    redirect_module = request.args.get('redirect_module') or request.form.get('redirect_module') or 'assets'
    anchor = f'#thumb-{article_id}' if redirect_module == 'assets' else f'#article-{article_id}'
    section = 'thumbnails' if redirect_module == 'assets' else 'publications'
    return redirect(_admin_module_url(redirect_module, anchor, section))

@app.route('/admin/upload-site-image/<slot>', methods=['POST'])
def admin_upload_site_image(slot):
    if not session.get('is_admin'):
        return "Unauthorized", 403

    image = request.files.get('image')
    if not image or not image.filename:
        return "No file selected", 400

    ext = os.path.splitext(secure_filename(image.filename))[1].lower()
    if ext not in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']:
        return "File type not allowed for site image", 400

    cfg = load_site_config()
    os.makedirs(SITE_IMAGES_DIR, exist_ok=True)

    if slot == 'logo':
        base_name = 'site_logo'
        for old_ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']:
            old_path = os.path.join(SITE_IMAGES_DIR, base_name + old_ext)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        filename = base_name + ext
        image.save(os.path.join(SITE_IMAGES_DIR, filename))
        cfg['logo_filename'] = filename
    else:
        if not slot.startswith('friend-'):
            return "Invalid site image slot", 400
        try:
            index = int(slot.split('-')[-1]) - 1
        except Exception:
            return "Invalid site image slot", 400
        links = _normalize_friend_links(cfg.get('friend_links'))
        if index < 0 or index >= len(links):
            return "Invalid site image slot", 400
        base_name = f"friend_link_{index + 1}"
        for old_ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']:
            old_path = os.path.join(SITE_IMAGES_DIR, base_name + old_ext)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        filename = base_name + ext
        image.save(os.path.join(SITE_IMAGES_DIR, filename))
        links[index]['image_filename'] = filename
        cfg['friend_links'] = links

    save_site_config(cfg)
    redirect_module = request.args.get('redirect_module') or request.form.get('redirect_module') or 'assets'
    section = 'site-images' if redirect_module == 'assets' else 'branding'
    return redirect(_admin_module_url(redirect_module, '#site-images', section))

# ==============================================================================
# 8. Asset Serving (from render_data)
# ==============================================================================
from flask import abort

@app.route('/assets/people/<filename>')
def asset_people(filename):
    path = os.path.join(PEOPLE_IMAGES_DIR, filename)
    if not os.path.exists(path):
        return abort(404)
    return send_file(path)

@app.route('/assets/articles/<filename>')
def asset_article_thumb(filename):
    path = os.path.join(ARTICLE_IMAGES_DIR, filename)
    if not os.path.exists(path):
        return abort(404)
    return send_file(path)

@app.route('/assets/news/<filename>')
def asset_news_image(filename):
    path = os.path.join(NEWS_IMAGES_DIR, filename)
    if not os.path.exists(path):
        fallback = os.path.join(app.static_folder, 'images', 'placeholder-news.svg')
        if os.path.exists(fallback):
            return send_file(fallback)
        return abort(404)
    return send_file(path)

@app.route('/assets/site/<filename>')
def asset_site_image(filename):
    path = os.path.join(SITE_IMAGES_DIR, filename)
    if not os.path.exists(path):
        fallback = os.path.join(app.static_folder, 'images', 'placeholder-site.svg')
        if os.path.exists(fallback):
            return send_file(fallback)
        return abort(404)
    return send_file(path)

@app.route('/admin/download-site-content.zip')
def download_site_content_zip():
    """Package the git-tracked site_content folder and download as ZIP."""
    if not session.get('is_admin'):
        return "Unauthorized", 403
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(CONTENT_ROOT):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for name in files:
                fpath = os.path.join(root, name)
                rel = os.path.relpath(fpath, CONTENT_ROOT)
                zf.write(fpath, arcname=rel)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='site_content_bundle.zip', mimetype='application/zip')

@app.route('/admin/download-logs.csv')
def download_logs_csv():
    """Export download logs as CSV"""
    if not session.get('is_admin'):
        return "Unauthorized", 403
    csv_path = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    os.makedirs(DATA_LOGS_DIR, exist_ok=True)
    if not os.path.exists(csv_path):
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write('time,name,affiliation,email,resource_id,type\n')
    return send_file(csv_path, as_attachment=True, download_name='lightchip_download_logs.csv')

@app.route('/admin/download-page-views.csv')
def download_page_views_csv():
    """Export page view logs as CSV"""
    if not session.get('is_admin'):
        return "Unauthorized", 403
    os.makedirs(DATA_LOGS_DIR, exist_ok=True)
    if not os.path.exists(PAGE_VIEWS_CSV_PATH):
        with open(PAGE_VIEWS_CSV_PATH, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'time', 'visitor_id', 'path', 'page_type', 'item_id',
                'title', 'name', 'affiliation', 'email'
            ])
    return send_file(PAGE_VIEWS_CSV_PATH, as_attachment=True, download_name='lightchip_page_views.csv')

@app.route('/admin/download-render-data.zip')
def download_render_data_zip():
    """Package runtime-only render_data/ (logs + legacy runtime files) and download as ZIP."""
    if not session.get('is_admin'):
        return "Unauthorized", 403
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PERSISTENT_ROOT):
            for name in files:
                fpath = os.path.join(root, name)
                rel = os.path.relpath(fpath, PERSISTENT_ROOT)
                zf.write(fpath, arcname=rel)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='runtime_data_bundle.zip', mimetype='application/zip')

@app.route('/admin/upload-render-data', methods=['POST'])
def upload_render_data_zip():
    if not session.get('is_admin'):
        return "Unauthorized", 403
    f = request.files.get('bundle')
    if not f or not f.filename:
        return "No file selected", 400
    name = secure_filename(f.filename)
    if not name.lower().endswith('.zip'):
        return "Only .zip allowed", 400
    import io, shutil, zipfile
    buf = io.BytesIO(f.read())
    root_abs = os.path.abspath(PERSISTENT_ROOT)
    tmp_dir = os.path.join(BASE_ROOT, '_render_data_import_tmp')
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    extract_root = os.path.join(tmp_dir, 'render_data_import')
    extract_root_abs = os.path.abspath(extract_root)
    os.makedirs(extract_root, exist_ok=True)
    try:
        with zipfile.ZipFile(buf, 'r') as zf:
            for member in zf.namelist():
                if member.endswith('/'):
                    dest_dir = os.path.abspath(os.path.join(extract_root, member))
                    if dest_dir.startswith(extract_root_abs):
                        os.makedirs(dest_dir, exist_ok=True)
                    continue
                dest_path = os.path.abspath(os.path.join(extract_root, member))
                if not dest_path.startswith(extract_root_abs):
                    continue
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with zf.open(member) as src, open(dest_path, 'wb') as out:
                    out.write(src.read())

        os.makedirs(PERSISTENT_ROOT, exist_ok=True)
        for name in os.listdir(PERSISTENT_ROOT):
            target = os.path.join(PERSISTENT_ROOT, name)
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)

        for name in os.listdir(extract_root):
            source = os.path.join(extract_root, name)
            target = os.path.join(PERSISTENT_ROOT, name)
            if os.path.isdir(source):
                shutil.copytree(source, target)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
    finally:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
    return redirect(url_for('admin_dashboard'))




# Cloudflare upload API removed — using only local admin upload


# ==========================================================================
# Phase 2.5 overrides - diskless analytics & compatibility shells
# ==========================================================================

def _csv_bytes(fieldnames, rows):
    text_buffer = io.StringIO()
    writer = csv.DictWriter(text_buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, '') for key in fieldnames})
    return io.BytesIO(text_buffer.getvalue().encode('utf-8-sig'))

def _download_logs_csv_supabase_only():
    if not session.get('is_admin'):
        return "Unauthorized", 403
    rows = _load_resource_open_rows()
    return send_file(
        _csv_bytes(
            ['time', 'name', 'affiliation', 'email', 'resource_id', 'type', 'target_url'],
            rows
        ),
        as_attachment=True,
        download_name='lightchip_resource_opens.csv',
        mimetype='text/csv'
    )

def _download_page_views_csv_supabase_only():
    if not session.get('is_admin'):
        return "Unauthorized", 403
    rows = _load_page_view_rows()
    return send_file(
        _csv_bytes(
            ['time', 'visitor_id', 'path', 'page_type', 'item_id', 'title', 'name', 'affiliation', 'email'],
            rows
        ),
        as_attachment=True,
        download_name='lightchip_page_views.csv',
        mimetype='text/csv'
    )

def _download_analytics_bundle_supabase_only():
    if not session.get('is_admin'):
        return "Unauthorized", 403
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            'resource_opens.csv',
            _csv_bytes(
                ['time', 'name', 'affiliation', 'email', 'resource_id', 'type', 'target_url'],
                _load_resource_open_rows()
            ).getvalue()
        )
        zf.writestr(
            'page_views.csv',
            _csv_bytes(
                ['time', 'visitor_id', 'path', 'page_type', 'item_id', 'title', 'name', 'affiliation', 'email'],
                _load_page_view_rows()
            ).getvalue()
        )
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name='analytics_export_bundle.zip',
        mimetype='application/zip'
    )

def _upload_runtime_bundle_retired():
    if not session.get('is_admin'):
        return "Unauthorized", 403
    session['admin_notice'] = {
        'kind': 'info',
        'message': 'Runtime ZIP upload has been retired in Version 1.2.0.',
        'details': 'Analytics now live in Supabase, and editable site content already lives in git-tracked site_content/.'
    }
    return redirect(url_for('admin_dashboard') + '#local-cms-panel')

def _open_link_supabase_only(link_type, resource_id):
    user_info = session.get('user_info')
    if not user_info:
        return redirect(url_for('register', next=request.path))
    if link_type not in ['paper', 'resource', 'official_free_access']:
        return "Invalid link type", 400

    article = next((a for a in load_articles_data() if a.get('id') == resource_id), None)
    if not article:
        return "Article not found", 404

    if link_type == 'paper':
        target_url = (article.get('paper_url') or '').strip()
    elif link_type == 'resource':
        target_url = (article.get('resource_url') or '').strip()
    else:
        target_url = (article.get('official_free_access_url') or '').strip()

    if not target_url:
        return "Requested link is not available", 404

    if _supabase_logs_ready():
        _write_supabase_resource_open(datetime.now(), resource_id, link_type, target_url, user_info)
    return redirect(target_url)

def _download_file_compat(file_type, resource_id):
    return redirect(url_for('open_link', link_type=file_type, resource_id=resource_id))

def _admin_upload_file_retired(file_type, resource_id):
    if not session.get('is_admin'):
        return "Unauthorized", 403
    redirect_module = request.args.get('redirect_module') or request.form.get('redirect_module') or 'assets'
    session['admin_notice'] = {
        'kind': 'info',
        'message': 'Local paper/resource file uploads have been retired in Version 1.2.0.',
        'details': 'Please use Paper URL, Official Free Access URL, and Resources URL instead. This keeps the site diskless and easier to maintain.'
    }
    return redirect(_admin_module_url(redirect_module, '#site-images', 'site-images'))

app.view_functions['download_logs_csv'] = _download_logs_csv_supabase_only
app.view_functions['download_page_views_csv'] = _download_page_views_csv_supabase_only
app.view_functions['download_render_data_zip'] = _download_analytics_bundle_supabase_only
app.view_functions['upload_render_data_zip'] = _upload_runtime_bundle_retired
app.view_functions['open_link'] = _open_link_supabase_only
app.view_functions['download_file'] = _download_file_compat
app.view_functions['admin_upload_file'] = _admin_upload_file_retired


# ==============================================================================
# 8. Main Entry Point
# ==============================================================================

if __name__ == '__main__':
    print(" Current time: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("🔧 Starting Flask development server...")
    print("🛑 Press Ctrl+C to stop")
    
    if IS_LOCAL:
        print(f"Local URL: http://{LOCAL_HOST}:{LOCAL_PORT}")
        app.run(host=LOCAL_HOST, port=LOCAL_PORT, debug=True, use_reloader=True)
    else:
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port, debug=False)
