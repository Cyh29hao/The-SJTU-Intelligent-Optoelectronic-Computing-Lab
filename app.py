# app.py - English Version (Fully Internationalized)
# 1. Preparation
from flask import Flask, render_template, request, redirect, url_for, send_file, session
import csv
import os
from datetime import datetime
import sys
import json
import logging
from dotenv import load_dotenv

# === Safely redefine print with flush=True ===
original_print = print
def debug_print(*args, **kwargs):
    """Print with auto-flush to avoid log buffering"""
    kwargs.setdefault('flush', True)
    original_print(*args, **kwargs)

print = debug_print
print("🚀 Application started, loading routes...")


IS_LOCAL = 1


app = Flask(__name__)
app.config['ENV'] = 'development'
app.config['DEBUG'] = True

# ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'default123')

os.makedirs('private_downloads', exist_ok=True)

# Load .env file (for local dev only; Render uses env vars directly)
load_dotenv()



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



# Admin credentials (from environment variables)
ADMIN_CREDENTIALS = {
    'name': os.environ.get('ADMIN_NAME', 'Admin'),
    'affiliation': os.environ.get('ADMIN_AFFILIATION', 'Your Lab'),
    'email': os.environ.get('ADMIN_EMAIL', 'admin@example.com')
}

# Generate and reuse secret key (for production)
SECRET_KEY_FILE = 'secret_key.bin'
if os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, 'rb') as f:
        secret_key = f.read()
else:
    secret_key = os.urandom(24)
    with open(SECRET_KEY_FILE, 'wb') as f:
        f.write(secret_key)
app.secret_key = secret_key

# ONLY_LOCAL: regenerate secret key on every launch → invalidate old sessions
if IS_LOCAL:
    app.secret_key = os.urandom(24)

# Optional: enable Flask built-in logger (for debugging)
if not app.debug:
    app.logger.addHandler(logging.StreamHandler())
    app.logger.setLevel(logging.DEBUG)

# 2. Route Definitions

# HOME
@app.route('/')
def index():
    return render_template('index.html')

# REGISTER / LOGIN
@app.route('/register')
def register():
    session.pop('user_info', None)
    session.pop('registered_at', None)
    return render_template('register.html')  # renamed to login.html

# LOGOUT (user)
@app.route('/logout')
def logout():
    session.pop('user_info', None)
    session.pop('registered_at', None)
    session.pop('resource_id', None)  # if exists
    return redirect(url_for('index'))

# ADMIN LOGOUT
@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))

# === Submit registration/login info ===
@app.route('/submit_register', methods=['POST'])
def submit_register():
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

    # Save to session
    session['user_info'] = {
        'name': name,
        'affiliation': affiliation,
        'email': email
    }
    session['registered_at'] = datetime.now().isoformat()
    print(f"✅ User registered: {name} | {affiliation} | {email}")
    return redirect(url_for('index'))

