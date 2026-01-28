# app.py - 修复版（兼容 Windows / Mac / Linux）

# 1.Preparation
from flask import Flask, render_template, request, redirect, url_for, send_file, session
import csv
import os
from datetime import datetime
import sys
import logging

from data.resources import ARTICLES, RESOURCES

# === 安全地重定义 print，强制 flush=True ===
original_print = print  # 保存原始 print 函数

def debug_print(*args, **kwargs):
    """带自动 flush 的 print，避免日志缓冲"""
    kwargs.setdefault('flush', True)
    original_print(*args, **kwargs)

# 替换全局 print
print = debug_print

print("🚀 程序已启动，正在加载路由...")

app = Flask(__name__)
app.config['ENV'] = 'development'
app.config['DEBUG'] = True

#app.secret_key = 'lightchip-lab-2026-download-system'  # 开发用固定值即可

#app.secret_key = os.urandom(24)

# 生成一次并复用（开发用）
SECRET_KEY_FILE = 'secret_key.bin'
if os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, 'rb') as f:
        secret_key = f.read()
else:
    secret_key = os.urandom(24)
    with open(SECRET_KEY_FILE, 'wb') as f:
        f.write(secret_key)

app.secret_key = secret_key


# 可选：启用 Flask 内置日志（辅助调试）
if not app.debug:
    app.logger.addHandler(logging.StreamHandler())
    app.logger.setLevel(logging.DEBUG)


# 2. 各个网页细则

# HOME
@app.route('/')
def index():
    print("🔍 访问首页 /")
    return render_template('index.html')

# REGISTER
@app.route('/register')
def register():
    return render_template('register.html')

# === 提交注册信息 ===
@app.route('/submit_register', methods=['POST'])
def submit_register():
    name = request.form.get('name', '').strip()
    affiliation = request.form.get('affiliation', '').strip()
    email = request.form.get('email', '').strip()
    
    if not name or not affiliation or not email:
        return "❌ 请填写所有必填字段", 400
    
    # 存入 session
    session['user_info'] = {
        'name': name,
        'affiliation': affiliation,
        'email': email
    }
    session['registered_at'] = datetime.now().isoformat()
    
    print(f"✅ 用户注册成功: {name} | {affiliation} | {email}")
    return redirect(url_for('index'))  # 返回首页

# === 安全下载路由（全站统一入口）===
@app.route('/download_file/<resource_id>')
def download_file(resource_id):
    # 检查是否已注册
    user_info = session.get('user_info')
    if not user_info:
        print(f"⚠️ 未注册用户尝试下载 {resource_id}，重定向到注册页")
        return redirect(url_for('register'))

    # 查找文件
    DOWNLOAD_DIR = "private_downloads"
    actual_path = None
    for ext in ['.zip', '.pdf', '.npz', '.tar.gz', '']:
        candidate = os.path.join(DOWNLOAD_DIR, f"{resource_id}{ext}")
        if os.path.isfile(candidate):
            actual_path = candidate
            break
    
    if not actual_path:
        return "❌ 请求的资源不存在", 404

    # 记录下载行为
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    csv_file = 'downloads.csv'
    file_exists = os.path.isfile(csv_file)
    try:
        with open(csv_file, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['时间', '姓名', '单位', '邮箱', '资源ID'])
            writer.writerow([
                timestamp,
                user_info['name'],
                user_info['affiliation'],
                user_info['email'],
                resource_id
            ])
        print(f"📥 下载记录: {user_info['name']} -> {resource_id}")
    except Exception as e:
        print(f"🔴 CSV 写入失败: {e}")

    # 发送文件
    filename = os.path.basename(actual_path)
    return send_file(actual_path, as_attachment=True, download_name=filename)


# @app.route('/submit_download', methods=['POST'])
# def submit_download():
#     print("\n" + "="*60)
#     print("🎯 收到 POST 请求！正在处理 /submit_download")
    
#     # 打印关键调试信息
#     print(f"📡 请求方法: {request.method}")
#     print(f"📦 表单数据: {dict(request.form)}")
#     print(f"🌐 客户端IP: {request.remote_addr}")

#     name = request.form.get('name', '').strip()
#     affiliation = request.form.get('affiliation', '').strip()
#     email = request.form.get('email', '').strip()
#     resource_id = request.form.get('resource_id', '').strip() 

#     if not name or not affiliation or not email:
#         print("❌ 提交失败：缺少必要字段")
#         return "提交失败：请填写所有字段", 400

