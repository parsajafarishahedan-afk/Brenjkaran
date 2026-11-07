import http.server
import socketserver
import sqlite3
import hashlib
import urllib.parse
import threading
import os
import json

PORT = int(os.environ.get("PORT", 8080))
DB_FILE = 'berenj_web.db'

class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_conn()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, fullname TEXT, phone TEXT UNIQUE, password TEXT, role TEXT, approved INTEGER DEFAULT 1, card_number TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id INTEGER, category TEXT, title TEXT, price TEXT, description TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, farmer_id INTEGER, service_id INTEGER, status TEXT DEFAULT 'در انتظار پرداخت', location TEXT DEFAULT '', created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, content TEXT, link TEXT, is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        
        # اضافه کردن ستون‌های جدید در صورت نبودن (برای دیتابیس‌های قدیمی)
        cursor = conn.execute("PRAGMA table_info(orders)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'location' not in columns:
            conn.execute("ALTER TABLE orders ADD COLUMN location TEXT DEFAULT ''")
            conn.commit()
        if 'created_at' not in columns:
            conn.execute("ALTER TABLE orders ADD COLUMN created_at TEXT DEFAULT ''")
            conn.commit()
            
        cursor = conn.execute("PRAGMA table_info(services)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'category' not in columns:
            conn.execute("ALTER TABLE services ADD COLUMN category TEXT DEFAULT 'سایر'")
            conn.commit()

        try:
            hashed_pw = hashlib.sha256('admin'.encode()).hexdigest()
            conn.execute('INSERT INTO users (fullname, phone, password, role, approved, card_number) VALUES (?, ?, ?, ?, 1, ?)', 
                         ('مدیر کل', '09120000000', hashed_pw, 'مدیر', '6037-XXXX-XXXX-XXXX'))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        conn.close()

    def authenticate(self, phone, password):
        conn = self.get_conn()
        hashed_pw = hashlib.sha256(password.encode()).hexdigest()
        user = conn.execute('SELECT * FROM users WHERE phone=? AND password=?', (phone, hashed_pw)).fetchone()
        conn.close()
        return dict(user) if user else None

    def register_user(self, fullname, phone, password, role, card_number):
        conn = self.get_conn()
        hashed_pw = hashlib.sha256(password.encode()).hexdigest()
        try:
            conn.execute('INSERT INTO users (fullname, phone, password, role, approved, card_number) VALUES (?, ?, ?, ?, 1, ?)', 
                         (fullname, phone, hashed_pw, role, card_number))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def get_user(self, user_id):
        conn = self.get_conn()
        user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        conn.close()
        return dict(user) if user else None

    def get_user_services(self, user_id):
        conn = self.get_conn()
        services = conn.execute('SELECT * FROM services WHERE provider_id=?', (user_id,)).fetchall()
        conn.close()
        return [dict(s) for s in services]

    def get_all_services(self):
        conn = self.get_conn()
        services = conn.execute('SELECT s.*, u.fullname as provider_name FROM services s JOIN users u ON s.provider_id = u.id').fetchall()
        conn.close()
        return [dict(s) for s in services]

    def get_service(self, service_id):
        conn = self.get_conn()
        service = conn.execute('SELECT s.*, u.card_number, u.fullname as provider_name FROM services s JOIN users u ON s.provider_id = u.id WHERE s.id=?', (service_id,)).fetchone()
        conn.close()
        return dict(service) if service else None

    def add_service(self, provider_id, category, title, price, desc):
        conn = self.get_conn()
        conn.execute('INSERT INTO services (provider_id, category, title, price, description) VALUES (?, ?, ?, ?, ?)', (provider_id, category, title, price, desc))
        conn.commit()
        conn.close()

    def create_order(self, farmer_id, service_id, location):
        conn = self.get_conn()
        try:
            # ثبت تاریخ در زمان درج
            conn.execute('''
                INSERT INTO orders (farmer_id, service_id, status, location, created_at) 
                VALUES (?, ?, "پرداخت شده", ?, datetime('now', 'localtime'))
            ''', (farmer_id, service_id, location))
            
            service_data = conn.execute('SELECT provider_id, title FROM services WHERE id=?', (service_id,)).fetchone()
            if service_data:
                provider_id = service_data['provider_id']
                service_title = service_data['title']
                farmer_data = conn.execute('SELECT fullname FROM users WHERE id=?', (farmer_id,)).fetchone()
                farmer_name = farmer_data['fullname'] if farmer_data else "کشاورز ناشناس"
                msg_content = f"کشاورز ({farmer_name}) خدمت «{service_title}» را رزرو کرد."
                map_link = f"https://www.google.com/maps?q={location}" if location else ""
                conn.execute('INSERT INTO notifications (user_id, content, link) VALUES (?, ?, ?)', (provider_id, msg_content, map_link))
                farmer_msg = f"شما با موفقیت خدمت «{service_title}» را رزرو کردید."
                conn.execute('INSERT INTO notifications (user_id, content) VALUES (?, ?)', (farmer_id, farmer_msg))
            conn.commit()
        except Exception as e:
            print(f"ERROR in create_order: {e}")
        finally:
            conn.close()

    def get_user_orders(self, user_id):
        conn = self.get_conn()
        orders = conn.execute('SELECT o.*, s.title, s.price, u.fullname as provider_name FROM orders o JOIN services s ON o.service_id = s.id JOIN users u ON s.provider_id = u.id WHERE o.farmer_id=? ORDER BY o.id DESC', (user_id,)).fetchall()
        conn.close()
        return [dict(o) for o in orders]
        
    def get_provider_orders(self, user_id):
        conn = self.get_conn()
        orders = conn.execute('SELECT o.*, s.title, u.fullname as farmer_name FROM orders o JOIN services s ON o.service_id = s.id JOIN users u ON o.farmer_id = u.id WHERE s.provider_id=? ORDER BY o.id DESC', (user_id,)).fetchall()
        conn.close()
        return [dict(o) for o in orders]

    def get_user_notifications(self, user_id):
        conn = self.get_conn()
        notifications = conn.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC', (user_id,)).fetchall()
        conn.execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (user_id,))
        conn.commit()
        conn.close()
        return [dict(n) for n in notifications]

    def get_unread_notification_count(self, user_id):
        conn = self.get_conn()
        count = conn.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0', (user_id,)).fetchone()[0]
        conn.close()
        return count
        
    def get_all_users(self):
        conn = self.get_conn()
        users = conn.execute('SELECT * FROM users WHERE role != "مدیر" ORDER BY id DESC').fetchall()
        conn.close()
        return [dict(u) for u in users]
        
    def approve_user(self, user_id):
        conn = self.get_conn()
        conn.execute('UPDATE users SET approved=1 WHERE id=?', (user_id,))
        conn.commit()
        conn.close()

class AuthManager:
    @staticmethod
    def get_user_id(headers):
        cookie = headers.get('Cookie')
        if cookie:
            for item in cookie.split(';'):
                if 'user_id' in item:
                    return item.split('=')[1].strip()
        return None

class WebApp(http.server.SimpleHTTPRequestHandler):
    db = Database(DB_FILE)

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)

            if path == '/manifest.json':
                self.send_response(200)
                self.send_header('Content-type', 'application/json; charset=utf-8')
                self.end_headers()
                manifest_data = {"name": "سامانه برنج‌کاران", "short_name": "برنج‌کاران", "start_url": "/", "display": "standalone", "background_color": "#ffffff", "theme_color": "#2e7d32", "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/2921/2921822.png", "sizes": "512x512", "type": "image/png"}]}
                self.wfile.write(json.dumps(manifest_data).encode('utf-8'))
                return
                
            elif path == '/service-worker.js':
                self.send_response(200)
                self.send_header('Content-type', 'application/javascript; charset=utf-8')
                self.end_headers()
                sw_code = "self.addEventListener('install', function(e) {});"
                self.wfile.write(sw_code.encode('utf-8'))
                return

            user_id = AuthManager.get_user_id(self.headers)
            user = self.db.get_user(user_id) if user_id else None

            if path == '/':
                self.send_html(self.page_login())
            elif path == '/login':
                self.send_html(self.page_login())
            elif path == '/register':
                self.send_html(self.page_register())
            elif path == '/logout':
                self.send_response(302)
                self.send_header('Set-Cookie', 'user_id=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT')
                self.send_header('Location', '/')
                self.end_headers()
            elif path == '/dashboard':
                if not user:
                    self.redirect('/login')
                    return
                self.send_html(self.page_dashboard(user))
            elif path == '/services':
                if not user:
                    self.redirect('/login')
                    return
                self.send_html(self.page_services(user))
            elif path == '/notifications':
                if not user:
                    self.redirect('/login')
                    return
                self.send_html(self.page_notifications(user))
            elif path == '/profile':
                if not user:
                    self.redirect('/login')
                    return
                self.send_html(self.page_profile(user))
            elif path == '/history':
                if not user:
                    self.redirect('/login')
                    return
                self.send_html(self.page_history(user))
            elif path == '/pay':
                if not user:
                    self.redirect('/login')
                    return
                service_id = query.get('id', [None])[0]
                service = self.db.get_service(service_id)
                if service:
                    self.send_html(self.page_payment(service))
                else:
                    self.redirect('/dashboard')
            elif path == '/confirm_pay':
                if not user:
                    self.redirect('/login')
                    return
                service_id = query.get('id', [None])[0]
                location = query.get('loc', [''])[0]
                if service_id:
                    self.db.create_order(user['id'], service_id, location)
                self.redirect('/dashboard')
            elif path == '/admin/approve':
                if not user or user['role'] != 'مدیر':
                    self.redirect('/login')
                    return
                target_id = query.get('id', [None])[0]
                if target_id:
                    self.db.approve_user(target_id)
                self.redirect('/profile')
            else:
                self.send_error(404)
        except Exception as e:
            print(f"GET Error: {e}")
            try: self.send_error(404)
            except: pass

    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            params = urllib.parse.parse_qs(post_data)

            if self.path == '/login_action':
                phone = params.get('phone', [''])[0]
                password = params.get('password', [''])[0]
                user = self.db.authenticate(phone, password)
                if user:
                    self.send_response(302)
                    self.send_header('Set-Cookie', f'user_id={user["id"]}; Path=/')
                    self.send_header('Location', '/dashboard')
                    self.end_headers()
                else:
                    self.redirect('/?error=1')
            elif self.path == '/register_action':
                fullname = params.get('fullname', [''])[0]
                phone = params.get('phone', [''])[0]
                password = params.get('password', [''])[0]
                role = params.get('role', [''])[0]
                card_number = params.get('card_number', [''])[0]
                if self.db.register_user(fullname, phone, password, role, card_number):
                    self.redirect('/?msg=registered')
                else:
                    self.redirect('/register?error=exists')
            elif self.path == '/add_service':
                user_id = AuthManager.get_user_id(self.headers)
                category = params.get('category', ['سایر'])[0]
                title = params.get('title', [''])[0]
                price = params.get('price', [''])[0]
                desc = params.get('desc', [''])[0]
                if user_id and title and price:
                    self.db.add_service(user_id, category, title, price, desc)
                self.redirect('/services')
        except Exception as e:
            print(f"POST Error: {e}")
            try: self.send_error(500)
            except: pass

    def redirect(self, url):
        self.send_response(302)
        self.send_header('Location', url)
        self.end_headers()

    def send_html(self, html):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        try: self.wfile.write(html.encode('utf-8'))
        except ConnectionAbortedError: pass

    def get_category_icon(self, category):
        icons = {
            'سم': ('bg-red', '<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>'),
            'کود': ('bg-green', '<svg viewBox="0 0 24 24"><path d="M17 12h-5v5h5v-5zM16 1v2H8V1H6v2H5c-1.11 0-1.99.9-1.99 2L3 19c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2h-1V1h-2zm3 18H5V8h14v11z"/></svg>'),
            'تراکتور': ('bg-orange', '<svg viewBox="0 0 24 24"><path d="M20 8h-3V4H3c-1.1 0-2 .9-2 2v11h2c0 1.66 1.34 3 3 3s3-1.34 3-3h6c0 1.66 1.34 3 3 3s3-1.34 3-3h2v-5l-3-4zM6 18.5c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm13.5-9l1.96 2.5H17V9.5h2.5zm-1.5 9c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z"/></svg>'),
            'کارگر': ('bg-blue', '<svg viewBox="0 0 24 24"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>'),
            'سایر': ('bg-gray', '<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>')
        }
        return icons.get(category, icons['سایر'])

    def wrap_layout(self, content, user=None, active_tab="", unread=0):
        nav = ""
        if user:
            badge = f'<span class="nav-badge">{unread}</span>' if unread > 0 else ""
            nav = f"""
            <div class="bottom-nav">
                <a href="/dashboard" class="nav-item {'active' if active_tab == 'home' else ''}"><svg viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>خانه</a>
                <a href="/services" class="nav-item {'active' if active_tab == 'services' else ''}"><svg viewBox="0 0 24 24"><path d="M4 8h4V4H4v4zm6 12h4v-4h-4v4zm-6 0h4v-4H4v4zm0-6h4v-4H4v4zm6 0h4v-4h-4v4zm6-10v4h4V4h-4zm-6 4h4V4h-4v4zm6 6h4v-4h-4v4zm0 6h4v-4h-4v4z"/></svg>خدمات</a>
                <a href="/notifications" class="nav-item {'active' if active_tab == 'notifications' else ''}"><svg viewBox="0 0 24 24"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>اعلان‌ها{badge}</a>
                <a href="/profile" class="nav-item {'active' if active_tab == 'profile' else ''}"><svg viewBox="0 0 24 24"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>پروفایل</a>
            </div>"""
        
        header = f'<div class="navbar"><h2>🌾 برنج‌کاران</h2><div style="font-size:0.8em; color:#666;">{user["fullname"] if user else ""}</div></div>'

        return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>برنج‌کاران</title>
    <link rel="manifest" href="/manifest.json"><meta name="theme-color" content="#ffffff">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" /><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&display=swap');
        * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
        body {{ font-family: 'Vazirmatn', Tahoma; background: #f0f2f5; margin: 0; direction: rtl; color: #333; padding-bottom: 70px; }}
        .navbar {{ background: #ffffff; padding: 15px 20px; color: #333; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .navbar h2 {{ font-size: 1.1em; margin: 0; font-weight: bold; }}
        .container {{ padding: 15px; max-width: 600px; margin: 0 auto; }}
        .card {{ background: #ffffff; padding: 15px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 15px; }}
        h3 {{ color: #333; margin-top: 0; font-size: 1em; margin-bottom: 10px; }}
        .btn {{ background: #2e7d32; color: white; padding: 12px; border: none; border-radius: 8px; cursor: pointer; text-decoration: none; display: flex; align-items: center; justify-content: center; font-family: inherit; font-weight: bold; transition: 0.2s; width: 100%; font-size: 1em; }}
        .btn:active {{ background: #1b5e20; }}
        input, select {{ padding: 12px; width: 100%; margin: 5px 0 15px 0; border: 1px solid #ddd; border-radius: 8px; box-sizing: border-box; font-family: inherit; font-size: 1em; background: #fafafa; }}
        .services-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px 10px; padding: 10px 0; }}
        .grid-item {{ display: flex; flex-direction: column; align-items: center; text-decoration: none; color: #444; text-align: center; }}
        .grid-icon {{ width: 50px; height: 50px; border-radius: 15px; display: flex; align-items: center; justify-content: center; margin-bottom: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
        .grid-icon svg {{ width: 28px; height: 28px; fill: white; }}
        .grid-title {{ font-size: 0.75em; font-weight: 500; }}
        .bg-green {{ background: linear-gradient(135deg, #43a047, #2e7d32); }}
        .bg-blue {{ background: linear-gradient(135deg, #1e88e5, #1565c0); }}
        .bg-red {{ background: linear-gradient(135deg, #e53935, #c62828); }}
        .bg-orange {{ background: linear-gradient(135deg, #fb8c00, #ef6c00); }}
        .bg-gray {{ background: linear-gradient(135deg, #757575, #424242); }}
        .list-item {{ display: flex; align-items: center; justify-content: space-between; background: #fff; padding: 15px; border-radius: 12px; margin-bottom: 10px; text-decoration: none; color: #333; border: 1px solid #f0f0f0; }}
        .list-info h4 {{ margin: 0 0 5px 0; font-size: 1em; color: #1b5e20; }}
        .list-info p {{ margin: 0; font-size: 0.85em; color: #666; }}
        .notif-card {{ background: #fff; border-right: 4px solid #1e88e5; padding: 15px; border-radius: 8px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        .bottom-nav {{ position: fixed; bottom: 0; left: 0; width: 100%; background: #ffffff; display: flex; justify-content: space-around; align-items: center; box-shadow: 0 -1px 5px rgba(0,0,0,0.1); z-index: 1000; padding: 8px 0; border-top: 1px solid #f0f0f0; }}
        .nav-item {{ display: flex; flex-direction: column; align-items: center; text-decoration: none; color: #888; font-size: 0.7em; flex: 1; position: relative; }}
        .nav-item.active {{ color: #2e7d32; font-weight: bold; }}
        .nav-item svg {{ width: 24px; height: 24px; fill: currentColor; margin-bottom: 2px; }}
        .nav-item .nav-badge {{ position: absolute; top: -2px; right: 25%; background: #e53935; color: white; border-radius: 50%; font-size: 0.6em; width: 14px; height: 14px; display: flex; align-items: center; justify-content: center; border: 1px solid #fff; }}
        #map {{ height: 250px; width: 100%; border-radius: 8px; margin: 10px 0; }}
    </style>
</head>
<body>
    {header}
    <div class="container">{content}</div>
    {nav}
</body></html>"""

    def page_login(self):
        content = """
        <div class="card" style="margin-top: 50px;">
            <h3 style="text-align:center;">ورود به سامانه</h3>
            <form method="POST" action="/login_action">
                <label>شماره موبایل</label><input type="text" name="phone" required>
                <label>رمز عبور</label><input type="password" name="password" required>
                <button type="submit" class="btn">ورود</button>
            </form>
            <div style="text-align:center; margin-top:15px;"><a href="/register" style="color:#2e7d32; text-decoration:none;">ساخت حساب جدید</a></div>
        </div>"""
        return self.wrap_layout(content)

    def page_register(self):
        content = """
        <div class="card" style="margin-top: 50px;">
            <h3 style="text-align:center;">ثبت‌نام</h3>
            <form method="POST" action="/register_action">
                <label>نام و نام خانوادگی</label><input type="text" name="fullname" required>
                <label>شماره موبایل</label><input type="text" name="phone" required>
                <label>رمز عبور</label><input type="password" name="password" required>
                <label>نقش</label><select name="role"><option value="کشاورز">کشاورز</option><option value="ارائه‌دهنده خدمات">ارائه‌دهنده خدمات</option></select>
                <label>شماره کارت</label><input type="text" name="card_number">
                <button type="submit" class="btn">ثبت‌نام</button>
            </form>
        </div>"""
        return self.wrap_layout(content)

    def page_dashboard(self, user):
        unread = self.db.get_unread_notification_count(user['id'])
        content = """
        <div class="card" style="padding: 15px 10px;">
            <div class="services-grid">
                <a href="/services" class="grid-item"><div class="grid-icon bg-green"><svg viewBox="0 0 24 24"><path d="M20 8h-3V4H3c-1.1 0-2 .9-2 2v11h2c0 1.66 1.34 3 3 3s3-1.34 3-3h6c0 1.66 1.34 3 3 3s3-1.34 3-3h2v-5l-3-4zM6 18.5c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm13.5-9l1.96 2.5H17V9.5h2.5zm-1.5 9c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z"/></svg></div><span class="grid-title">خدمات</span></a>
                <a href="/profile" class="grid-item"><div class="grid-icon bg-blue"><svg viewBox="0 0 24 24"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg></div><span class="grid-title">پروفایل</span></a>
                <a href="/notifications" class="grid-item"><div class="grid-icon bg-red"><svg viewBox="0 0 24 24"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg></div><span class="grid-title">اعلان‌ها</span></a>
                <a href="/history" class="grid-item"><div class="grid-icon bg-orange"><svg viewBox="0 0 24 24"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg></div><span class="grid-title">تاریخچه</span></a>
            </div>
        </div>
        <div class="card" style="text-align: center; background: #e8f5e9; border: 1px solid #c8e6c9;">
            <h3 style="color: #1b5e20;">خوش آمدید 👋</h3>
            <p style="font-size: 0.9em;">برای شروع، روی آیکون «خدمات» کلیک کنید.</p>
        </div>"""
        return self.wrap_layout(content, user, 'home', unread)

    def page_services(self, user):
        unread = self.db.get_unread_notification_count(user['id'])
        if user['role'] == 'ارائه‌دهنده خدمات':
            services = self.db.get_user_services(user['id'])
            content = """
            <div class="card"><h3>➕ ثبت خدمت جدید</h3>
                <form method="POST" action="/add_service">
                    <label>نوع خدمت</label>
                    <select name="category">
                        <option value="سم">سم</option>
                        <option value="کود">کود</option>
                        <option value="تراکتور">تراکتور</option>
                        <option value="کارگر">کارگر</option>
                        <option value="سایر">سایر</option>
                    </select>
                    <label>عنوان خدمت</label>
                    <input type="text" name="title" placeholder="مثلاً سم علف کش" required>
                    <label>قیمت</label>
                    <input type="text" name="price" placeholder="قیمت" required>
                    <label>توضیحات</label>
                    <input type="text" name="desc" placeholder="توضیحات">
                    <button type="submit" class="btn">ثبت</button>
                </form>
            </div>"""
            for s in services:
                icon_bg, icon_svg = self.get_category_icon(s['category'])
                content += f"""
                <div class="list-item">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <div class="grid-icon {icon_bg}" style="width:40px; height:40px; margin:0;">{icon_svg}</div>
                        <div class="list-info"><h4>{s['title']}</h4><p>قیمت: {s['price']}</p></div>
                    </div>
                </div>"""
        else:
            services = self.db.get_all_services()
            content = ""
            if not services:
                content += '<div class="card"><p style="text-align:center;">خدمتی موجود نیست.</p></div>'
            for s in services:
                icon_bg, icon_svg = self.get_category_icon(s['category'])
                content += f"""
                <div class="list-item">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <div class="grid-icon {icon_bg}" style="width:40px; height:40px; margin:0;">{icon_svg}</div>
                        <div class="list-info"><h4>{s['title']}</h4><p>قیمت: {s['price']}</p></div>
                    </div>
                    <a href="/pay?id={s["id"]}" class="btn" style="width:auto; padding:8px 15px; font-size:0.9em;">رزرو</a>
                </div>"""
        
        return self.wrap_layout(content, user, 'services', unread)

    def page_notifications(self, user):
        notifications = self.db.get_user_notifications(user['id'])
        content = ""
        if not notifications:
            content = "<div class='card'><p style='text-align:center;'>هیچ اعلانی وجود ندارد.</p></div>"
        else:
            for n in notifications:
                map_btn = f"<a href='{n['link']}' target='_blank' class='btn' style='background: #1e88e5; margin-top:10px; padding: 8px;'>📍 مشاهده زمین</a>" if n['link'] else ""
                content += f'<div class="notif-card"><small style="color:gray;">{n["created_at"]}</small><p style="margin-top:8px;">{n["content"]}</p>{map_btn}</div>'
        
        return self.wrap_layout(content, user, 'notifications', 0)

    def page_profile(self, user):
        unread = self.db.get_unread_notification_count(user['id'])
        content = f"""
        <div class="card" style="text-align: center; padding: 30px 15px;">
            <div style="width: 80px; height: 80px; background: #e8f5e9; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 15px auto;">
                <svg style="width: 40px; height: 40px; fill: #2e7d32;" viewBox="0 0 24 24"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>
            </div>
            <h3>{user['fullname']}</h3>
            <p style="color: #666;">{user['phone']}</p>
            <span style="background:#e8f5e9; color:#2e7d32; padding:5px 10px; border-radius:10px;">{user['role']}</span>
        </div>"""
        
        if user['role'] == 'مدیر':
            users = self.db.get_all_users()
            content += "<h3 style='margin-top:20px;'>مدیریت کاربران</h3>"
            for u in users:
                action = "-" if u['approved'] else f"<a href='/admin/approve?id={u['id']}' class='btn' style='width:auto; padding:5px 10px; font-size:0.8em;'>تأیید</a>"
                content += f'<div class="list-item"><div class="list-info"><h4>{u["fullname"]}</h4><p>{u["role"]}</p></div>{action}</div>'

        content += '<a href="/logout" class="btn" style="background: #e53935; margin-top: 20px;">🚪 خروج</a>'
        return self.wrap_layout(content, user, 'profile', unread)

    def page_history(self, user):
        unread = self.db.get_unread_notification_count(user['id'])
        content = "<h3 style='margin-bottom:15px;'>تاریخچه فعالیت‌ها</h3>"
        
        if user['role'] == 'ارائه‌دهنده خدمات':
            orders = self.db.get_provider_orders(user['id'])
            if not orders:
                content += '<div class="card"><p style="text-align:center;">هنوز سفارشی برای شما ثبت نشده است.</p></div>'
            else:
                for o in orders:
                    content += f"""
                    <div class="list-item">
                        <div class="list-info">
                            <h4>سفارش: {o['title']}</h4>
                            <p>کشاورز: {o['farmer_name']}</p>
                            <p>تاریخ: {o['created_at']}</p>
                        </div>
                        <span style="background:#e8f5e9; color:#2e7d32; padding:5px 10px; border-radius:10px; font-size:0.8em;">{o['status']}</span>
                    </div>"""
        else:
            orders = self.db.get_user_orders(user['id'])
            if not orders:
                content += '<div class="card"><p style="text-align:center;">شما هنوز سفارشی ثبت نکرده‌اید.</p></div>'
            else:
                for o in orders:
                    content += f"""
                    <div class="list-item">
                        <div class="list-info">
                            <h4>{o['title']}</h4>
                            <p>ارائه‌دهنده: {o['provider_name']}</p>
                            <p>تاریخ: {o['created_at']}</p>
                        </div>
                        <span style="background:#e8f5e9; color:#2e7d32; padding:5px 10px; border-radius:10px; font-size:0.8em;">{o['status']}</span>
                    </div>"""
        
        return self.wrap_layout(content, user, 'history', unread)

    def page_payment(self, service):
        content = f"""
        <div class="card">
            <h3>{service['title']}</h3>
            <p>مبلغ: <b>{service['price']} تومان</b></p>
            <p>شماره کارت: <b>{service['card_number']}</b></p>
            <div id="map"></div>
            <p id="selected_loc" style="text-align:center; color:#2e7d32; font-weight:bold;">موقعیتی انتخاب نشده.</p>
            <button onclick="submitOrder()" class="btn">✅ تأیید و پرداخت</button>
        </div>
        <script>
            var map = L.map('map').setView([35.6892, 51.3890], 10);
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);
            var marker;
            map.on('click', function(e) {{
                if (marker) map.removeLayer(marker);
                marker = L.marker(e.latlng).addTo(map);
                document.getElementById('selected_loc').innerText = 'موقعیت: ' + e.latlng.lat.toFixed(4) + ',' + e.latlng.lng.toFixed(4);
                document.getElementById('selected_loc').dataset.loc = e.latlng.lat.toFixed(4) + ',' + e.latlng.lng.toFixed(4);
            }});
            function submitOrder() {{
                var loc = document.getElementById('selected_loc').dataset.loc;
                if (!loc) {{ alert('لطفاً روی نقشه کلیک کنید.'); return; }}
                window.location.href = '/confirm_pay?id={service['id']}&loc=' + loc;
            }}
        </script>"""
        return self.wrap_layout(content)

class App:
    def __init__(self, port=PORT):
        self.port = port

    def run(self):
        handler = WebApp
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("", self.port), handler) as httpd:
            print(f"✅ Server started at http://localhost:{self.port}")
            httpd.serve_forever()

if __name__ == '__main__':
    app = App()
    app.run()