# === Secure download endpoint (unified entry) ===
@app.route('/download_file/<resource_id>')
def download_file(resource_id):
    user_info = session.get('user_info')
    if not user_info:
        print(f"⚠️ Unregistered user attempted to download {resource_id}, redirecting to login")
        return redirect(url_for('register'))
    else:
        print(user_info)

    # Locate file
    DOWNLOAD_DIR = "private_downloads"
    actual_path = None
    for ext in ['.zip', '.pdf', '.npz', '.tar.gz', '']:
        candidate = os.path.join(DOWNLOAD_DIR, f"{resource_id}{ext}")
        if os.path.isfile(candidate):
            actual_path = candidate
            break

    if not actual_path:
        return "❌ Requested resource not found.", 404

    # === 🔒 SAFE CSV LOGGING WITH DIRECTORY CREATION ===
    LOG_DIR = 'data_logs'
    csv_file = os.path.join(LOG_DIR, 'downloads.csv')

    # ✅ 确保日志目录存在（关键！）
    os.makedirs(LOG_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(csv_file)

    try:
        with open(csv_file, 'a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                # Use English headers
                writer.writerow(['time', 'name', 'affiliation', 'email', 'resource_id'])
            writer.writerow([
                timestamp,
                user_info['name'],
                user_info['affiliation'],
                user_info['email'],
                resource_id
            ])
        print(f"📥 Download logged: {user_info['name']} -> {resource_id}")
    except Exception as e:
        print(f"🔴 CSV write failed: {e}")

    # Send file
    filename = os.path.basename(actual_path)
    return send_file(actual_path, as_attachment=True, download_name=filename)



# === Articles Page ===
@app.route('/articles')
def articles():
    ARTICLES = load_json_data('articles.json')
    sorted_articles = sorted(ARTICLES, key=lambda x: x['year'], reverse=True)
    return render_template('articles.html', articles=sorted_articles)

# === Article Detail ===
@app.route('/article/<id>')
def article_detail(id):
    ARTICLES = load_json_data('articles.json')
    article = next((a for a in ARTICLES if a['id'] == id), None)
    if not article:
        return "Article not found", 404
    return render_template('article_detail.html', item=article)

# === Resources Page ===
@app.route('/resources')
def resources():
    RESOURCES = load_json_data('resources.json')
    sorted_resources = sorted(RESOURCES, key=lambda x: x['year'], reverse=True)
    return render_template('resources.html', resources=sorted_resources)

# === Resource Detail ===
@app.route('/resource/<id>')
def resource_detail(id):
    RESOURCES = load_json_data('resources.json')
    resource = next((r for r in RESOURCES if r['id'] == id), None)
    if not resource:
        return "Resource not found", 404
    return render_template('resource_detail.html', item=resource)

# === Team Page ===
@app.route('/team')
def team():
    print("🔍 Visiting /team")
    return render_template('team.html')




# ==== ADMIN =====
@app.route('/admin', methods=['GET', 'POST'])
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('register'))

    # === Handle form submissions (add/edit/delete) ===
    action = request.form.get('action') or request.args.get('action')
    item_type = request.form.get('item_type') or request.args.get('item_type')  # 'article' or 'resource'

    if action == 'add':
        _add_item(item_type, request.form)
        return redirect(url_for('admin_dashboard'))
    elif action == 'edit':
        item_id = request.form.get('id')
        _update_item(item_type, item_id, request.form)
        return redirect(url_for('admin_dashboard'))
    elif action == 'delete':
        item_id = request.args.get('id')
        _delete_item(item_type, item_id)
        return redirect(url_for('admin_dashboard'))

    # === Load data for display ===
    
        # === Load download logs and count per item ===
    download_counts = {}
    csv_path = 'data_logs/downloads.csv'
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    item_id = row.get('resource_id')  # 假设字段名是 'resource_id'
                    if item_id:
                        download_counts[item_id] = download_counts.get(item_id, 0) + 1
        except Exception as e:
            print(f"CSV read error: {e}")

    # Load articles and resources
    articles = load_json_data('articles.json')
    resources = load_json_data('resources.json')

    articles = load_json_data('articles.json')
    resources = load_json_data('resources.json')

    return render_template(
        'admin.html',
        downloads=[],  # 不再需要原始日志列表（因为我们只提供下载按钮）
        articles=articles,
        resources=resources,
        download_counts=download_counts  # 👈 新增这一行
    )

# ADD DEL UPD for ADMIN
def _add_item(item_type, form_data):
    filename = 'articles.json' if item_type == 'article' else 'resources.json'
    data = load_json_data(filename)
    
    # Generate new ID (simple increment)
    ids = [int(item['id'].split('_')[-1]) for item in data if '_' in item['id']]
    new_id_num = max(ids) + 1 if ids else 1
    prefix = 'art' if item_type == 'article' else 'res'
    new_id = f"{prefix}_{new_id_num:03d}"

    # Parse authors (comma-separated string → list)
    authors_str = form_data.get('authors', '').strip()
    authors = [a.strip() for a in authors_str.split(',')] if authors_str else []

    new_item = {
        'id': new_id,
        'title': form_data.get('title', '').strip(),
        'authors': authors,
        'year': int(form_data.get('year', 2025)),
    }

    if item_type == 'article':
        new_item['venue'] = form_data.get('venue', '').strip()
        new_item['abstract'] = form_data.get('abstract', '').strip()
    else:  # resource
        new_item['type'] = form_data.get('type', 'Code').strip()
        new_item['readme'] = form_data.get('readme', '').strip()

    data.append(new_item)
    save_json_data(filename, data)
    print(f"✅ Added {item_type}: {new_id}")


def _update_item(item_type, item_id, form_data):
    filename = 'articles.json' if item_type == 'article' else 'resources.json'
    data = load_json_data(filename)
    for item in data:
        if item['id'] == item_id:
            item['title'] = form_data.get('title', item['title']).strip()
            authors_str = form_data.get('authors', ', '.join(item['authors'])).strip()
            item['authors'] = [a.strip() for a in authors_str.split(',')] if authors_str else []
            item['year'] = int(form_data.get('year', item['year']))
            
            if item_type == 'article':
                item['venue'] = form_data.get('venue', item.get('venue', '')).strip()
                item['abstract'] = form_data.get('abstract', item.get('abstract', '')).strip()
            else:
                item['type'] = form_data.get('type', item.get('type', 'Code')).strip()
                item['readme'] = form_data.get('readme', item.get('readme', '')).strip()
            break
    save_json_data(filename, data)
    print(f"✏️ Updated {item_type}: {item_id}")


def _delete_item(item_type, item_id):
    filename = 'articles.json' if item_type == 'article' else 'resources.json'
    data = load_json_data(filename)
    data = [item for item in data if item['id'] != item_id]
    save_json_data(filename, data)
    print(f"🗑️ Deleted {item_type}: {item_id}")

# ADMIN UPLOAD 
@app.route('/admin/upload/<resource_id>', methods=['POST'])
def admin_upload_file(resource_id):
    if not session.get('is_admin'):
        return "Unauthorized", 403

    # Check if resource exists
    resources = load_json_data('resources.json')
    target = next((r for r in resources if r['id'] == resource_id), None)
    if not target:
        return "Resource not found", 404

    file = request.files.get('file')
    if not file or not file.filename:
        return "No file selected", 400

    # Allow only safe extensions
    ALLOWED_EXTENSIONS = {'.zip', '.pdf', '.npz', '.tar.gz', '.h5', '.mat'}
    filename = file.filename
    ext = ''
    for allowed in sorted(ALLOWED_EXTENSIONS, key=len, reverse=True):  # match .tar.gz before .gz
        if filename.lower().endswith(allowed):
            ext = allowed
            break
    if not ext:
        return f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}", 400

    # Save as {resource_id}{ext}
    os.makedirs('private_downloads', exist_ok=True)
    save_path = os.path.join('private_downloads', f"{resource_id}{ext}")
    file.save(save_path)
    print(f"✅ Admin uploaded file for {resource_id} -> {save_path}")

    return redirect(url_for('admin_dashboard') + '#resource-' + resource_id)

#ADMIN DOWNLOAD LOGS
@app.route('/admin/download-logs.csv')
def download_logs_csv():
    if not session.get('is_admin'):
        return "Unauthorized", 403
    LOG_DIR = 'data_logs'
    csv_path = os.path.join(LOG_DIR, 'downloads.csv')
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(csv_path):
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write('time,name,affiliation,email,resource_id\n')
    return send_file(csv_path, as_attachment=True, download_name='lightchip_download_logs.csv')



# === Test Page ===
@app.route('/test')
def test():
    print("\n🎉 /test page accessed! Flask is running.\n")
    return "✅ Test OK! Check console for real-time output."

# 3. Launch
if __name__ == '__main__':
    print(" Current time: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("🔧 Starting Flask development server...")
    print("🛑 Press Ctrl+C to stop")
    if IS_LOCAL:
        app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=True)
    else:
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port, debug=False)