# app.py - English Version (Fully Internationalized)
# Refactored for readability and structure

# ==============================================================================
# 1. Imports
# ==============================================================================
import csv
import json
import logging
import os
import sys
from datetime import datetime, timedelta
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
IS_LOCAL = 0  # Set to 0 for production/deployment
BASE_ROOT = os.environ.get('PERSISTENT_ROOT', '.').strip()
PERSISTENT_ROOT = os.path.join(BASE_ROOT, 'render_data')
LOCAL_HOST = (os.environ.get('LOCAL_HOST', '127.0.0.1') or '127.0.0.1').strip()
LOCAL_PORT = int(os.environ.get('LOCAL_PORT') or os.environ.get('PORT') or 5000)

# --- Directory Paths ---
PRIVATE_DOWNLOADS_DIR = os.path.join(PERSISTENT_ROOT, 'private_downloads')
DATA_LOGS_DIR = os.path.join(PERSISTENT_ROOT, 'data_logs')

# Ensure directories exist (Safety check on every launch)
os.makedirs(PRIVATE_DOWNLOADS_DIR, exist_ok=True)
os.makedirs(DATA_LOGS_DIR, exist_ok=True)

# --- Admin Credentials ---
# In production, these should be set in environment variables
ADMIN_CREDENTIALS = {
    'name': os.environ.get('ADMIN_NAME', 'Admin'),
    'affiliation': os.environ.get('ADMIN_AFFILIATION', 'Your Lab'),
    'email': os.environ.get('ADMIN_EMAIL', 'admin@example.com')
}

# --- Flask App Initialization ---
app = Flask(__name__)
app.config['ENV'] = 'development'
app.config['DEBUG'] = True

# --- Security Configuration ---
# Generate/Load secret key for session management
SECRET_KEY_FILE = 'secret_key.bin'
if IS_LOCAL:
    # Regenerate secret key on every launch in local mode (invalidates old sessions)
    app.secret_key = os.urandom(24)
else:
    # Use persistent secret key for production
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'rb') as f:
            secret_key = f.read()
    else:
        secret_key = os.urandom(24)
        with open(SECRET_KEY_FILE, 'wb') as f:
            f.write(secret_key)
    app.secret_key = secret_key

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
print(f"📂 Persistent Root: {PERSISTENT_ROOT}")
print(f"📂 Downloads Dir: {PRIVATE_DOWNLOADS_DIR}")
print(f"📂 Logs Dir: {DATA_LOGS_DIR}")