#     # === 新增：构建文件路径 ===
#     DOWNLOAD_DIR = "private_downloads"
#     file_path = os.path.join(DOWNLOAD_DIR, f"{resource_id}.zip")  # 默认 .zip
    
#     # 尝试常见扩展名（.zip, .pdf, .npz, .tar.gz）
#     extensions = ['.zip', '.pdf', '.npz', '.tar.gz', '']  # 空字符串用于无扩展名文件
#     actual_file_path = None
#     for ext in extensions:
#         candidate = os.path.join(DOWNLOAD_DIR, f"{resource_id}{ext}")
#         if os.path.isfile(candidate):
#             actual_file_path = candidate
#             break

#     if not actual_file_path or not os.path.exists(actual_file_path):
#         print(f"🔴 文件未找到！resource_id={resource_id}")
#         return "请求的资源不存在，请联系管理员。", 404    

#     timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#     csv_file = 'downloads.csv'
#     file_exists = os.path.isfile(csv_file)

#     try:
#         with open(csv_file, 'a', encoding='utf-8', newline='') as f:
#             writer = csv.writer(f)
#             if not file_exists:
#                 writer.writerow(['时间', '姓名', '单位', '邮箱', '申请资源ID'])
#                 print("🆕 创建新文件 downloads.csv 并写入表头（含 resource_id）")
#             writer.writerow([timestamp, name, affiliation, email, resource_id])
#             print(f"✅ 成功记录：{name} | {affiliation} | {email} | {resource_id}")

#     except Exception as e:
#         print(f"🔴 写入 CSV 失败！错误: {str(e)}")
#         import traceback
#         traceback.print_exc()
#         return "服务器内部错误，请联系管理员。", 500

#     # === 直接发送文件 ===
#     print(f"⬇️ 正在发送文件: {actual_file_path}")
#     print("="*60 + "\n")
    
#     # 提取文件名用于下载提示
#     filename = os.path.basename(actual_file_path)
#     return send_file(
#         actual_file_path,
#         as_attachment=True,
#         download_name=filename  # Flask 2.0+ 使用 download_name
#     )

    # print("➡️ 将重定向到 /thank_you")
    # print("="*60 + "\n")
    # return redirect(url_for('thank_you'))

# @app.route('/thank_you')
# def thank_you():
#     print("🔍 访问感谢页 /thank_you")
#     return render_template('thank_you.html')

# === 研究方向页面 ===
# @app.route('/research')
# def research():
#     print("🔍 访问研究方向页 /research")
#     return render_template('research.html')

# === 成员介绍页面 ===
@app.route('/team')
def team():
    print("🔍 访问成员介绍页 /team")
    return render_template('team.html')








# === 论文列表页 ===
@app.route('/articles')
def articles():
    sorted_articles = sorted(ARTICLES, key=lambda x: x['year'], reverse=True)
    return render_template('articles.html', articles=sorted_articles)

# === 资源列表页（代码/数据集）===
@app.route('/resources')
def resources():
    sorted_resources = sorted(RESOURCES, key=lambda x: x['year'], reverse=True)
    return render_template('resources.html', resources=sorted_resources)

# === 论文详情页 ===
@app.route('/article/<id>')
def article_detail(id):
    article = next((a for a in ARTICLES if a['id'] == id), None)
    if not article:
        return "论文未找到", 404
    return render_template('article_detail.html', item=article)

# === 资源详情页 ===
@app.route('/resource/<id>')
def resource_detail(id):
    resource = next((r for r in RESOURCES if r['id'] == id), None)
    if not resource:
        return "资源未找到", 404
    return render_template('resource_detail.html', item=resource)

# === 修改下载页：支持 resource_id 参数 ===
# @app.route('/download')
# def download():
#     resource_id = request.args.get('resource_id', '')
#     return render_template('download.html', resource_id=resource_id)






# == 测试页面 ==
@app.route('/test')
def test():
    print("\n🎉 /test 页面被访问！Flask 正常运行\n")
    return "✅ Test OK! 查看控制台是否有实时输出。"



# 3. 启动

if __name__ == '__main__':
    print("🔧 启动 Flask 开发服务器...")
    print("🌐 访问 http://localhost:5000")
    print("🛑 按 Ctrl+C 停止")
    print(" 当前时间："+datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("💡 提示：修改代码后会自动重载（debug=True）\n")
    
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=True)