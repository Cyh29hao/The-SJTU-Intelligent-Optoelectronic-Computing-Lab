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
from datetime import datetime
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
    original_print(*args, **kwargs)

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

def load_site_config():
    os.makedirs(PERSISTENT_ROOT, exist_ok=True)
    if not os.path.exists(SITE_CONFIG_PATH):
        default = {
            "home_welcome": "Our lab focuses on research in all-optical neural networks, diffractive deep learning, and intelligent photonic chips.\n\nThis website provides publicly available publications, code, and datasets from our group.",
            "lab_name": LAB_NAME
        }
        try:
            with open(SITE_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to init site config: {e}")
    try:
        with open(SITE_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load site config: {e}")
        return {"home_welcome": "", "lab_name": LAB_NAME}

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
    data = load_json_data(filename)
    
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
        'last_edited': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    data.append(new_item)
    save_json_data(filename, data)
    print(f"✅ Added article: {new_id}")

def _update_item(item_type, item_id, form_data):
    """Helper to update existing article"""
    filename = 'articles.json'
    data = load_json_data(filename)
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
            item['last_edited'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
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

def _add_person(form_data, photo_file=None):
    filename = 'people.json'
    data = load_json_data(filename)
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
        'last_edited': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    data.append(item)
    save_json_data(filename, data)
    print(f"✅ Added person: {new_id}")

def _update_person(person_id, form_data, photo_file=None):
    filename = 'people.json'
    data = load_json_data(filename)
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
            item['last_edited'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    save_json_data(filename, data)
    print(f"✏️ Updated person: {person_id}")

def _delete_person(person_id):
    filename = 'people.json'
    data = load_json_data(filename)
    data = [item for item in data if item['id'] != person_id]
    save_json_data(filename, data)
    print(f"🗑️ Deleted person: {person_id}")


# ==============================================================================
# 4. Route Definitions - Public Pages
# ==============================================================================

@app.route('/')
def index():
    site_cfg = load_site_config()
    return render_template('index.html', site_cfg=site_cfg)

@app.route('/team')
def team():
    people = load_json_data('people.json')
    return render_template('team.html', people=people)

@app.route('/articles')
def articles():
    ARTICLES = load_json_data('articles.json')
    sorted_articles = sorted(ARTICLES, key=lambda x: x['year'], reverse=True)
    return render_template('articles.html', articles=sorted_articles)

@app.route('/article/<id>')
def article_detail(id):
    ARTICLES = load_json_data('articles.json')
    article = next((a for a in ARTICLES if a['id'] == id), None)
    if not article:
        return "Article not found", 404
    
    status = get_file_status(id)
    paper_info = _file_info(id, 'paper')
    resource_info = _file_info(id, 'resource')
    return render_template('article_detail.html', item=article, file_status=status, paper_info=paper_info, resource_info=resource_info)

@app.route('/person/<id>')
def person_detail(id):
    people = load_json_data('people.json')
    person = next((p for p in people if p['id'] == id), None)
    if not person:
        return "Person not found", 404
    return render_template('person_detail.html', person=person)
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
    item_type = request.form.get('item_type') or request.args.get('item_type')  # 'article' or 'person' or 'site'

    if action == 'add':
        if item_type == 'person':
            _add_person(request.form, request.files.get('photo'))
        else:
            _add_item(item_type, request.form)
        return redirect(url_for('admin_dashboard'))
    elif action == 'edit':
        item_id = request.form.get('id')
        if item_type == 'person':
            _update_person(item_id, request.form, request.files.get('photo'))
        else:
            _update_item(item_type, item_id, request.form)
        return redirect(url_for('admin_dashboard'))
    elif action == 'edit_site_welcome':
        note = (request.form.get('home_welcome') or '').strip()
        cfg = load_site_config()
        cfg['home_welcome'] = note
        save_site_config(cfg)
        return redirect(url_for('admin_dashboard'))
    elif action == 'delete':
        item_id = request.args.get('id')
        if item_type == 'person':
            _delete_person(item_id)
        else:
            _delete_item(item_type, item_id)
        return redirect(url_for('admin_dashboard'))

    # === Load data for display ===
    
    # Load download counts and unique downloaders
    download_counts = {}
    unique_downloaders = {}
    last_download_times = {}
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
                            prev = last_download_times.get(item_id)
                            if (prev is None) or (t > prev):
                                last_download_times[item_id] = t
                        except Exception:
                            pass
        except Exception as e:
            print(f"CSV read error: {e}")

    # Load articles
    articles = load_json_data('articles.json')
    people = load_json_data('people.json')
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
        file_statuses=file_statuses,
        file_infos=file_infos,
        unique_counts=unique_counts,
        last_download_times=last_download_times,
        people=people,
        people_photo_status=people_photo_status,
        people_photo_info=people_photo_info,
        start_time=START_TIME,
        lab_name=LAB_NAME,
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
    articles = load_json_data('articles.json')
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
    articles = load_json_data('articles.json')
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
    return redirect(url_for('admin_dashboard'))

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

@app.route('/admin/download-logs.csv')
def download_logs_csv():
    """Export download logs as CSV"""
    if not session.get('is_admin'):
        return "Unauthorized", 403
    csv_path = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    os.makedirs(DATA_LOGS_DIR, exist_ok=True)
    if not os.path.exists(csv_path):
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write('time,name,affiliation,email,resource_id\n')
    return send_file(csv_path, as_attachment=True, download_name='lightchip_download_logs.csv')

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





# Cloudflare upload API removed — using only local admin upload


# ==============================================================================
# 8. Main Entry Point
# ==============================================================================

if __name__ == '__main__':
    print(" Current time: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("🔧 Starting Flask development server...")
    print("🛑 Press Ctrl+C to stop")
    
    if IS_LOCAL:
        app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=True)
    else:
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port, debug=False)