# Runtime & Lab info
START_TIME = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
LAB_NAME = "Intelligent Optoelectronic Computing Lab"
SITE_CONFIG_PATH = os.path.join(PERSISTENT_ROOT, 'site.json')
PAGE_VIEWS_CSV_PATH = os.path.join(DATA_LOGS_DIR, 'page_views.csv')
VIEW_LOG_COOLDOWN_SECONDS = 30
PAGE_TYPE_LABELS = {
    'home': 'Home',
    'team': 'People',
    'news': 'News',
    'articles': 'Publications',
    'article_detail': 'Publication Detail',
    'news_detail': 'News Detail',
    'person_detail': 'Profile Detail',
    'register': 'Register'
}
DEFAULT_RESEARCH_HIGHLIGHTS = [
    {
        'title': 'Photonic AI',
        'summary': 'Exploring photonic hardware and system design for AI workloads, with a focus on efficient optical-domain inference.'
    },
    {
        'title': 'Optical Computing',
        'summary': 'Studying computation schemes that leverage the parallelism and propagation properties of light to process information.'
    },
    {
        'title': 'Diffractive Networks',
        'summary': 'Investigating diffractive deep neural networks and related free-space optical architectures for compact intelligent systems.'
    },
    {
        'title': 'Integrated Photonics',
        'summary': 'Connecting algorithms, devices, and chip-level implementation to build practical intelligent optoelectronic platforms.'
    }
]
DEFAULT_PERSON_TAGS = [
    'Algorithms',
    'Optics',
    'Electronics',
    'Systems',
    'Resources'
]
DEFAULT_SITE_VERSION = '1.0.1'
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
        'image_filename': 'friend_link_icisee.svg'
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
        'url': 'https://github.com/Cyh29hao/0129_YitongChen_lightchip_lab_website_draft',
        'image_filename': 'friend_link_github.svg'
    }
]
DEFAULT_SITE_CONFIG = {
    'home_note': 'To download our resources, please first fill in your information on the Login page.',
    'home_welcome': "Our lab focuses on research in all-optical neural networks, diffractive deep learning, and intelligent photonic chips.\n\nThis website provides publicly available publications, code, and datasets from our group.",
    'hero_summary': 'Research in photonic neural networks, intelligent photonic integrated circuits, and open academic resources for optical computing.',
    'lab_name': LAB_NAME,
    'lab_name_short': 'SJTU IOC Lab',
    'lab_name_full': 'the SJTU Intelligent Optoelectronic Computing Lab',
    'site_version': DEFAULT_SITE_VERSION,
    'footer_copyright': '2026 AI Intelligent Optoelectronic Computing Lab',
    'logo_filename': 'site_logo.svg',
    'friend_links': DEFAULT_FRIEND_LINKS,
    'research_highlights': DEFAULT_RESEARCH_HIGHLIGHTS,
    'person_tags': DEFAULT_PERSON_TAGS
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
    ext = _find_existing_ext(resource_id, file_type)
    if not ext:
        return {'exists': False}
    key = _build_key(resource_id, file_type, ext)
    path = os.path.join(PRIVATE_DOWNLOADS_DIR, key)
    try:
        stat = os.stat(path)
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        size = None
        mtime = None
    return {
        'exists': True,
        'ext': ext,
        'filename': os.path.basename(path),
        'size': size,
        'mtime': mtime,
        'path': path
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

def _normalize_research_highlights(items):
    normalized = []
    source_items = items if isinstance(items, list) else []
    for index, default_item in enumerate(DEFAULT_RESEARCH_HIGHLIGHTS):
        current = source_items[index] if index < len(source_items) and isinstance(source_items[index], dict) else {}
        title = (current.get('title') or '').strip() or default_item['title']
        summary = (current.get('summary') or '').strip() or default_item['summary']
        normalized.append({
            'title': title,
            'summary': summary
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
        'category': (item.get('category') or '').strip(),
        'email': (item.get('email') or '').strip(),
        'photo_filename': (item.get('photo_filename') or '').strip(),
        'bio': (item.get('bio') or '').strip(),
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
        'title': 'SJTU IOC Lab website is now online',
        'date': today,
        'summary': f'Version {DEFAULT_SITE_VERSION} is now available with publications, people profiles, resource downloads, and admin analytics.',
        'content': (
            f'Our lab website officially went online on {today}.\n\n'
            f'The current release is Version {DEFAULT_SITE_VERSION}. It includes publication pages, people pages, '
            'resource download access, simple analytics, and a lightweight content-management workflow.\n\n'
            'Welcome to browse the site, read our publications, download available resources, and use the shared materials for academic purposes.'
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
        'date': (item.get('date') or '').strip() or datetime.now().strftime("%Y-%m-%d"),
        'summary': (item.get('summary') or '').strip(),
        'content': (item.get('content') or '').strip(),
        'image_filename': (item.get('image_filename') or '').strip(),
        'last_edited': (item.get('last_edited') or '').strip()
    }

def _news_sort_key(item):
    return ((item.get('date') or ''), (item.get('last_edited') or ''), item.get('id', ''))

def load_news_data():
    path = os.path.join(PERSISTENT_ROOT, 'news.json')
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
    csv_path = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    if not os.path.exists(csv_path):
        return {
            'download_counts': download_counts,
            'unique_downloaders': unique_downloaders,
            'last_download_times': last_download_times,
            'total_downloads': 0
        }

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                item_id = row.get('resource_id') or ''
                if not item_id:
                    continue
                download_counts[item_id] = download_counts.get(item_id, 0) + 1
                key_triplet = (row.get('name', ''), row.get('affiliation', ''), row.get('email', ''))
                unique_downloaders.setdefault(item_id, set()).add(key_triplet)
                try:
                    stamp = datetime.strptime(row.get('time', ''), "%Y-%m-%d %H:%M:%S")
                    previous = last_download_times.get(item_id)
                    if previous is None or stamp > previous:
                        last_download_times[item_id] = stamp
                except Exception:
                    pass
    except Exception as e:
        print(f"CSV read error: {e}")

    return {
        'download_counts': download_counts,
        'unique_downloaders': unique_downloaders,
        'last_download_times': last_download_times,
        'total_downloads': sum(download_counts.values())
    }

def _read_page_view_log_summary():
    total_views = 0
    article_view_counts = {}
    if not os.path.exists(PAGE_VIEWS_CSV_PATH):
        return {
            'total_views': total_views,
            'article_view_counts': article_view_counts
        }

    try:
        with open(PAGE_VIEWS_CSV_PATH, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_views += 1
                if (row.get('page_type') or '') == 'article_detail':
                    article_id = row.get('item_id') or ''
                    if article_id:
                        article_view_counts[article_id] = article_view_counts.get(article_id, 0) + 1
    except Exception as e:
        print(f"Page view CSV read error: {e}")

    return {
        'total_views': total_views,
        'article_view_counts': article_view_counts
    }

def load_site_config():
    os.makedirs(PERSISTENT_ROOT, exist_ok=True)
    if not os.path.exists(SITE_CONFIG_PATH):
        try:
            with open(SITE_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_SITE_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to init site config: {e}")
    try:
        with open(SITE_CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        changed = False
        for key in ('home_note', 'home_welcome', 'hero_summary', 'lab_name', 'lab_name_short', 'lab_name_full', 'logo_filename', 'footer_copyright', 'site_version'):
            if key not in cfg or not isinstance(cfg.get(key), str):
                cfg[key] = DEFAULT_SITE_CONFIG[key]
                changed = True
        normalized_highlights = _normalize_research_highlights(cfg.get('research_highlights'))
        if cfg.get('research_highlights') != normalized_highlights:
            cfg['research_highlights'] = normalized_highlights
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
    """Safely load JSON list from render_data/ directory"""
    os.makedirs(PERSISTENT_ROOT, exist_ok=True)
    path = os.path.join(PERSISTENT_ROOT, filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load {filename}: {e}")
        return []

def save_json_data(filename, data):
    """Save data to JSON file (used by admin) into render_data/"""
    os.makedirs(PERSISTENT_ROOT, exist_ok=True)
    path = os.path.join(PERSISTENT_ROOT, filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _latest_content_modified_time():
    latest_ts = None
    for root, dirs, files in os.walk(PERSISTENT_ROOT):
        dirs[:] = [d for d in dirs if d != 'data_logs' and not d.startswith('.')]
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
    if _normalize_home_carousel_flags(articles):
        save_json_data('articles.json', articles)
    return articles

@app.context_processor
def inject_site_globals():
    return {
        'site_cfg_global': load_site_config()
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

    os.makedirs(DATA_LOGS_DIR, exist_ok=True)
    file_exists = os.path.isfile(PAGE_VIEWS_CSV_PATH)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    visitor_id = _ensure_visitor_id()
    user_info = session.get('user_info') or {}

    try:
        with open(PAGE_VIEWS_CSV_PATH, 'a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    'time', 'visitor_id', 'path', 'page_type', 'item_id',
                    'title', 'name', 'affiliation', 'email'
                ])
            writer.writerow([
                timestamp,
                visitor_id,
                request.path,
                page_type,
                item_id,
                title,
                user_info.get('name', ''),
                user_info.get('affiliation', ''),
                user_info.get('email', '')
            ])
        session['last_view_key'] = page_key
        session['last_view_time'] = timestamp
    except Exception as e:
        print(f"View log write failed: {e}")

def get_file_status(item_id):
    status = {'paper': False, 'resource': False}
    for t in ['paper', 'resource']:
        ext = _find_existing_ext(item_id, t)
        if ext:
            status[t] = True
    return status

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
            resource_url_in = form_data.get('resource_url')
            if paper_url_in is not None:
                item['paper_url'] = paper_url_in.strip()
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

PEOPLE_IMAGES_DIR = os.path.join(PERSISTENT_ROOT, 'images', 'people')
os.makedirs(PEOPLE_IMAGES_DIR, exist_ok=True)
ARTICLE_IMAGES_DIR = os.path.join(PERSISTENT_ROOT, 'images', 'articles')
os.makedirs(ARTICLE_IMAGES_DIR, exist_ok=True)
NEWS_IMAGES_DIR = os.path.join(PERSISTENT_ROOT, 'images', 'news')
os.makedirs(NEWS_IMAGES_DIR, exist_ok=True)
SITE_IMAGES_DIR = os.path.join(PERSISTENT_ROOT, 'images')
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
        'category': form_data.get('category', '').strip(),
        'email': form_data.get('email', '').strip(),
        'photo_filename': photo_filename,
        'bio': form_data.get('bio', '').strip(),
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
        'date': (form_data.get('date') or '').strip() or datetime.now().strftime("%Y-%m-%d"),
        'summary': (form_data.get('summary') or '').strip(),
        'content': (form_data.get('content') or '').strip(),
        'image_filename': image_filename,
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
        item['date'] = (form_data.get('date') or item.get('date') or '').strip() or datetime.now().strftime("%Y-%m-%d")
        item['summary'] = (form_data.get('summary') or item.get('summary') or '').strip()
        item['content'] = (form_data.get('content') or item.get('content') or '').strip()
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
        latest_news=news_items[:1]
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
    
    status = get_file_status(id)
    paper_info = _file_info(id, 'paper')
    resource_info = _file_info(id, 'resource')
    download_summary = _read_download_log_summary()
    log_page_view('article_detail', item_id=id, title=article.get('title', id))
    return render_template(
        'article_detail.html',
        item=article,
        file_status=status,
        paper_info=paper_info,
        resource_info=resource_info,
        download_count=download_summary['download_counts'].get(id, 0)
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
    log_page_view('register', title='Register')
    return render_template('register.html')

@app.route('/submit_register', methods=['POST'])
def submit_register():
    """Handle login/register form submission"""
    name = request.form.get('name', '').strip()
    affiliation = request.form.get('affiliation', '').strip()
    email = request.form.get('email', '').strip()
    consent = request.form.get('consent')

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
    return send_file(actual_path, as_attachment=True, download_name=filename)


# ==============================================================================
# 7. Route Definitions - Admin Dashboard
# ==============================================================================

@app.route('/admin', methods=['GET', 'POST'])
def admin_dashboard():
    """Admin Dashboard for managing content"""
    if not session.get('is_admin'):
        return redirect(url_for('register'))

    # === Handle form submissions (add/edit/delete) ===
    action = request.form.get('action') or request.args.get('action')
    item_type = request.form.get('item_type') or request.args.get('item_type')  # 'article' or 'person' or 'news' or 'site'

    if action == 'add':
        if item_type == 'person':
            _add_person(request.form, request.files.get('photo'))
        elif item_type == 'news':
            _add_news(request.form, request.files.get('image'))
        else:
            _add_item(item_type, request.form)
        return redirect(url_for('admin_dashboard'))
    elif action == 'edit':
        item_id = request.form.get('id')
        if item_type == 'person':
            _update_person(item_id, request.form, request.files.get('photo'))
        elif item_type == 'news':
            _update_news(item_id, request.form, request.files.get('image'))
        else:
            _update_item(item_type, item_id, request.form)
        return redirect(url_for('admin_dashboard'))
    elif action in ('edit_site_welcome', 'edit_site_content', 'edit_site_branding', 'edit_friend_links', 'edit_research_highlights', 'edit_person_tags'):
        cfg = load_site_config()
        if action in ('edit_site_welcome', 'edit_site_content', 'edit_site_branding'):
            cfg['home_welcome'] = (request.form.get('home_welcome') or '').strip()
            cfg['home_note'] = (request.form.get('home_note') or '').strip() or DEFAULT_SITE_CONFIG['home_note']
            cfg['hero_summary'] = (request.form.get('hero_summary') or '').strip() or DEFAULT_SITE_CONFIG['hero_summary']
            cfg['lab_name_short'] = (request.form.get('lab_name_short') or '').strip() or DEFAULT_SITE_CONFIG['lab_name_short']
            cfg['lab_name_full'] = (request.form.get('lab_name_full') or '').strip() or DEFAULT_SITE_CONFIG['lab_name_full']
            cfg['site_version'] = (request.form.get('site_version') or '').strip() or DEFAULT_SITE_CONFIG['site_version']
            cfg['footer_copyright'] = (request.form.get('footer_copyright') or '').strip() or DEFAULT_SITE_CONFIG['footer_copyright']
            cfg['lab_name'] = cfg['lab_name_full']
        if action in ('edit_site_content', 'edit_friend_links'):
            friend_links = []
            for index, default_item in enumerate(DEFAULT_FRIEND_LINKS, start=1):
                current_links = cfg.get('friend_links') or []
                current = current_links[index - 1] if index - 1 < len(current_links) and isinstance(current_links[index - 1], dict) else {}
                friend_links.append({
                    'title': (request.form.get(f'friend_title_{index}') or '').strip(),
                    'caption': (request.form.get(f'friend_caption_{index}') or '').strip(),
                    'url': (request.form.get(f'friend_url_{index}') or '').strip(),
                    'image_filename': (current.get('image_filename') or '').strip()
                })
            cfg['friend_links'] = _normalize_friend_links(friend_links)
        if action in ('edit_site_content', 'edit_research_highlights'):
            highlights = []
            for index, default_item in enumerate(DEFAULT_RESEARCH_HIGHLIGHTS, start=1):
                highlights.append({
                    'title': (request.form.get(f'highlight_title_{index}') or '').strip() or default_item['title'],
                    'summary': (request.form.get(f'highlight_summary_{index}') or '').strip() or default_item['summary']
                })
            cfg['research_highlights'] = _normalize_research_highlights(highlights)
        if action in ('edit_site_content', 'edit_person_tags'):
            raw_text = request.form.get('person_tags_text', '')
            cfg['person_tags'] = _normalize_person_tags(raw_text.splitlines())
        save_site_config(cfg)
        return redirect(url_for('admin_dashboard'))
    elif action == 'delete':
        item_id = request.args.get('id')
        if item_type == 'person':
            _delete_person(item_id)
        elif item_type == 'news':
            _delete_news(item_id)
        else:
            _delete_item(item_type, item_id)
        return redirect(url_for('admin_dashboard'))

    # === Load data for display ===
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
    csv_path = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    item_id = row.get('resource_id')
                    if item_id:
                        download_counts[item_id] = download_counts.get(item_id, 0) + 1
                        key_triplet = (row.get('name',''), row.get('affiliation',''), row.get('email',''))
                        s = unique_downloaders.get(item_id)
                        if s is None:
                            s = set()
                            unique_downloaders[item_id] = s
                        s.add(key_triplet)
                        try:
                            t = datetime.strptime(row.get('time',''), "%Y-%m-%d %H:%M:%S")
                            date_key = t.strftime("%Y-%m-%d")
                            if date_key in trend_keys:
                                trend_keys[date_key]['downloads'] += 1
                                downloads_last_7_days += 1
                            if t.date() == today:
                                downloads_today += 1
                            prev = last_download_times.get(item_id)
                            if (prev is None) or (t > prev):
                                last_download_times[item_id] = t
                        except Exception:
                            pass
        except Exception as e:
            print(f"CSV read error: {e}")

    # Load articles
    articles = load_articles_data()
    people = load_people_data()
    news_items = load_news_data()
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
    if os.path.exists(PAGE_VIEWS_CSV_PATH):
        try:
            with open(PAGE_VIEWS_CSV_PATH, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
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

                    try:
                        viewed_at = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
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
                    except Exception:
                        viewed_at = None

                    if page_type == 'article_detail':
                        article_detail_views += 1
                        article_view_counts[item_id] = article_view_counts.get(item_id, 0) + 1
                        prev = article_last_view_times.get(item_id)
                        if viewed_at and (prev is None or viewed_at > prev):
                            article_last_view_times[item_id] = viewed_at
        except Exception as e:
            print(f"Page view CSV read error: {e}")

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

    # People photo status/info
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
    
    # Get file statuses for all articles
    file_statuses = {item['id']: get_file_status(item['id']) for item in articles}
    file_infos = {item['id']: {'paper': _file_info(item['id'], 'paper'), 'resource': _file_info(item['id'], 'resource')} for item in articles}
    unique_counts = {aid: len(unique_downloaders.get(aid, set())) for aid in download_counts.keys()}

    site_cfg = load_site_config()
    return render_template(
        'admin.html',
        articles=articles,
        download_counts=download_counts,
        total_downloads=total_downloads,
        file_statuses=file_statuses,
        file_infos=file_infos,
        unique_counts=unique_counts,
        last_download_times=last_download_times,
        page_view_stats=page_view_stats,
        trend_data=trend_data,
        top_pages=top_pages,
        top_articles_by_views=top_articles_by_views,
        article_view_counts=article_view_counts,
        article_metrics=article_metrics,
        people=people,
        news_items=news_items,
        people_photo_status=people_photo_status,
        people_photo_info=people_photo_info,
        start_time=START_TIME,
        admin_login_time=session.get('admin_login_at') or START_TIME,
        content_last_modified=_latest_content_modified_time(),
        lab_name=site_cfg.get('lab_name_full', LAB_NAME),
        site_cfg=site_cfg
    )

@app.route('/admin/view-as-user')
def admin_view_as_user():
    if not session.get('is_admin'):
        return redirect(url_for('register'))
    session['admin_shadow'] = True
    session['is_admin'] = False
    session['user_info'] = {
        'name': ADMIN_CREDENTIALS.get('name','Admin'),
        'affiliation': ADMIN_CREDENTIALS.get('affiliation','Intelligent Optoelectronic Computing Lab'),
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

    return redirect(url_for('admin_dashboard') + '#article-' + resource_id)

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
    return redirect(url_for('admin_dashboard') + '#article-' + article_id)

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
    return redirect(url_for('admin_dashboard') + '#site-section')

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
    """Package the entire render_data folder and download as ZIP"""
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
    return send_file(buf, as_attachment=True, download_name='render_data_bundle.zip', mimetype='application/zip')

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
