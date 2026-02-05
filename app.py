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
IS_LOCAL = 1  # Set to 0 for production/deployment
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


# ==============================================================================
# 3. Helper Functions
# ==============================================================================

def _build_key(resource_id, file_type, ext):
    return f"{resource_id}_{file_type}{ext}"

def _find_existing_ext(resource_id, file_type):
    for ext in ['.pdf', '.zip', '.npz', '.tar.gz', '.h5', '.mat', '.txt']:
        key = _build_key(resource_id, file_type, ext)
        local_path = os.path.join(PRIVATE_DOWNLOADS_DIR, key)
        if os.path.isfile(local_path):
            return ext
    return None

def load_json_data(filename):
    """Safely load JSON list from data/ directory"""
    path = os.path.join('data', filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load {filename}: {e}")
        return []

def save_json_data(filename, data):
    """Save data to JSON file (used by admin)"""
    path = os.path.join('data', filename)
    os.makedirs('data', exist_ok=True)
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
            # Optional source links
            paper_url_in = form_data.get('paper_url')
            resource_url_in = form_data.get('resource_url')
            if paper_url_in is not None:
                item['paper_url'] = paper_url_in.strip()
            if resource_url_in is not None:
                item['resource_url'] = resource_url_in.strip()
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

PEOPLE_IMAGES_DIR = os.path.join('static', 'images', 'people')
os.makedirs(PEOPLE_IMAGES_DIR, exist_ok=True)

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
    item = {
        'id': new_id,
        'name': form_data.get('name', '').strip(),
        'category': form_data.get('category', '').strip(),
        'email': form_data.get('email', '').strip(),
        'photo_filename': photo_filename,
        'bio': form_data.get('bio', '').strip(),
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
    return render_template('index.html')

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
    return render_template('article_detail.html', item=article, file_status=status)

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

    if not name or not affiliation or not email:
        return "❌ Please fill in all required fields.", 400

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

    # Locate file locally
    ext = _find_existing_ext(resource_id, file_type)
    if not ext:
        return "❌ Requested resource not found.", 404

    # === Safe CSV Logging ===
    csv_file = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    os.makedirs(DATA_LOGS_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    item_type = request.form.get('item_type') or request.args.get('item_type')  # 'article' only

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
    elif action == 'delete':
        item_id = request.args.get('id')
        if item_type == 'person':
            _delete_person(item_id)
        else:
            _delete_item(item_type, item_id)
        return redirect(url_for('admin_dashboard'))

    # === Load data for display ===
    
    # Load download counts (simplified logic, maybe aggregated by ID)
    download_counts = {}
    csv_path = os.path.join(DATA_LOGS_DIR, 'downloads.csv')
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    item_id = row.get('resource_id')
                    if item_id:
                        download_counts[item_id] = download_counts.get(item_id, 0) + 1
        except Exception as e:
            print(f"CSV read error: {e}")

    # Load articles
    articles = load_json_data('articles.json')
    people = load_json_data('people.json')
    
    # Get file statuses for all articles
    file_statuses = {item['id']: get_file_status(item['id']) for item in articles}

    return render_template(
        'admin.html',
        articles=articles,
        download_counts=download_counts,
        file_statuses=file_statuses,
        people=people
    )

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
    ALLOWED_EXTENSIONS = {'.zip', '.pdf', '.npz', '.tar.gz', '.h5', '.mat', '.txt'}
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
