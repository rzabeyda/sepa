import asyncio
import calendar
import os
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Tallinn")

def now_tallinn() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
from dotenv import load_dotenv
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_IDS = {7308147004, 1865333207}  # Ромик, Сергей

bot = Bot(token=API_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

DB_FILE = os.path.join(BASE_DIR, "bot.db")

def db_connect():
    return sqlite3.connect(DB_FILE)

def init_db():
    con = db_connect()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            service TEXT NOT NULL, year INTEGER NOT NULL, month INTEGER NOT NULL,
            day INTEGER NOT NULL, time TEXT NOT NULL, name TEXT NOT NULL,
            phone TEXT NOT NULL, reminded_24 INTEGER DEFAULT 0,
            reminded_2 INTEGER DEFAULT 0, review_sent INTEGER DEFAULT 0,
            rebooking_sent INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
            date TEXT NOT NULL, time TEXT
        );
        CREATE TABLE IF NOT EXISTS cancelled (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            service TEXT NOT NULL, year INTEGER NOT NULL, month INTEGER NOT NULL,
            day INTEGER NOT NULL, time TEXT NOT NULL, name TEXT NOT NULL,
            phone TEXT NOT NULL, cancelled_by TEXT NOT NULL, cancelled_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, service TEXT NOT NULL, old_date TEXT NOT NULL,
            old_time TEXT NOT NULL, new_date TEXT NOT NULL, new_time TEXT NOT NULL,
            transferred_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, service TEXT NOT NULL, rating INTEGER NOT NULL,
            text TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            price TEXT NOT NULL, duration TEXT NOT NULL, duration_min INTEGER NOT NULL,
            img TEXT NOT NULL, sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1, description TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            description TEXT NOT NULL,
            link TEXT NOT NULL,
            photo_id TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            btn_text TEXT DEFAULT '',
            btn_url TEXT DEFAULT '',
            sent_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            voucher_sent INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS vouchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            discount_pct INTEGER NOT NULL DEFAULT 30,
            used INTEGER DEFAULT 0,
            used_at TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            banned_at TEXT NOT NULL,
            reason TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            stars INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS completed_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            day INTEGER NOT NULL,
            time TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            completed_at TEXT NOT NULL
        );
    """)
    con.commit(); con.close()

init_db()

def column_exists(con, table, column):
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)

def migrate_db():
    con = db_connect()
    # ALTER TABLE только если колонки ещё нет
    alter_stmts = [
        ("bookings",  "review_sent",    "ALTER TABLE bookings ADD COLUMN review_sent INTEGER DEFAULT 0"),
        ("bookings",  "rebooking_sent", "ALTER TABLE bookings ADD COLUMN rebooking_sent INTEGER DEFAULT 0"),
        ("referrals", "voucher_sent",   "ALTER TABLE referrals ADD COLUMN voucher_sent INTEGER DEFAULT 0"),
        ("reviews",   "username",       "ALTER TABLE reviews ADD COLUMN username TEXT DEFAULT ''"),
        ("services",  "description",    "ALTER TABLE services ADD COLUMN description TEXT DEFAULT ''"),
    ]
    for table, col, stmt in alter_stmts:
        if not column_exists(con, table, col):
            try: con.execute(stmt); con.commit()
            except Exception as _e: print(f"[WARN] {_e}")
    for stmt in [
        """CREATE TABLE IF NOT EXISTS tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            stars INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            added_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            btn_text TEXT DEFAULT '',
            btn_url TEXT DEFAULT '',
            sent_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",

        """CREATE TABLE IF NOT EXISTS completed_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            day INTEGER NOT NULL,
            time TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            completed_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            description TEXT NOT NULL,
            link TEXT NOT NULL,
            photo_id TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        )""",
    ]:
        try: con.execute(stmt); con.commit()
        except Exception as _e: print(f"[WARN] {_e}")
    con.close()

migrate_db()

# ── Friends DB helpers ────────────────────────────────────────────────────────
def get_all_friends():
    con = db_connect()
    rows = con.execute("SELECT * FROM friends ORDER BY sort_order, id").fetchall()
    con.close()
    cols = ["id","name","specialty","description","link","photo_id","sort_order"]
    return [dict(zip(cols, r)) for r in rows]

def get_friend(fid):
    con = db_connect()
    row = con.execute("SELECT * FROM friends WHERE id=?", (fid,)).fetchone()
    con.close()
    if not row: return None
    return dict(zip(["id","name","specialty","description","link","photo_id","sort_order"], row))

def add_friend(name, specialty, description, link, photo_id):
    con = db_connect()
    con.execute("INSERT INTO friends (name,specialty,description,link,photo_id) VALUES (?,?,?,?,?)",
                (name, specialty, description, link, photo_id))
    con.commit(); con.close()

def delete_friend(fid):
    con = db_connect()
    con.execute("DELETE FROM friends WHERE id=?", (fid,))
    con.commit(); con.close()

# ── Bot users ─────────────────────────────────────────────────────────────────
def register_user(user_id, username="", first_name=""):
    con = db_connect()
    con.execute(
        "INSERT OR IGNORE INTO bot_users (user_id, username, first_name, added_at) VALUES (?,?,?,?)",
        (user_id, username or "", first_name or "", now_tallinn().strftime("%Y-%m-%d %H:%M"))
    ); con.commit(); con.close()

def get_all_user_ids():
    con = db_connect()
    rows = con.execute("SELECT user_id FROM bot_users").fetchall()
    con.close(); return [r[0] for r in rows]

def get_users_count():
    con = db_connect()
    n = con.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
    con.close(); return n

# ── Broadcasts ────────────────────────────────────────────────────────────────
def save_broadcast(text, btn_text, btn_url, sent_count):
    con = db_connect()
    con.execute(
        "INSERT INTO broadcasts (text, btn_text, btn_url, sent_count, created_at) VALUES (?,?,?,?,?)",
        (text or "", btn_text or "", btn_url or "", sent_count, now_tallinn().strftime("%Y-%m-%d %H:%M"))
    ); con.commit(); con.close()

def get_broadcasts():
    con = db_connect()
    rows = con.execute("SELECT id, text, btn_text, btn_url, sent_count, created_at FROM broadcasts ORDER BY id DESC LIMIT 20").fetchall()
    con.close()
    return [{"id":r[0],"text":r[1],"btn_text":r[2],"btn_url":r[3],"sent_count":r[4],"created_at":r[5]} for r in rows]

def add_referral(referrer_id, referred_id):
    con = db_connect()
    exists = con.execute("SELECT id FROM referrals WHERE referred_id=?", (referred_id,)).fetchone()
    if exists: con.close(); return False
    con.execute("INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)",
        (referrer_id, referred_id, now_tallinn().strftime("%Y-%m-%d %H:%M")))
    con.commit(); con.close(); return True

def get_referral_count(user_id):
    con = db_connect()
    n = con.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user_id,)).fetchone()[0]
    con.close(); return n

# ── Vouchers ──────────────────────────────────────────────────────────────────
import random, string

def generate_voucher_code():
    return "SERGEY-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

def create_voucher(user_id, discount_pct=30):
    con = db_connect()
    for _ in range(10):
        code = generate_voucher_code()
        try:
            con.execute("INSERT INTO vouchers (code, user_id, discount_pct, created_at) VALUES (?,?,?,?)",
                (code, user_id, discount_pct, now_tallinn().strftime("%Y-%m-%d %H:%M")))
            con.commit(); con.close(); return code
        except: continue
    con.close(); return None

def get_all_vouchers():
    con = db_connect()
    rows = con.execute("SELECT id, code, user_id, discount_pct, used, used_at, created_at FROM vouchers ORDER BY id DESC LIMIT 50").fetchall()
    con.close()
    return [{"id":r[0],"code":r[1],"user_id":r[2],"discount_pct":r[3],"used":r[4],"used_at":r[5],"created_at":r[6]} for r in rows]

def get_user_vouchers(user_id):
    con = db_connect()
    rows = con.execute("SELECT id, code, discount_pct, used, used_at, created_at FROM vouchers WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
    con.close()
    result = []
    for r in rows:
        v = {"id":r[0],"code":r[1],"discount_pct":r[2],"used":r[3],"used_at":r[4],"created_at":r[5]}
        try:
            created = datetime.strptime(v["created_at"], "%Y-%m-%d %H:%M")
            v["expired"] = (now_tallinn() - created).days > 180
        except: v["expired"] = False
        result.append(v)
    return result

def use_voucher(code):
    con = db_connect()
    con.execute("UPDATE vouchers SET used=1, used_at=? WHERE code=?",
        (now_tallinn().strftime("%Y-%m-%d %H:%M"), code.upper()))
    con.commit(); con.close()

def get_voucher(code):
    con = db_connect()
    row = con.execute("SELECT id, code, user_id, discount_pct, used, used_at, created_at FROM vouchers WHERE code=?", (code.upper(),)).fetchone()
    con.close()
    if not row: return None
    v = {"id":row[0],"code":row[1],"user_id":row[2],"discount_pct":row[3],"used":row[4],"used_at":row[5],"created_at":row[6]}
    # Проверяем срок 180 дней
    try:
        created = datetime.strptime(v["created_at"], "%Y-%m-%d %H:%M")
        if (now_tallinn() - created).days > 180:
            v["expired"] = True
        else:
            v["expired"] = False
    except:
        v["expired"] = False
    return v

# ── Ban helpers ───────────────────────────────────────────────────────────────
def ban_user(user_id, username=""):
    con = db_connect()
    con.execute("INSERT OR REPLACE INTO banned_users (user_id, username, banned_at) VALUES (?,?,?)",
        (user_id, username or "", now_tallinn().strftime("%Y-%m-%d %H:%M")))
    # Удаляем все брони
    bookings = con.execute("SELECT * FROM bookings WHERE user_id=?", (user_id,)).fetchall()
    for row in bookings:
        b = _row_to_booking(row)
        con.execute("INSERT INTO cancelled (user_id,service,year,month,day,time,name,phone,cancelled_by,cancelled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (b["user_id"],b["service"],b["year"],b["month"],b["day"],b["time"],b["name"],b["phone"],
             "ban", now_tallinn().strftime("%Y-%m-%d %H:%M")))
    con.execute("DELETE FROM bookings WHERE user_id=?", (user_id,))
    con.commit(); con.close()

def unban_user(user_id):
    con = db_connect()
    con.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
    con.commit(); con.close()

def is_banned(user_id):
    con = db_connect()
    row = con.execute("SELECT user_id FROM banned_users WHERE user_id=?", (user_id,)).fetchone()
    con.close(); return row is not None

def get_banned_users():
    con = db_connect()
    rows = con.execute("SELECT user_id, username, banned_at FROM banned_users ORDER BY banned_at DESC").fetchall()
    con.close()
    return [{"user_id":r[0],"username":r[1],"banned_at":r[2]} for r in rows]

def find_user_by_username(username):
    username = username.lstrip("@").lower()
    con = db_connect()
    row = con.execute("SELECT user_id, username, first_name FROM bot_users WHERE LOWER(username)=?", (username,)).fetchone()
    con.close()
    return {"user_id":row[0],"username":row[1],"first_name":row[2]} if row else None
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_SERVICES = [
    ("Классический массаж",    "30€", "60 мин", 60, "images/1.jpg", 1),
    ("Спортивный массаж",      "30€", "60 мин", 60, "images/2.jpg", 2),
    ("Тайский массаж",         "30€", "60 мин", 60, "images/3.jpg", 3),
    ("Ароматический массаж",   "30€", "60 мин", 60, "images/4.jpg", 4),
    ("Сегментарный массаж",    "30€", "60 мин", 60, "images/5.jpg", 5),
    ("Лимфодренажный массаж",  "30€", "60 мин", 60, "images/6.jpg", 6),
    ("Лечебный массаж",        "30€", "60 мин", 60, "images/7.jpg", 7),
]

def _seed_services():
    con = db_connect()
    for name, price, duration, duration_min, img, sort_order in _DEFAULT_SERVICES:
        exists = con.execute("SELECT id FROM services WHERE name=?", (name,)).fetchone()
        if exists:
            con.execute("UPDATE services SET price=?, duration=?, duration_min=?, sort_order=? WHERE name=?",
                (price, duration, duration_min, sort_order, name))
        else:
            con.execute("INSERT INTO services (name,price,duration,duration_min,img,sort_order) VALUES (?,?,?,?,?,?)",
                (name, price, duration, duration_min, img, sort_order))
    con.commit()
    con.close()

_seed_services()

def get_services_db():
    con = db_connect()
    rows = con.execute(
        "SELECT id,name,price,duration,duration_min,img FROM services WHERE active=1 ORDER BY sort_order,id"
    ).fetchall(); con.close()
    return [{"id":r[0],"name":r[1],"price":r[2],"duration":r[3],"duration_min":r[4],"img":r[5]} for r in rows]

def get_all_services_db():
    con = db_connect()
    rows = con.execute(
        "SELECT id,name,price,duration,duration_min,img,active FROM services ORDER BY sort_order,id"
    ).fetchall(); con.close()
    return [{"id":r[0],"name":r[1],"price":r[2],"duration":r[3],"duration_min":r[4],"img":r[5],"active":r[6]} for r in rows]

def get_service(name):
    con = db_connect()
    row = con.execute(
        "SELECT id,name,price,duration,duration_min,img,description FROM services WHERE name=? AND active=1", (name,)
    ).fetchone(); con.close()
    if not row: return None
    return {"id":row[0],"name":row[1],"price":row[2],"duration":row[3],"duration_min":row[4],"img":row[5],"description":row[6] if len(row)>6 else ""}

def get_service_by_id(sid):
    con = db_connect()
    row = con.execute(
        "SELECT id,name,price,duration,duration_min,img,active,description FROM services WHERE id=?", (sid,)
    ).fetchone(); con.close()
    if not row: return None
    return {"id":row[0],"name":row[1],"price":row[2],"duration":row[3],"duration_min":row[4],"img":row[5],"active":row[6],"description":row[7] if len(row)>7 else ""}

def add_service_db(name, price, duration, duration_min, img):
    try:
        con = db_connect()
        max_o = con.execute("SELECT MAX(sort_order) FROM services").fetchone()[0] or 0
        con.execute(
            "INSERT INTO services (name,price,duration,duration_min,img,sort_order,active) VALUES (?,?,?,?,?,?,1)",
            (name, price, duration, duration_min, img, max_o+1)
        ); con.commit(); con.close(); return True
    except: return False

def update_service_db(sid, field, value):
    if field not in {"name","price","duration","duration_min","img","active","description"}: return
    con = db_connect()
    con.execute(f"UPDATE services SET {field}=? WHERE id=?", (value, sid))
    con.commit(); con.close()

def deactivate_service_db(sid): update_service_db(sid, "active", 0)
def restore_service_db(sid):    update_service_db(sid, "active", 1)

def delete_service_db(sid):
    con = db_connect()
    con.execute("DELETE FROM services WHERE id=?", (sid,))
    con.commit(); con.close()

def add_booking(user_id, service, year, month, day, time, name, phone):
    con = db_connect(); cur = con.cursor()
    cur.execute(
        "INSERT INTO bookings (user_id,service,year,month,day,time,name,phone) VALUES (?,?,?,?,?,?,?,?)",
        (user_id,service,year,month,day,time,name,phone)
    ); bid = cur.lastrowid; con.commit(); con.close(); return bid

def remove_booking(bid):
    con = db_connect(); con.execute("DELETE FROM bookings WHERE id=?", (bid,)); con.commit(); con.close()

def log_completed_booking(b):
    """Сохраняем завершённую бронь для корректного учёта выручки."""
    con = db_connect()
    con.execute(
        "INSERT INTO completed_bookings (user_id,service,year,month,day,time,name,phone,completed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (b["user_id"], b["service"], b["year"], b["month"], b["day"], b["time"], b["name"], b["phone"],
         now_tallinn().strftime("%Y-%m-%d %H:%M"))
    )
    con.commit(); con.close()

def get_booking(bid):
    con = db_connect(); row = con.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone(); con.close()
    return _row_to_booking(row) if row else None

def get_user_bookings(user_id):
    con = db_connect()
    rows = con.execute("SELECT * FROM bookings WHERE user_id=? ORDER BY year,month,day,time", (user_id,)).fetchall()
    con.close(); return [_row_to_booking(r) for r in rows]

def get_all_bookings():
    con = db_connect()
    rows = con.execute("SELECT * FROM bookings ORDER BY year,month,day,time").fetchall()
    con.close(); return [_row_to_booking(r) for r in rows]

def update_booking_field(bid, field, value):
    if field not in {"service","year","month","day","time","name","phone"}: return
    con = db_connect(); con.execute(f"UPDATE bookings SET {field}=? WHERE id=?", (value,bid)); con.commit(); con.close()

def _row_to_booking(row):
    return {"id":row[0],"user_id":row[1],"service":row[2],"year":row[3],"month":row[4],
            "day":row[5],"time":row[6],"name":row[7],"phone":row[8],
            "reminded_24":row[9],"reminded_2":row[10],"review_sent":row[11] if len(row)>11 else 0,
            "rebooking_sent":row[12] if len(row)>12 else 0}

def log_cancellation(b, cancelled_by):
    con = db_connect()
    con.execute(
        "INSERT INTO cancelled (user_id,service,year,month,day,time,name,phone,cancelled_by,cancelled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (b["user_id"],b["service"],b["year"],b["month"],b["day"],b["time"],b["name"],b["phone"],
         cancelled_by, now_tallinn().strftime("%Y-%m-%d %H:%M"))
    ); con.commit(); con.close()

def log_transfer(bid, user_id, service, old_date, old_time, new_date, new_time):
    con = db_connect()
    con.execute(
        "INSERT INTO transfers (booking_id,user_id,service,old_date,old_time,new_date,new_time,transferred_at) VALUES (?,?,?,?,?,?,?,?)",
        (bid,user_id,service,old_date,old_time,new_date,new_time,now_tallinn().strftime("%Y-%m-%d %H:%M"))
    ); con.commit(); con.close()

def add_review(booking_id, user_id, service, rating, text, username=""):
    con = db_connect()
    cur = con.execute(
        "INSERT INTO reviews (booking_id,user_id,service,rating,text,created_at,username) VALUES (?,?,?,?,?,?,?)",
        (booking_id,user_id,service,rating,text,now_tallinn().strftime("%Y-%m-%d %H:%M"),username)
    ); rid = cur.lastrowid; con.commit(); con.close(); return rid

def get_service_price_int(service_name):
    svc = get_service(service_name)
    if not svc: return 0
    m = re.search(r"(\d+)", svc["price"]); return int(m.group(1)) if m else 0

def get_stats():
    con = db_connect(); s = {}
    now = now_tallinn()
    s["total_active"]    = con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    s["total_cancelled"] = con.execute("SELECT COUNT(*) FROM cancelled").fetchone()[0]
    s["total_transfers"] = con.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
    s["total_reviews"]   = con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    avg = con.execute("SELECT AVG(rating) FROM reviews").fetchone()[0]
    s["avg_rating"] = round(avg,1) if avg else 0
    rows = con.execute("SELECT service,COUNT(*) FROM completed_bookings GROUP BY service ORDER BY COUNT(*) DESC").fetchall()
    s["by_service"] = rows
    s["total_revenue"] = sum(get_service_price_int(r[0]) for r in con.execute("SELECT service FROM completed_bookings").fetchall())
    s["revenue_by_service"] = {svc:get_service_price_int(svc)*cnt for svc,cnt in rows}
    s["month_revenue"] = sum(get_service_price_int(r[0]) for r in con.execute(
        "SELECT service FROM completed_bookings WHERE year=? AND month=?", (now.year,now.month)).fetchall())
    s["cancelled_by_client"] = con.execute("SELECT COUNT(*) FROM cancelled WHERE cancelled_by='client'").fetchone()[0]
    s["cancelled_by_master"] = con.execute("SELECT COUNT(*) FROM cancelled WHERE cancelled_by='master'").fetchone()[0]
    s["last_reviews"] = con.execute("SELECT rating,text,service,created_at FROM reviews ORDER BY id DESC LIMIT 5").fetchall()
    con.close(); return s

def get_stats_month(year, month):
    con = db_connect(); s = {}
    s["bookings"] = con.execute("SELECT COUNT(*) FROM completed_bookings WHERE year=? AND month=?", (year,month)).fetchone()[0]
    s["cancelled"] = con.execute("SELECT COUNT(*) FROM cancelled WHERE substr(cancelled_at,1,7)=?", (f"{year}-{month:02d}",)).fetchone()[0]
    s["revenue"] = sum(get_service_price_int(r[0]) for r in con.execute(
        "SELECT service FROM completed_bookings WHERE year=? AND month=?", (year,month)).fetchall())
    rows = con.execute("SELECT service,COUNT(*) FROM completed_bookings WHERE year=? AND month=? GROUP BY service ORDER BY COUNT(*) DESC", (year,month)).fetchall()
    s["by_service"] = rows
    avg = con.execute("SELECT AVG(rating) FROM reviews WHERE substr(created_at,1,7)=?", (f"{year}-{month:02d}",)).fetchone()[0]
    s["avg_rating"] = round(avg,1) if avg else 0
    s["reviews"] = con.execute("SELECT COUNT(*) FROM reviews WHERE substr(created_at,1,7)=?", (f"{year}-{month:02d}",)).fetchone()[0]
    tips_rows = con.execute("SELECT stars FROM tips WHERE substr(created_at,1,7)=?", (f"{year}-{month:02d}",)).fetchall()
    s["tips_count"] = len(tips_rows)
    s["tips_stars"] = sum(r[0] for r in tips_rows)
    s["tips_eur"] = round(s["tips_stars"] * 0.013, 2)
    con.close(); return s

def get_stats_all():
    con = db_connect(); s = {}
    s["total_bookings"]  = con.execute("SELECT COUNT(*) FROM completed_bookings").fetchone()[0]
    s["total_cancelled"] = con.execute("SELECT COUNT(*) FROM cancelled").fetchone()[0]
    s["total_transfers"] = con.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
    s["total_reviews"]   = con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    avg = con.execute("SELECT AVG(rating) FROM reviews").fetchone()[0]
    s["avg_rating"] = round(avg,1) if avg else 0
    s["total_revenue"] = sum(get_service_price_int(r[0]) for r in con.execute("SELECT service FROM completed_bookings").fetchall())
    rows = con.execute("SELECT service,COUNT(*) FROM completed_bookings GROUP BY service ORDER BY COUNT(*) DESC").fetchall()
    s["by_service"] = rows
    s["cancelled_by_client"] = con.execute("SELECT COUNT(*) FROM cancelled WHERE cancelled_by='client'").fetchone()[0]
    s["cancelled_by_master"] = con.execute("SELECT COUNT(*) FROM cancelled WHERE cancelled_by='master'").fetchone()[0]
    tips_rows = con.execute("SELECT stars FROM tips").fetchall()
    s["tips_count"] = len(tips_rows)
    s["tips_stars"] = sum(r[0] for r in tips_rows)
    s["tips_eur"] = round(s["tips_stars"] * 0.013, 2)
    con.close(); return s

def get_client_visits(user_id):
    con = db_connect()
    n = con.execute("SELECT COUNT(*) FROM bookings WHERE user_id=?", (user_id,)).fetchone()[0]
    con.close(); return n

def get_client_rank(visits):
    if visits >= 7: return "💎 VIP"
    elif visits >= 3: return "⭐ Постоянный"
    else: return "🆕 Новичок"

def block_day(date):
    con = db_connect(); con.execute("INSERT OR IGNORE INTO schedule (type,date) VALUES ('day',?)", (date,)); con.commit(); con.close()

def unblock_day(date):
    con = db_connect(); con.execute("DELETE FROM schedule WHERE type='day' AND date=?", (date,)); con.commit(); con.close()

def block_slot(date, time):
    con = db_connect(); con.execute("INSERT OR IGNORE INTO schedule (type,date,time) VALUES ('slot',?,?)", (date,time)); con.commit(); con.close()

def unblock_slot(date, time):
    con = db_connect(); con.execute("DELETE FROM schedule WHERE type='slot' AND date=? AND time=?", (date,time)); con.commit(); con.close()

def get_blocked_days():
    con = db_connect(); rows = con.execute("SELECT date FROM schedule WHERE type='day'").fetchall(); con.close(); return [r[0] for r in rows]

def get_blocked_slots_for_date(date):
    con = db_connect(); rows = con.execute("SELECT time FROM schedule WHERE type='slot' AND date=?", (date,)).fetchall(); con.close(); return [r[0] for r in rows]

def get_all_blocked_slots():
    con = db_connect(); rows = con.execute("SELECT date,time FROM schedule WHERE type='slot' ORDER BY date,time").fetchall(); con.close()
    result = {}
    for date,time in rows: result.setdefault(date,[]).append(time)
    return result

CONTACTS_FULL  = "📞 +372 53 730 882\n💬 Telegram: @Sepa17"
CONTACTS_SHORT = "📞 +372 53 730 882\n💬 Telegram: @Sepa17"

MONTHS = {1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
          7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"}
MONTHS_GEN = {1:"Января",2:"Февраля",3:"Марта",4:"Апреля",5:"Мая",6:"Июня",
              7:"Июля",8:"Августа",9:"Сентября",10:"Октября",11:"Ноября",12:"Декабря"}
TIME_SLOTS = ["09:00","10:00","11:00","12:00","13:00","14:00","15:00","16:00","17:00","18:00"]

class Booking(StatesGroup):
    service=State(); duration=State(); year=State(); month=State(); day=State(); time=State(); name=State(); phone=State(); voucher=State()

class EditBooking(StatesGroup):
    service=State(); year=State(); month=State(); day=State(); time=State(); name=State(); phone=State()

class Reschedule(StatesGroup):
    year=State(); month=State(); day=State(); time=State()

class Review(StatesGroup):
    rating=State(); text=State()

class TipState(StatesGroup):
    amount = State()

class TipCustom(StatesGroup):
    amount = State()

class AdminReview(StatesGroup):
    text=State(); add_text=State(); add_rating=State(); add_service=State()

class AddService(StatesGroup):
    name=State(); price=State(); duration=State(); img=State()

class EditService(StatesGroup):
    value=State(); img=State()

class AddFriend(StatesGroup):
    photo=State(); name=State(); specialty=State(); description=State(); link=State()

class Broadcast(StatesGroup):
    text=State(); btn_text=State(); btn_url=State(); confirm=State()

class BanUser(StatesGroup):
    username=State()

def date_key(year, month, day): return f"{year}-{month:02d}-{day:02d}"
def is_day_blocked(year, month, day): return date_key(year,month,day) in get_blocked_days()

def duration_minutes(svc):
    if not svc: return 60
    if svc.get("duration_min"): return svc["duration_min"]
    m = re.search(r"(\d+)", svc.get("duration","60")); return int(m.group(1)) if m else 60

def get_end_time(start_slot, dur_min):
    h,m = int(start_slot.split(":")[0]), int(start_slot.split(":")[1])
    total = h*60+m+dur_min; return f"{total//60:02d}:{total%60:02d}"

def get_available_slots(year, month, day, new_dur_min=60, exclude_bid=None):
    now = now_tallinn()
    key = date_key(year,month,day)
    manual_blocked = set()
    for slot in get_blocked_slots_for_date(key):
        h,m = int(slot.split(":")[0]), int(slot.split(":")[1]); manual_blocked.add(h*60+m)
    booked = []
    for b in get_all_bookings():
        if exclude_bid and b["id"]==exclude_bid: continue
        if b["year"]!=year or b["month"]!=month or b["day"]!=day: continue
        svc = get_service(b["service"]); dur = duration_minutes(svc)
        h,m = int(b["time"].split(":")[0]), int(b["time"].split(":")[1])
        booked.append((h*60+m, h*60+m+dur))
    def is_free(start, dur):
        if start > 18*60: return False
        for bs,be in booked:
            if start < be and start+dur > bs: return False
        if start in manual_blocked: return False
        return True
    candidates = list(range(9*60, 18*60+1, 60))
    for _,be in booked:
        if be%60==30 and 9*60<=be<=18*60: candidates.append(be)
    if year==now.year and month==now.month and day==now.day:
        cutoff = now.hour*60+now.minute
        candidates = [c for c in candidates if c > cutoff]
    result = sorted(set(c for c in candidates if is_free(c, new_dur_min)))
    return [f"{c//60:02d}:{c%60:02d}" for c in result]

DAYS_RU = {0: "в понедельник", 1: "во вторник", 2: "в среду", 3: "в четверг", 4: "в пятницу", 5: "в субботу", 6: "в воскресенье"}

def time_until_booking(b) -> str:
    """Возвращает строку типа '⏳ Через 2 дня, в пятницу 21 Марта в 14:00'"""
    try:
        appt = datetime(b["year"], b["month"], b["day"],
                        int(b["time"].split(":")[0]), int(b["time"].split(":")[1]))
        now = now_tallinn()
        delta = appt - now
        total_min = int(delta.total_seconds() / 60)
        if total_min < 0:
            return ""
        if total_min < 60:
            return f"⏳ Уже через {total_min} мин!"
        if total_min < 120:
            return f"⏳ Уже через час!"
        total_hours = total_min // 60
        if total_hours < 24:
            return f"⏳ Сегодня в {b['time']} — скоро!"
        days = delta.days
        weekday = DAYS_RU.get(appt.weekday(), "")
        if days == 1:
            return f"⏳ Завтра {weekday} {b['day']} {MONTHS_GEN[b['month']]} в {b['time']}"
        days_word = "день" if days % 10 == 1 and days % 100 != 11 else ("дня" if 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14 else "дней")
        return f"⏳ Через {days} {days_word}"
    except Exception:
        return ""

def format_booking(b, idx=None, username=None):
    month_name = MONTHS_GEN[b["month"]]
    prefix = f"Бронь №{idx}\n" if idx else ""
    svc = get_service(b["service"]); dur_str = svc["duration"] if svc else ""
    tg_line = f" | 💬 @{username}" if username else ""
    addr = "\n\n🏠 Linnamäe tee 24–44" if username is None else ""
    countdown = time_until_booking(b)
    countdown_line = f"\n{countdown}" if countdown else ""
    return f"{prefix}💆 {b['service']}\n⏱ Длительность: ~{dur_str}\n🕐 {b['time']} | {b['day']} {month_name}{countdown_line}\n👤 {b['name']} 📞 {b['phone']}{tg_line}{addr}".strip()

def bottom_kb(is_admin=False, user_id=None):
    has_booking = bool(user_id and get_user_bookings(user_id))
    broni_btn = KeyboardButton(text="✅ Брони") if has_booking else KeyboardButton(text="🗓 Брони")
    row1 = [KeyboardButton(text="💆 Услуги"), broni_btn, KeyboardButton(text="🎁 Бонусы")]
    row2 = [KeyboardButton(text="💬 Написать"), KeyboardButton(text="👱‍♀️ Коллеги"), KeyboardButton(text="⭐ Отзывы")]
    buttons = [row1, row2]
    if is_admin: buttons.append([KeyboardButton(text="🔐 Админка")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def main_menu_kb():
    rows = []
    for s in get_services_db():
        rows.append([InlineKeyboardButton(text=f"{s['name']} — {s['price']} (~{s['duration']})", callback_data=f"svc:{s['name']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def months_kb(year):
    now = now_tallinn(); rows=[]; row=[]; shown=0
    for num,name in MONTHS.items():
        if num < now.month: continue
        if shown >= 3: break
        row.append(InlineKeyboardButton(text=name, callback_data=f"month:{num}")); shown+=1
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def days_kb(year, month, new_dur_min=60):
    now = now_tallinn(); _,days_in_month = calendar.monthrange(year,month); rows=[]; row=[]
    for day in range(1, days_in_month+1):
        if year==now.year and month==now.month and day<now.day: continue
        if is_day_blocked(year,month,day): continue
        if not get_available_slots(year,month,day,new_dur_min): continue
        row.append(InlineKeyboardButton(text=str(day), callback_data=f"day:{day}"))
        if len(row)==7: rows.append(row); row=[]
    if row: rows.append(row)
    if not rows: rows.append([InlineKeyboardButton(text="Нет доступных дней", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_months"),
                 InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def time_kb(year, month, day, exclude_bid=None, new_dur_min=60):
    available = get_available_slots(year,month,day,new_dur_min,exclude_bid)
    rows=[]; row=[]
    for slot in available:
        row.append(InlineKeyboardButton(text=slot, callback_data="t_"+slot.replace(":","")))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    if not rows: rows.append([InlineKeyboardButton(text="Нет свободного времени", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_days"),
                 InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_to_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]])

def booking_list_kb(user_id):
    rows = []
    for b in get_user_bookings(user_id):
        label = f"{b['time']} | {b['day']} {MONTHS_GEN[b['month']]} | {b['service'][:20]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"viewb:{b['id']}")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def make_calendar_url(b) -> str:
    """Генерирует ссылку на создание события в Google Calendar."""
    try:
        svc = get_service(b["service"])
        dur = duration_minutes(svc)
        start = datetime(b["year"], b["month"], b["day"],
                         int(b["time"].split(":")[0]), int(b["time"].split(":")[1]))
        end = start + timedelta(minutes=dur)
        fmt = "%Y%m%dT%H%M%S"
        title = f"Маникюр у Сергея — {b['service']}"
        details = f"Адрес: Linnamäe tee 24–44\nТелефон: +372 53 730 882"
        url = (
            f"https://calendar.google.com/calendar/r/eventedit"
            f"?text={title.replace(' ', '+')}"
            f"&dates={start.strftime(fmt)}/{end.strftime(fmt)}"
            f"&details={details.replace(' ', '+').replace(':', '%3A')}"
            f"&location=Linnam%C3%A4e+tee+24-44"
        )
        return url
    except Exception:
        return ""

def booking_actions_kb(bid, b=None):
    rows = [
        [InlineKeyboardButton(text="🔄 Перенести", callback_data=f"reschedule:{bid}"),
         InlineKeyboardButton(text="🗑 Удалить",   callback_data=f"del_booking:{bid}")],
    ]
    if b:
        cal_url = make_calendar_url(b)
        if cal_url:
            rows.append([InlineKeyboardButton(text="📅 Добавить в календарь", url=cal_url)])
    rows.append([InlineKeyboardButton(text="◀️ Все брони", callback_data="my_booking"),
                 InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def confirm_delete_kb(bid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_del:{bid}"),
        InlineKeyboardButton(text="❌ Отмена",       callback_data=f"viewb:{bid}")]])

def edit_options_kb(bid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💅 Изменить услугу",      callback_data=f"efield:service:{bid}")],
        [InlineKeyboardButton(text="📅 Изменить дату",        callback_data=f"efield:date:{bid}")],
        [InlineKeyboardButton(text="⏱ Изменить время",       callback_data=f"efield:time:{bid}")],
        [InlineKeyboardButton(text="👤 Изменить имя",         callback_data=f"efield:name:{bid}")],
        [InlineKeyboardButton(text="📞 Изменить телефон",     callback_data=f"efield:phone:{bid}")],
        [InlineKeyboardButton(text="◀️ Назад",                callback_data=f"viewb:{bid}")]])

def services_edit_kb(bid):
    rows = []
    for s in get_services_db():
        rows.append([InlineKeyboardButton(text=f"{s['name']} — {s['price']} (~{s['duration']})", callback_data=f"esvc:{bid}:{s['name']}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"edit_booking:{bid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Все брони",      callback_data="admin_all"),
         InlineKeyboardButton(text="📅 На сегодня",     callback_data="admin_today")],
        [InlineKeyboardButton(text="🗓 Расписание",     callback_data="admin_schedule"),
         InlineKeyboardButton(text="📊 Статистика",     callback_data="admin_stats")],
        [InlineKeyboardButton(text="💆 Услуги",         callback_data="admin_services"),
         InlineKeyboardButton(text="⭐ Отзывы",         callback_data="admin_reviews")],
        [InlineKeyboardButton(text="👱‍♀️ Коллеги",       callback_data="admin_masters"),
         InlineKeyboardButton(text="📣 Рассылка",       callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎁 Бонусы",         callback_data="admin_vouchers"),
         InlineKeyboardButton(text="🚫 Бан",            callback_data="admin_ban_menu")]])

def admin_friends_kb():
    rows = []
    for f in get_all_friends():
        rows.append([InlineKeyboardButton(
            text=f"👤 {f['name']} — {f['specialty']}",
            callback_data=f"friend_manage:{f['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить друга", callback_data="friend_add")])
    rows.append([InlineKeyboardButton(text="◀️ Назад",          callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def friend_manage_kb(fid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить",  callback_data=f"friend_delete:{fid}")],
        [InlineKeyboardButton(text="◀️ Назад",    callback_data="admin_masters")]])

def admin_services_kb():
    rows = []
    for s in get_all_services_db():
        icon = "✅" if s["active"] else "❌"
        rows.append([InlineKeyboardButton(text=f"{icon} {s['name']} — {s['price']} ({s['duration']})", callback_data=f"svc_manage:{s['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить услугу", callback_data="svc_add")])
    rows.append([InlineKeyboardButton(text="◀️ Назад",           callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def svc_manage_kb(sid, active):
    toggle_text = "❌ Скрыть от клиентов" if active else "✅ Показать клиентам"
    toggle_cb   = f"svc_hide:{sid}" if active else f"svc_show:{sid}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название",    callback_data=f"svc_edit:name:{sid}")],
        [InlineKeyboardButton(text="💰 Изменить цену",        callback_data=f"svc_edit:price:{sid}")],
        [InlineKeyboardButton(text="⏱ Изменить длительность", callback_data=f"svc_edit:duration:{sid}")],
        [InlineKeyboardButton(text="📝 Изменить описание",    callback_data=f"svc_edit:description:{sid}")],
        [InlineKeyboardButton(text="🖼 Изменить картинку",    callback_data=f"svc_edit:img:{sid}")],
        [InlineKeyboardButton(text=toggle_text,               callback_data=toggle_cb)],
        [InlineKeyboardButton(text="🗑 Удалить услугу",         callback_data=f"svc_delete_confirm:{sid}")],
        [InlineKeyboardButton(text="◀️ Назад",                callback_data="admin_services")]])

def schedule_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Заблокировать день",   callback_data="sched_block_day")],
        [InlineKeyboardButton(text="✅ Разблокировать день",  callback_data="sched_unblock_day")],
        [InlineKeyboardButton(text="🚫 Заблокировать часы",   callback_data="sched_block_slots")],
        [InlineKeyboardButton(text="✅ Разблокировать часы",  callback_data="sched_unblock_slots")],
        [InlineKeyboardButton(text="📋 Показать расписание",  callback_data="sched_show")],
        [InlineKeyboardButton(text="🔓 Разблокировать всё",   callback_data="sched_unblock_all_confirm")],
        [InlineKeyboardButton(text="◀️ Назад",                callback_data="admin_back")]])

def schedule_months_kb(action):
    now=now_tallinn(); rows=[]; row=[]; shown=0
    for num,name in MONTHS.items():
        if num<now.month: continue
        if shown>=3: break
        row.append(InlineKeyboardButton(text=name, callback_data=f"sm_{action}:{num}")); shown+=1
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_schedule")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def schedule_days_kb(month, action):
    now=now_tallinn(); _,dim=calendar.monthrange(now.year,month); rows=[]; row=[]
    for day in range(1,dim+1):
        if month==now.month and day<now.day: continue
        key=date_key(now.year,month,day)
        label=f"🚫{day}" if key in get_blocked_days() else str(day)
        row.append(InlineKeyboardButton(text=label, callback_data=f"sd_{action}:{month}:{day}"))
        if len(row)==7: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_schedule")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def schedule_slots_kb(month, day, action):
    year=now_tallinn().year; key=date_key(year,month,day)
    blocked_manual=get_blocked_slots_for_date(key)
    booked_slots=set()
    for b in get_all_bookings():
        if b["year"]==year and b["month"]==month and b["day"]==day:
            svc=get_service(b["service"]); dur=duration_minutes(svc)
            h,m=int(b["time"].split(":")[0]),int(b["time"].split(":")[1])
            start=h*60+m
            for minute in range(start,start+dur,60):
                slot=f"{minute//60:02d}:00"
                if slot in TIME_SLOTS: booked_slots.add(slot)
    rows=[]; row=[]
    for slot in TIME_SLOTS:
        if slot in blocked_manual: label=f"🔒{slot}"
        elif slot in booked_slots: label=f"👤{slot}"
        else: label=slot
        row.append(InlineKeyboardButton(text=label, callback_data=f"ss_{action}:{month}:{day}:{slot.replace(':','')}"))
        if len(row)==3: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_schedule")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ── Ban middleware (registered after DB init) ─────────────────────────────────
@dp.message.outer_middleware()
async def ban_check_middleware(handler, event, data):
    try:
        if hasattr(event, 'from_user') and event.from_user:
            if is_banned(event.from_user.id):
                if not (hasattr(event, 'text') and event.text and event.text.startswith('/start')):
                    await event.answer("⛔️ Доступ к боту ограничен.")
                    return
    except Exception: pass
    return await handler(event, data)
# ─────────────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer("⛔️ Доступ к боту ограничен."); return
    register_user(user_id, message.from_user.username or "", message.from_user.first_name or "")
    # Обработка реферальной ссылки
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].split("_")[1])
            if referrer_id != user_id:
                is_new = add_referral(referrer_id, user_id)
                if is_new:
                    # Просто сообщаем — промокоды выдадутся после завершения услуги
                    referrer_name = message.from_user.first_name or "Клиент"
                    try:
                        await bot.send_message(referrer_id,
                            f"🎀 По твоей ссылке пришёл новый клиент — {referrer_name}!\n\n"
                            f"Промокод на скидку 30% вы оба получите после того как друг завершит свой первый визит 💕")
                    except Exception as _e: print(f"[WARN] {_e}")
                    await message.answer(
                        f"🎀 Ты пришла по реферальной ссылке подруги!\n\n"
                        f"После твоего первого визита к Сергею вы оба получите промокод на скидку *30%* 💆\n\n"
                        f"Записывайся — и бонус придёт автоматически!",
                        parse_mode="Markdown")
                    for _aid in ADMIN_IDS:
                        try: await bot.send_message(_aid, f"🎀 Новый реферал!\n\n👤 {referrer_name} пришёл по чьей-то ссылке.\nПромокоды выдадутся после первого завершённого визита.")
                        except Exception as _e: print(f"[WARN] {_e}")
        except Exception as _e: print(f"[WARN] {_e}")
    is_admin = message.from_user.id in ADMIN_IDS
    photo = FSInputFile(os.path.join(BASE_DIR, "images/sergey.jpg"))
    await message.answer(".", reply_markup=bottom_kb(is_admin, user_id=message.from_user.id))
    await message.answer_photo(
        photo=photo,
        caption=(
            "*Сергей — мастер массажа в Таллине* 💆\n\n"
            "✨ Более 7 лет опыта\n"
            "⏱ Пн–Пт: 17:15–19:00 | Сб–Вс: 10:00–17:00\n\n"
            "Запишитесь онлайн — это займёт 1 минуту 👇"
        ),
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )

@dp.message(F.text == "💆 Услуги")
async def btn_services(message: types.Message, state: FSMContext):
    await state.clear(); await message.answer("Выберите услугу 👇", reply_markup=main_menu_kb())

@dp.message(F.text.in_({"✅ Брони", "🗓 Брони"}))
async def btn_my_bookings(message: types.Message, state: FSMContext):
    await state.clear()
    if not get_user_bookings(message.from_user.id):
        await message.answer("У вас нет активных броней."); return
    await message.answer("📋 Ваши брони:", reply_markup=booking_list_kb(message.from_user.id))

@dp.message(F.text == "⭐ Отзывы")
async def btn_reviews(message: types.Message):
    con = db_connect()
    rows = con.execute(
        "SELECT rating, text, service, created_at, username FROM reviews ORDER BY id DESC LIMIT 20"
    ).fetchall()
    con.close()
    if not rows:
        await message.answer("😊 Отзывов пока нет — будьте первым!")
        return
    text = "⭐ Отзывы клиентов:\n\n"
    for row in rows:
        rating, rv, svc_name, created_at = row[0], row[1], row[2], row[3]
        username = row[4] if len(row) > 4 and row[4] else "Аноним"
        if username != "Аноним" and not username.startswith("@"):
            username = "@" + username
        stars = "⭐" * rating
        date_str = created_at[:10] if created_at else ""
        text += f"{stars} — {svc_name} ({date_str})\n"
        text += f"👤 {username}\n"
        if rv:
            text += f"💬 {rv}"
        text += "\n\n"
    await message.answer(text.strip())

@dp.message(F.text == "👱‍♀️ Коллеги")
async def btn_friends(message: types.Message):
    friends = get_all_friends()
    if not friends:
        await message.answer(
            "👱‍♀️ *Коллеги Сергея*\n\n"
            "🔧 Раздел в разработке — скоро здесь появятся проверенные коллеги Сергея 💆",
            parse_mode="Markdown")
        return
    rows = []
    for f in friends:
        rows.append([InlineKeyboardButton(text=f"👤 {f['name']} — {f['specialty']}", callback_data=f"friend_view:{f['id']}")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("👱‍♀️ Коллеги Сергея\n\nВыберите мастера 👇", reply_markup=kb)

@dp.callback_query(F.data.startswith("friend_view:"))
async def friend_view(call: types.CallbackQuery):
    fid = int(call.data.split(":")[1])
    f = get_friend(fid)
    if not f:
        await call.answer("Не найдено", show_alert=True); return
    caption = f"👤 *{f['name']}*\n💼 {f['specialty']}\n\n{f['description']}\n\n🔗 {f['link']}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="masters_back")]])
    await call.message.answer_photo(photo=f['photo_id'], caption=caption, parse_mode="Markdown", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "masters_back")
async def friends_back(call: types.CallbackQuery):
    friends = get_all_friends()
    rows = []
    for f in friends:
        rows.append([InlineKeyboardButton(text=f"👤 {f['name']} — {f['specialty']}", callback_data=f"friend_view:{f['id']}")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.answer("💆 Коллеги Сергея\n\nВыберите мастера 👇", reply_markup=kb)
    await call.answer()

@dp.message(F.text == "🎁 Бонусы")
async def btn_referral(message: types.Message):
    user_id = message.from_user.id
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{user_id}"
    vouchers = get_user_vouchers(user_id)
    active = [v for v in vouchers if not v["used"] and not v.get("expired")]
    expired = [v for v in vouchers if not v["used"] and v.get("expired")]
    used = [v for v in vouchers if v["used"]]

    if not vouchers:
        await message.answer(
            f"🎁 *Бонусы и скидки*\n\n"
            f"Приведи друга к Сергею — и вы оба получите промокод на *скидку 30%* на любую услугу! 🤝\n\n"
            f"Как это работает:\n"
            f"1️⃣ Скопируй свою ссылку ниже\n"
            f"2️⃣ Отправь другу\n"
            f"3️⃣ Когда он запишется и сходит на процедуру — оба получат промокод на скидку 30% на любую услугу\n"
            f"4️⃣ При записи введи промокод и получи скидку\n\n"
            f"Твоя ссылка:\n`{ref_link}`",
            parse_mode="Markdown")
        return

    text = f"🎁 *Мои бонусы*\n\n"
    if active:
        text += "✅ *Активные:*\n"
        for v in active:
            try:
                created = datetime.strptime(v["created_at"], "%Y-%m-%d %H:%M")
                expires = created + timedelta(days=180)
                exp_str = f"действует до {expires.day} {MONTHS_GEN[expires.month]} {expires.year}"
            except Exception:
                exp_str = "действует 180 дней"
            text += f"`{v['code']}` — скидка {v['discount_pct']}% ({exp_str})\n"
        text += "\n"
    if expired:
        text += "⏰ *Просроченные:*\n"
        for v in expired:
            text += f"`{v['code']}` — истёк срок\n"
        text += "\n"
    if used:
        text += f"✔️ Использовано: {len(used)} промокод(ов)\n\n"

    text += f"📌 Чтобы применить промокод — введи код при оформлении записи.\n\n"
    text += f"━━━━━━━━━━━━━━━━━\n"
    text += f"🎁 *Как получить ещё?*\n"
    text += f"Поделись ссылкой с другом — когда он запишется, вы оба получите новый промокод -30%!\n\n"
    text += f"Твоя ссылка:\n`{ref_link}`"
    await message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "show_portfolio")
async def cb_portfolio(call: types.CallbackQuery):
    portfolio_files = [os.path.join(BASE_DIR, f"images/{i}.jpg") for i in range(86, 100)]
    existing = [f for f in portfolio_files if os.path.exists(f)]
    if not existing:
        await call.answer("🖼 Портфолио пока пустое — скоро добавим работы! 💅", show_alert=True); return
    await call.answer()
    chunk_size = 10
    for i in range(0, len(existing), chunk_size):
        chunk = existing[i:i + chunk_size]
        media = [types.InputMediaPhoto(media=FSInputFile(path)) for path in chunk]
        await call.message.answer_media_group(media=media)

@dp.message(F.text == "💬 Написать")
async def btn_chat(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Написать Сергею", url="https://t.me/Sepa17")],
        [InlineKeyboardButton(text="👥 Комьюнити", url="https://t.me/beautytallinn")]])
    await message.answer("💬 Выбери как удобнее:", reply_markup=kb)

@dp.callback_query(F.data.startswith("tip_open:"))
async def tip_open(call: types.CallbackQuery):
    bid = int(call.data.split(":")[1])
    await call.message.answer("💝 Выберите сумму чаевых:", reply_markup=tip_amounts_kb(bid))
    await call.answer()

@dp.callback_query(F.data.startswith("tip_send:"))
async def tip_send(call: types.CallbackQuery):
    parts = call.data.split(":")
    bid, amount = int(parts[1]), int(parts[2])
    await call.message.answer_invoice(
        title="💝 Чаевые мастеру",
        description=" ",
        payload=f"tip:{bid}",
        currency="XTR",
        prices=[{"label": "Чаевые", "amount": amount}]
    )
    await call.answer()

@dp.callback_query(F.data == "tip_close")
async def tip_close(call: types.CallbackQuery):
    await call.message.delete()
    await call.answer()

@dp.callback_query(F.data.startswith("tip_custom:"))
async def tip_custom_start(call: types.CallbackQuery, state: FSMContext):
    bid = int(call.data.split(":")[1])
    await state.update_data(tip_bid=bid)
    await state.set_state(TipCustom.amount)
    await call.message.answer("✏️ Введите количество звёзд (минимум 1):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tip_close")]]))
    await call.answer()

@dp.message(TipCustom.amount)
async def tip_custom_amount(message: types.Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit() or int(message.text.strip()) < 1:
        await message.answer("⚠️ Введите целое число, минимум 1")
        return
    data = await state.get_data()
    amount = int(message.text.strip())
    bid = data.get("tip_bid", 0)
    await state.clear()
    await message.answer_invoice(
        title="💝 Чаевые мастеру",
        description=" ",
        payload=f"tip:{bid}",
        currency="XTR",
        prices=[{"label": "Чаевые", "amount": amount}]
    )

@dp.pre_checkout_query()
async def pre_checkout(query: types.PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    stars = message.successful_payment.total_amount
    username = message.from_user.username or message.from_user.first_name or ""
    # Сохраняем чаевые в БД
    con = db_connect()
    con.execute("INSERT INTO tips (user_id, username, stars, created_at) VALUES (?,?,?,?)",
        (message.from_user.id, username, stars, now_tallinn().strftime("%Y-%m-%d %H:%M")))
    con.commit(); con.close()
    await message.answer(f"🙏 Спасибо за {stars} ⭐! Сергей очень рад 💆")
    for _aid in ADMIN_IDS:
        try:
            await bot.send_message(_aid, f"💝 Новые чаевые! {stars} ⭐ от @{username}")
        except Exception as _e: print(f"[WARN] {_e}")

@dp.message(F.text == "🔐 Админка")
async def btn_admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: await message.answer("⛔️ Нет доступа."); return
    await message.answer(f"🔐 Панель администратора\nВсего броней: {len(get_all_bookings())}", reply_markup=admin_panel_kb())

@dp.callback_query(F.data == "main_menu")
async def go_main_menu(call: types.CallbackQuery, state: FSMContext):
    await state.clear(); await call.message.answer("Выберите услугу 👇", reply_markup=main_menu_kb()); await call.answer()

@dp.callback_query(F.data == "my_booking")
async def show_my_bookings(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if not get_user_bookings(call.from_user.id):
        await call.answer("У вас нет активных броней.", show_alert=True); return
    await call.message.answer("📋 Ваши брони:", reply_markup=booking_list_kb(call.from_user.id)); await call.answer()

@dp.callback_query(F.data.startswith("viewb:"))
async def view_booking(call: types.CallbackQuery):
    bid=int(call.data.split(":")[1]); b=get_booking(bid)
    if not b or b["user_id"]!=call.from_user.id:
        await call.answer("Бронь не найдена.", show_alert=True); return
    bookings=get_user_bookings(call.from_user.id)
    idx=next((i+1 for i,x in enumerate(bookings) if x["id"]==bid),1)
    await call.message.answer(format_booking(b,idx), reply_markup=booking_actions_kb(bid, b)); await call.answer()

@dp.callback_query(F.data.startswith("del_booking:"))
async def delete_booking_confirm(call: types.CallbackQuery):
    bid=int(call.data.split(":")[1])
    await call.message.answer("❗️ Вы уверены, что хотите удалить эту бронь?", reply_markup=confirm_delete_kb(bid)); await call.answer()

@dp.callback_query(F.data.startswith("confirm_del:"))
async def confirm_delete(call: types.CallbackQuery):
    bid=int(call.data.split(":")[1]); b=get_booking(bid)
    if b and b["user_id"]==call.from_user.id:
        log_cancellation(b,"client"); remove_booking(bid)
        for _aid in ADMIN_IDS:
            try: await bot.send_message(_aid, f"🗑 Клиент отменил бронь!\n\n{format_booking(b)}")
            except Exception as _e: print(f"[WARN] {_e}")
    await call.message.answer("✅ Бронь удалена.", reply_markup=back_to_menu_kb()); await call.answer()

@dp.callback_query(F.data == "del_all_confirm")
async def del_all_confirm(call: types.CallbackQuery):
    kb=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить все", callback_data="del_all_yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="my_booking")]])
    await call.message.answer("❗️ Все ваши брони будут удалены!", reply_markup=kb); await call.answer()

@dp.callback_query(F.data == "del_all_yes")
async def del_all_yes(call: types.CallbackQuery):
    for b in get_user_bookings(call.from_user.id):
        remove_booking(b["id"])
        for _aid in ADMIN_IDS:
            try: await bot.send_message(_aid, f"🗑 Клиент удалил все брони!\n\n{format_booking(b)}")
            except Exception as _e: print(f"[WARN] {_e}")
    await call.message.answer("✅ Все брони удалены.", reply_markup=back_to_menu_kb()); await call.answer()

@dp.callback_query(F.data.startswith("edit_booking:"))
async def edit_booking_menu(call: types.CallbackQuery):
    bid=int(call.data.split(":")[1])
    await call.message.answer("Что хотите изменить?", reply_markup=edit_options_kb(bid)); await call.answer()

@dp.callback_query(F.data.startswith("efield:"))
async def edit_field_start(call: types.CallbackQuery, state: FSMContext):
    parts=call.data.split(":",2); field,bid=parts[1],int(parts[2])
    await state.update_data(edit_bid=bid)
    cancel_kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"viewb:{bid}")]])
    if field=="service":
        await call.message.answer("Выберите новую услугу:", reply_markup=services_edit_kb(bid)); await state.set_state(EditBooking.service)
    elif field=="date":
        await call.message.answer("Выберите год:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=str(now_tallinn().year), callback_data=f"year:{now_tallinn().year}"),
            InlineKeyboardButton(text=str(now_tallinn().year+1), callback_data=f"year:{now_tallinn().year+1}")]])); await state.set_state(EditBooking.year)
    elif field=="time":
        b=get_booking(bid)
        if b:
            svc=get_service(b["service"]); dur=duration_minutes(svc)
            await call.message.answer("Выберите новое время:", reply_markup=time_kb(b["year"],b["month"],b["day"],exclude_bid=bid,new_dur_min=dur))
        await state.set_state(EditBooking.time)
    elif field=="name":
        await call.message.answer("Введите новое имя:", reply_markup=cancel_kb); await state.set_state(EditBooking.name)
    elif field=="phone":
        await call.message.answer("Введите новый телефон:", reply_markup=cancel_kb); await state.set_state(EditBooking.phone)
    await call.answer()

async def notify_edit(bid):
    b=get_booking(bid)
    if not b: return
    bookings=get_user_bookings(b["user_id"]); idx=next((i+1 for i,x in enumerate(bookings) if x["id"]==bid),1)
    try: await bot.send_message(b["user_id"], f"✅ Ваша бронь обновлена!\n\n{format_booking(b,idx)}", reply_markup=booking_actions_kb(bid, b))
    except Exception as _e: print(f"[WARN] {_e}")
    for _aid in ADMIN_IDS:
        try: await bot.send_message(_aid, f"✏️ Клиент изменил бронь!\n\n{format_booking(b)}")
        except Exception as _e: print(f"[WARN] {_e}")

@dp.callback_query(F.data.startswith("esvc:"), EditBooking.service)
async def edit_service_save(call: types.CallbackQuery, state: FSMContext):
    _,bid_str,service_name=call.data.split(":",2); bid=int(bid_str)
    update_booking_field(bid,"service",service_name); await state.clear(); await notify_edit(bid); await call.answer()

@dp.callback_query(F.data.startswith("year:"), EditBooking.year)
async def edit_year_save(call: types.CallbackQuery, state: FSMContext):
    year=int(call.data.split(":")[1]); await state.update_data(edit_year=year)
    await call.message.answer(f"Выберите месяц ({year}):", reply_markup=months_kb(year)); await state.set_state(EditBooking.month); await call.answer()

@dp.callback_query(F.data.startswith("month:"), EditBooking.month)
async def edit_month_save(call: types.CallbackQuery, state: FSMContext):
    month=int(call.data.split(":")[1]); data=await state.get_data(); year=data.get("edit_year",now_tallinn().year)
    await state.update_data(edit_month=month)
    await call.message.answer("Выберите день:", reply_markup=days_kb(year,month)); await state.set_state(EditBooking.day); await call.answer()

@dp.callback_query(F.data.startswith("day:"), EditBooking.day)
async def edit_day_save(call: types.CallbackQuery, state: FSMContext):
    day=int(call.data.split(":")[1]); data=await state.get_data(); bid=data["edit_bid"]
    year=data.get("edit_year",now_tallinn().year); month=data.get("edit_month")
    if month:
        update_booking_field(bid,"year",year); update_booking_field(bid,"month",month); update_booking_field(bid,"day",day)
    await state.clear(); await notify_edit(bid); await call.answer()

@dp.callback_query(F.data.startswith("t_"), EditBooking.time)
async def edit_time_save(call: types.CallbackQuery, state: FSMContext):
    raw=call.data[2:]; time_str=raw[:2]+":"+raw[2:]; data=await state.get_data(); bid=data["edit_bid"]
    update_booking_field(bid,"time",time_str); await state.clear(); await notify_edit(bid); await call.answer()

@dp.message(EditBooking.name)
async def edit_name_save(message: types.Message, state: FSMContext):
    data=await state.get_data(); update_booking_field(data["edit_bid"],"name",message.text); await state.clear(); await notify_edit(data["edit_bid"])

@dp.message(EditBooking.phone)
async def edit_phone_save(message: types.Message, state: FSMContext):
    data=await state.get_data(); update_booking_field(data["edit_bid"],"phone",message.text); await state.clear(); await notify_edit(data["edit_bid"])

@dp.callback_query(F.data.startswith("svc:"))
async def service_choice(call: types.CallbackQuery, state: FSMContext):
    service_name=call.data[4:]; svc=get_service(service_name)
    if not svc: await call.answer("Услуга не найдена.", show_alert=True); return
    year=now_tallinn().year; await state.update_data(service=service_name,year=year)
    photo=FSInputFile(os.path.join(BASE_DIR, svc["img"]))
    dur_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ 60 мин — 30€", callback_data="dur:60:30€")],
        [InlineKeyboardButton(text="⏱ 90 мин — 40€", callback_data="dur:90:40€")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await call.message.answer_photo(photo=photo,
        caption=f"✅ Вы выбрали: *{service_name}*\n\nВыберите длительность:",
        parse_mode="Markdown", reply_markup=dur_kb)
    await state.set_state(Booking.duration); await call.answer()

@dp.callback_query(F.data.startswith("dur:"), Booking.duration)
async def duration_choice(call: types.CallbackQuery, state: FSMContext):
    parts = call.data.split(":"); dur_min = int(parts[1]); price = parts[2]
    await state.update_data(override_price=price, override_dur_min=dur_min, override_dur_str=f"{dur_min} мин")
    data = await state.get_data()
    year = data.get("year", now_tallinn().year)
    service_name = data.get("service", "")
    await call.message.answer(f"✅ {service_name}\n💰 {price} | ⏱ {dur_min} мин\n\nВыберите месяц:", reply_markup=months_kb(year))
    await state.set_state(Booking.month); await call.answer()

@dp.callback_query(F.data == "back_to_months")
async def back_to_months(call: types.CallbackQuery, state: FSMContext):
    data=await state.get_data(); year=data.get("year",now_tallinn().year)
    await call.message.answer("Выберите месяц:", reply_markup=months_kb(year)); await state.set_state(Booking.month); await call.answer()

@dp.callback_query(F.data.startswith("month:"), Booking.month)
async def month_choice(call: types.CallbackQuery, state: FSMContext):
    month=int(call.data.split(":")[1]); data=await state.get_data(); year=data.get("year",now_tallinn().year)
    await state.update_data(month=month); svc=get_service(data.get("service","")); dur=duration_minutes(svc)
    await call.message.answer(f"Выберите день ({MONTHS[month]}):", reply_markup=days_kb(year,month,dur))
    await state.set_state(Booking.day); await call.answer()

@dp.callback_query(F.data == "back_to_days")
async def back_to_days(call: types.CallbackQuery, state: FSMContext):
    data=await state.get_data(); year=data.get("year",now_tallinn().year); month=data.get("month")
    if month:
        await call.message.answer(f"Выберите день ({MONTHS[month]}):", reply_markup=days_kb(year,month)); await state.set_state(Booking.day)
    else:
        await call.message.answer("Выберите месяц:", reply_markup=months_kb(year)); await state.set_state(Booking.month)
    await call.answer()

@dp.callback_query(F.data.startswith("day:"), Booking.day)
async def day_choice(call: types.CallbackQuery, state: FSMContext):
    day=int(call.data.split(":")[1]); data=await state.get_data()
    year=data.get("year",now_tallinn().year); month=data.get("month")
    await state.update_data(day=day); svc=get_service(data.get("service","")); dur=duration_minutes(svc)
    await call.message.answer(f"✅ Вы выбрали: {day} {MONTHS[month]}\n\nВыберите удобное время:",
        reply_markup=time_kb(year,month,day,new_dur_min=dur))
    await state.set_state(Booking.time); await call.answer()

@dp.callback_query(F.data.startswith("t_"), Booking.time)
async def time_choice(call: types.CallbackQuery, state: FSMContext):
    raw=call.data[2:]; time_str=raw[:2]+":"+raw[2:]
    await state.update_data(time=time_str)
    await call.message.answer(f"✅ Вы выбрали: {time_str}\n\nВведите ваше имя:")
    await state.set_state(Booking.name); await call.answer()

@dp.message(Booking.name)
async def enter_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите ваш номер телефона:"); await state.set_state(Booking.phone)

@dp.message(Booking.phone)
async def enter_phone(message: types.Message, state: FSMContext):
    if re.search(r"[a-zA-Zа-яА-ЯёЁ]", message.text or ""):
        await message.answer("⚠️ Номер не должен содержать буквы. Попробуйте ещё раз:"); return
    await state.update_data(phone=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Без промокода", callback_data="skip_voucher")],
        [InlineKeyboardButton(text="🎁 Ввести промокод", callback_data="enter_voucher")]])
    await message.answer("🎁 Есть промокод на скидку?\n\nВведите код или продолжите без него:", reply_markup=kb)
    await state.set_state(Booking.voucher)

@dp.callback_query(F.data == "enter_voucher", Booking.voucher)
async def ask_voucher_code(call: types.CallbackQuery, state: FSMContext):
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Без промокода", callback_data="skip_voucher")]])
    await call.message.answer("Введите промокод (например: SERGEY-X7K2):", reply_markup=cancel_kb)
    await call.answer()

@dp.callback_query(F.data == "skip_voucher", Booking.voucher)
async def skip_voucher(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(voucher_code=None, voucher_discount=0)
    await _finalize_booking(call.message, state, call.from_user.id)
    await call.answer()

@dp.message(Booking.voucher)
async def process_voucher_code(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    voucher = get_voucher(code)
    if not voucher:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Без промокода", callback_data="skip_voucher")]])
        await message.answer("❌ Промокод не найден. Проверьте код и попробуйте снова:", reply_markup=kb); return
    if voucher["used"]:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Без промокода", callback_data="skip_voucher")]])
        await message.answer("❌ Этот промокод уже использован.", reply_markup=kb); return
    await state.update_data(voucher_code=code, voucher_discount=voucher["discount_pct"])
    data = await state.get_data()
    svc = get_service(data.get("service", ""))
    price_str = svc["price"] if svc else ""
    m = re.search(r"(\d+)", price_str)
    if m:
        original = int(m.group(1))
        discounted = round(original * (1 - voucher["discount_pct"] / 100))
        price_info = f"💰 {original}€ → {discounted}€ (-{voucher['discount_pct']}%)"
    else:
        price_info = f"Скидка {voucher['discount_pct']}%"
    await message.answer(f"✅ Промокод применён!\n\n{price_info}\n\nОформляю запись...")
    await _finalize_booking(message, state, message.from_user.id)

async def _finalize_booking(message, state, user_id):
    data = await state.get_data()
    svc = get_service(data.get("service", "")); dur = data.get("override_dur_min") or duration_minutes(svc)
    yr = data.get("year") or now_tallinn().year; mon = data.get("month"); day = data.get("day")
    phone = data.get("phone", "")
    available = get_available_slots(yr, mon, day, dur)
    if data["time"] not in available:
        await message.answer("⚠️ Это время уже заняли пока вы оформляли запись.\nПожалуйста, начните заново 👇", reply_markup=main_menu_kb())
        await state.clear(); return
    voucher_code = data.get("voucher_code")
    voucher_discount = data.get("voucher_discount", 0)
    bid = add_booking(user_id=user_id, service=data["service"], year=yr, month=mon, day=day, time=data["time"], name=data["name"], phone=phone)
    b = get_booking(bid)
    # Применяем промокод
    voucher_line = ""
    if voucher_code and voucher_discount:
        use_voucher(voucher_code)
        m = re.search(r"(\d+)", svc["price"] if svc else "")
        if m:
            original = int(m.group(1))
            discounted = round(original * (1 - voucher_discount / 100))
            voucher_line = f"\n🎀 Промокод {voucher_code}: {original}€ → {discounted}€"
    # Кнопки после подтверждения — с календарём
    confirm_kb_rows = []
    cal_url = make_calendar_url(b)
    if cal_url:
        confirm_kb_rows.append([InlineKeyboardButton(text="📅 Добавить в календарь", url=cal_url)])
    confirm_kb_rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=confirm_kb_rows)
    price_show = data.get("override_price") or (svc["price"] if svc else "")
    dur_show = data.get("override_dur_str") or (svc["duration"] if svc else "")
    await message.answer(f"✅ Бронь подтверждена!\n\n{format_booking(b)}{voucher_line}\n⏱ {dur_show} | 💰 {price_show}\n\n━━━━━━━━━━━━━━━━━\n📍 Контакты Сергея:\n{CONTACTS_SHORT}", reply_markup=confirm_kb)
    try:
        con_u = db_connect()
        u_row = con_u.execute("SELECT username FROM bot_users WHERE user_id=?", (user_id,)).fetchone()
        con_u.close()
        tg_username = u_row[0] if u_row and u_row[0] else ""
    except Exception as _e: print(f"[WARN] {_e}"); tg_username = ""
    tg_line = f"\n💬 @{tg_username}" if tg_username else ""
    visits = get_client_visits(user_id); rank = get_client_rank(visits)
    vw = "визит" if visits == 1 else ("визита" if 2 <= visits <= 4 else "визитов")
    voucher_admin = f"\n🎀 Промокод: {voucher_code} (-{voucher_discount}%)" if voucher_code else ""
    for _aid in ADMIN_IDS:
        try: await bot.send_message(_aid, f"🔔 Новая бронь!\n\n💆 {data['service']}\n⏱ {dur_show} | 💰 {price_show}\n🕐 {data['time']} | {day} {MONTHS_GEN[mon]}\n👤 {data['name']} 📞 {phone}{tg_line}\n{rank} • {visits} {vw}{voucher_admin}")
        except Exception as _e: print(f"[WARN] {_e}")
    await state.clear()

@dp.callback_query(F.data.startswith("admin_view:"))
async def admin_view_booking(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    bid=int(call.data.split(":")[1]); b=get_booking(bid)
    if not b: await call.answer("Бронь не найдена.", show_alert=True); return
    svc=get_service(b["service"]); dur=svc["duration"] if svc else ""
    visits=get_client_visits(b["user_id"]); rank=get_client_rank(visits)
    vw="визит" if visits==1 else ("визита" if 2<=visits<=4 else "визитов")
    text=f"📋 Бронь #{bid}\n\n💅 {b['service']}\n⏱ ~{dur}\n🕐 {b['time']} | {b['day']} {MONTHS_GEN[b['month']]}\n👤 {b['name']} 📞 {b['phone']}\n{rank} • {visits} {vw}"
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_booking:{bid}"),
         InlineKeyboardButton(text="🗑 Удалить",        callback_data=f"admin_del:{bid}")],
        [InlineKeyboardButton(text="◀️ Назад",          callback_data="admin_all")]])
    await call.message.answer(text, reply_markup=kb); await call.answer()

@dp.callback_query(F.data.startswith("admin_"))
async def admin_actions(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    action=call.data
    if action=="admin_all":
        all_b=get_all_bookings()
        if not all_b: await call.message.answer("Броней пока нет."); await call.answer(); return
        rows=[[InlineKeyboardButton(text=f"👁 {b['time']} {b['day']} {MONTHS[b['month']]} — {b['name']}", callback_data=f"admin_view:{b['id']}")] for b in all_b]
        rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
        await call.message.answer(f"📋 Все брони ({len(all_b)} шт.):", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif action=="admin_today":
        now=now_tallinn(); today_b=[b for b in get_all_bookings() if b["year"]==now.year and b["month"]==now.month and b["day"]==now.day]
        if not today_b: await call.message.answer("Сегодня броней нет.")
        else:
            rows=[[InlineKeyboardButton(text=f"👁 {b['time']} — {b['name']} | {b['service'][:20]}", callback_data=f"admin_view:{b['id']}")] for b in today_b]
            rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
            await call.message.answer(f"📅 Сегодня ({now.day} {MONTHS[now.month]}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif action=="admin_tomorrow":
        tom=now_tallinn()+timedelta(days=1); tom_b=[b for b in get_all_bookings() if b["year"]==tom.year and b["month"]==tom.month and b["day"]==tom.day]
        if not tom_b: await call.message.answer("Завтра броней нет.")
        else:
            rows=[[InlineKeyboardButton(text=f"👁 {b['time']} — {b['name']} | {b['service'][:20]}", callback_data=f"admin_view:{b['id']}")] for b in tom_b]
            rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
            await call.message.answer(f"🔜 Завтра ({tom.day} {MONTHS[tom.month]}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif action=="admin_schedule":
        await call.message.answer("🗓 Управление расписанием:", reply_markup=schedule_main_kb())
    elif action=="admin_services":
        await call.message.answer("💅 Управление услугами:", reply_markup=admin_services_kb())
    elif action=="admin_stats":
        now=now_tallinn()
        try: s=get_stats_month(now.year, now.month)
        except Exception as e: await call.message.answer(f"❌ Ошибка: {e}"); await call.answer(); return
        text=f"📊 {MONTHS[now.month]} {now.year}\n\n"
        text+=f"📋 Записей: {s['bookings']}\n"
        text+=f"🗑 Отмен: {s['cancelled']}\n"
        text+=f"⭐ Отзывов: {s['reviews']}"
        if s["avg_rating"]: text+=f" | {s['avg_rating']} ⭐"
        text+=f"\n💰 Выручка: {s['revenue']}€"
        text+=f"\n⭐ Чаевые: {s.get('tips_count',0)} раз | {s.get('tips_stars',0)} ⭐ ≈ {s.get('tips_eur',0)}€"
        if s["by_service"]:
            text+="\n\n💅 По услугам:\n"
            for svc_name,cnt in s["by_service"]:
                text+=f"  • {svc_name}: {cnt} шт.\n"
        prev_month = now.month - 1 if now.month > 1 else 12
        prev_year = now.year if now.month > 1 else now.year - 1
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"◀️ {MONTHS[prev_month]}", callback_data=f"stats_month:{prev_year}:{prev_month}")],
            [InlineKeyboardButton(text="📊 За всё время", callback_data="stats_all")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]])
        await call.message.answer(text, reply_markup=kb)
    elif action=="admin_reviews":
        con=db_connect()
        rows=con.execute("SELECT id,rating,text,service,created_at,username FROM reviews ORDER BY id DESC LIMIT 30").fetchall()
        con.close()
        if not rows:
            await call.message.answer("⭐ Отзывов пока нет.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✍️ Добавить отзыв", callback_data="admin_rev_add")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]]))
            await call.answer(); return
        kb_rows=[]
        for row in rows:
            rev_id,rating,rv,svc_name,created_at = row[0],row[1],row[2],row[3],row[4]
            username = row[5] if len(row) > 5 and row[5] else ""
            stars="⭐"*rating
            label = f"@{username}" if username else f"#{rev_id}"
            # Форматируем дату: "10 Марта"
            MONTHS_BTN = {1:"Января",2:"Февраля",3:"Марта",4:"Апреля",5:"Мая",6:"Июня",7:"Июля",8:"Августа",9:"Сентября",10:"Октября",11:"Ноября",12:"Декабря"}
            try:
                from datetime import datetime as _dt
                _d = _dt.strptime(created_at[:10], "%Y-%m-%d")
                date_label = f"{_d.day} {MONTHS_BTN[_d.month]}"
            except: date_label = ""
            btn_label = f"{stars} {label} {date_label}"
            kb_rows.append([InlineKeyboardButton(text=btn_label, callback_data=f"admin_rev_open:{rev_id}")])
        kb_rows.append([InlineKeyboardButton(text="✍️ Добавить отзыв", callback_data="admin_rev_add")])
        kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
        await call.message.answer("⭐ Отзывы клиентов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    elif action.startswith("admin_rev_del:"):
        rev_id=int(action.split(":")[1])
        con=db_connect(); con.execute("DELETE FROM reviews WHERE id=?", (rev_id,)); con.commit(); con.close()
        await call.message.answer("🗑 Отзыв удалён.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К отзывам", callback_data="admin_reviews")]]))
    elif action.startswith("admin_rev_edit:"):
        rev_id=int(action.split(":")[1])
        await state.update_data(edit_rev_id=rev_id)
        await state.set_state(AdminReview.text)
        await call.message.answer("✏️ Введите новый текст отзыва (или /skip чтобы оставить без текста):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_reviews")]]))
    elif action.startswith("admin_rev_open:"):
        rev_id=int(action.split(":")[1])
        con=db_connect()
        row=con.execute("SELECT id,rating,text,service,created_at,username FROM reviews WHERE id=?", (rev_id,)).fetchone()
        con.close()
        if not row:
            await call.answer("Отзыв не найден", show_alert=True); return
        _,rating,rv,svc_name,created_at,username = row
        stars="⭐"*rating
        uname = f"@{username}" if username else "Аноним"
        date_str = created_at[:10] if created_at else ""
        text = f"{stars}\n👤 {uname}\n💅 {svc_name}\n📅 {date_str}"
        if rv: text += f"\n💬 {rv}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"admin_rev_edit:{rev_id}"),
             InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_rev_del:{rev_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_reviews")]])
        await call.message.answer(text, reply_markup=kb)
    elif action=="admin_rev_add":
        await state.set_state(AdminReview.add_text)
        await call.message.answer("✍️ Введите текст отзыва от мастера:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_reviews")]]))
    elif action=="admin_masters":
        await call.message.answer("💅 Управление мастерами:", reply_markup=admin_friends_kb())
    elif action=="admin_ban_menu":
        banned = get_banned_users()
        kb_rows = [[InlineKeyboardButton(text="🚫 Забанить по @username", callback_data="ban_start")]]
        for b in banned[:10]:
            uname = f"@{b['username']}" if b['username'] else f"id:{b['user_id']}"
            kb_rows.append([InlineKeyboardButton(text=f"🔓 Разбанить {uname}", callback_data=f"unban:{b['user_id']}")])
        kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
        await call.message.answer(
            f"🚫 Бан-лист\n\nЗабанено: {len(banned)} чел." + ("\n\nНет забаненных." if not banned else ""),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    elif action=="admin_vouchers":
        vouchers = get_all_vouchers()
        if not vouchers:
            await call.message.answer("🎁 Бонусов пока нет.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]]))
        else:
            active = [v for v in vouchers if not v["used"]]
            used = [v for v in vouchers if v["used"]]
            text = f"🎁 Бонусы\n\n✅ Активных: {len(active)} | ❌ Использованных: {len(used)}\n\n"
            for v in vouchers[:20]:
                status = f"✅ использован {v['used_at']}" if v["used"] else "🟢 активен"
                text += f"`{v['code']}` — -{v['discount_pct']}% | {status}\n"
            await call.message.answer(text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]]))
    elif action=="admin_broadcast":
        broadcasts = get_broadcasts()
        users_count = get_users_count()
        kb_rows = [
            [InlineKeyboardButton(text="✉️ Создать рассылку", callback_data="broadcast_start")],
        ]
        if broadcasts:
            kb_rows.append([InlineKeyboardButton(text="📋 История рассылок", callback_data="broadcast_history")])
        kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
        await call.message.answer(f"📣 Рассылка\n\nВсего пользователей: {users_count}\n\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    elif action=="admin_back":
        await call.message.answer(f"🔐 Панель администратора\nВсего броней: {len(get_all_bookings())}", reply_markup=admin_panel_kb())
    elif action.startswith("admin_del:"):
        bid=int(action.split(":")[1]); b=get_booking(bid)
        if b:
            log_cancellation(b,"master"); remove_booking(bid)
            try: await bot.send_message(b["user_id"], f"❌ Ваша бронь отменена мастером.\n\n{format_booking(b)}")
            except Exception as _e: print(f"[WARN] {_e}")
            await call.message.answer("✅ Бронь отменена.")
        else: await call.message.answer("Бронь не найдена.")
    await call.answer()

# ── Friends admin handlers ────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("friend_manage:"))
async def friend_manage(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    fid = int(call.data.split(":")[1])
    f = get_friend(fid)
    if not f: await call.answer("Не найдено", show_alert=True); return
    text = f"👤 {f['name']} — {f['specialty']}\n\n{f['description']}\n\n🔗 {f['link']}"
    await call.message.answer_photo(photo=f['photo_id'], caption=text, reply_markup=friend_manage_kb(fid))
    await call.answer()

@dp.callback_query(F.data.startswith("friend_delete:"))
async def friend_delete(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    fid = int(call.data.split(":")[1])
    delete_friend(fid)
    await call.message.answer("✅ Друг удалён.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К списку", callback_data="admin_masters")]]))
    await call.answer()

@dp.callback_query(F.data == "friend_add")
async def friend_add_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    await state.set_state(AddFriend.photo)
    await call.message.answer("📸 Отправьте фото мастера:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_masters")]]))
    await call.answer()

@dp.message(AddFriend.photo, F.photo)
async def friend_add_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await state.set_state(AddFriend.name)
    await message.answer("✍️ Введите имя мастера (например: Анна):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_masters")]]))

@dp.message(AddFriend.name)
async def friend_add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddFriend.specialty)
    await message.answer("💼 Введите специализацию (например: мастер по губам):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_masters")]]))

@dp.message(AddFriend.specialty)
async def friend_add_specialty(message: types.Message, state: FSMContext):
    await state.update_data(specialty=message.text.strip())
    await state.set_state(AddFriend.description)
    await message.answer("📝 Введите описание мастера (кратко о работах, стиле):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_masters")]]))

@dp.message(AddFriend.description)
async def friend_add_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AddFriend.link)
    await message.answer("🔗 Введите ссылку на бота или сайт (например: https://t.me/username):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_masters")]]))

@dp.message(AddFriend.link)
async def friend_add_link(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.update_data(link=message.text.strip())
    data = await state.get_data()
    # Показываем предпросмотр
    caption = f"👤 *{data['name']}* — {data['specialty']}\n\n{data['description']}\n\n🔗 {data['link']}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="friend_confirm"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="admin_masters")]])
    await message.answer_photo(photo=data['photo_id'], caption=caption, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data == "friend_confirm")
async def friend_confirm(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    data = await state.get_data()
    await state.clear()
    add_friend(data['name'], data['specialty'], data['description'], data['link'], data['photo_id'])
    await call.message.answer(f"✅ Мастер {data['name']} добавлен! 👯",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К списку", callback_data="admin_masters")]]))
    await call.answer()
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("svc_manage:"))
async def svc_manage(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    sid=int(call.data.split(":")[1]); s=get_service_by_id(sid)
    if not s: await call.answer("Услуга не найдена.", show_alert=True); return
    status="✅ Активна" if s["active"] else "❌ Скрыта"
    desc_line = f"\n📝 {s['description']}" if s.get("description") else ""
    await call.message.answer(f"💅 {s['name']}\n💰 {s['price']} | ⏱ {s['duration']}{desc_line}\nСтатус: {status}", reply_markup=svc_manage_kb(sid,s["active"])); await call.answer()

@dp.callback_query(F.data.startswith("svc_hide:"))
async def svc_hide(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    sid=int(call.data.split(":")[1]); deactivate_service_db(sid)
    await call.answer("❌ Услуга скрыта"); await call.message.edit_reply_markup(reply_markup=svc_manage_kb(sid,0))

@dp.callback_query(F.data.startswith("svc_show:"))
async def svc_show(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    sid=int(call.data.split(":")[1]); restore_service_db(sid)
    await call.answer("✅ Услуга видна"); await call.message.edit_reply_markup(reply_markup=svc_manage_kb(sid,1))

@dp.callback_query(F.data.startswith("svc_edit:"))
async def svc_edit_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    _,field,sid_str=call.data.split(":"); sid=int(sid_str); s=get_service_by_id(sid)
    if not s: await call.answer("Услуга не найдена.", show_alert=True); return
    await state.update_data(edit_svc_id=sid, edit_svc_field=field)
    cancel_kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_services")]])
    if field=="img":
        await call.message.answer("🖼 Отправьте новую картинку для услуги фото-сообщением:", reply_markup=cancel_kb)
        await state.set_state(EditService.img)
    else:
        cur_desc = s.get("description","") or "нет"
        prompts={"name":f"Текущее: {s['name']}\n\nВведите новое название:","price":f"Текущая: {s['price']}\n\nВведите цену (пример: 20€):","duration":f"Текущая: {s['duration']}\n\nВведите длительность (пример: 45 мин):","description":f"Текущее описание: {cur_desc}\n\nВведите новое описание услуги (или напишите - чтобы убрать):"}
        await call.message.answer(prompts.get(field,"Введите новое значение:"), reply_markup=cancel_kb)
        await state.set_state(EditService.value)
    await call.answer()

@dp.message(EditService.value)
async def svc_edit_save(message: types.Message, state: FSMContext):
    data=await state.get_data(); sid=data["edit_svc_id"]; field=data["edit_svc_field"]; value=message.text.strip()
    if field=="price" and "€" not in value: value=value+"€"
    if field=="description" and value=="-": value=""
    if field=="duration":
        if "мин" not in value.lower(): value=value+" мин"
        m=re.search(r"(\d+)",value); update_service_db(sid,"duration_min",int(m.group(1)) if m else 60)
    update_service_db(sid,field,value); s=get_service_by_id(sid); await state.clear()
    await message.answer(f"✅ Обновлено!\n\n💅 {s['name']}\n💰 {s['price']} | ⏱ {s['duration']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К услугам", callback_data="admin_services")]]))

@dp.message(EditService.img)
async def svc_edit_img_save(message: types.Message, state: FSMContext):
    if not message.photo: await message.answer("⚠️ Отправьте именно фотографию."); return
    data=await state.get_data(); sid=data["edit_svc_id"]; s=get_service_by_id(sid)
    os.makedirs("images",exist_ok=True); file_path=f"images/svc_{sid}.jpg"
    photo=message.photo[-1]; file_info=await bot.get_file(photo.file_id)
    await bot.download_file(file_info.file_path, destination=file_path)
    update_service_db(sid,"img",file_path); await state.clear()
    await message.answer(f"✅ Картинка обновлена!\n\n💅 {s['name']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К услугам", callback_data="admin_services")]]))

@dp.callback_query(F.data.startswith("svc_delete_confirm:"))
async def svc_delete_confirm(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    sid=int(call.data.split(":")[1]); s=get_service_by_id(sid)
    if not s: await call.answer("Услуга не найдена.", show_alert=True); return
    kb=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"svc_delete_yes:{sid}"),
        InlineKeyboardButton(text="❌ Отмена",       callback_data=f"svc_manage:{sid}")]])
    await call.message.answer(
        f"❗️ Удалить услугу «{s['name']}»?\n\nВсе существующие брони сохранятся.",
        reply_markup=kb); await call.answer()

@dp.callback_query(F.data.startswith("svc_delete_yes:"))
async def svc_delete_yes(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    sid=int(call.data.split(":")[1]); s=get_service_by_id(sid)
    if not s: await call.answer("Услуга не найдена.", show_alert=True); return
    name=s["name"]; delete_service_db(sid)
    await call.message.answer(
        f"✅ Услуга «{name}» удалена.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ К услугам", callback_data="admin_services")]])); await call.answer()

@dp.callback_query(F.data == "svc_add")
async def svc_add_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    await call.message.answer("➕ Новая услуга\n\nВведите название:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_services")]]))
    await state.set_state(AddService.name); await call.answer()

@dp.message(AddService.name)
async def svc_add_name(message: types.Message, state: FSMContext):
    await state.update_data(new_name=message.text.strip()); await message.answer("Введите цену (пример: 25€):"); await state.set_state(AddService.price)

@dp.message(AddService.price)
async def svc_add_price(message: types.Message, state: FSMContext):
    await state.update_data(new_price=message.text.strip()); await message.answer("Введите длительность (пример: 60 мин):"); await state.set_state(AddService.duration)

@dp.message(AddService.duration)
async def svc_add_duration(message: types.Message, state: FSMContext):
    dur=message.text.strip(); m=re.search(r"(\d+)",dur)
    await state.update_data(new_duration=dur,new_dur_min=int(m.group(1)) if m else 60)
    await message.answer("Отправьте картинку для услуги фото-сообщением:\n(или напишите skip)"); await state.set_state(AddService.img)

@dp.message(AddService.img)
async def svc_add_img(message: types.Message, state: FSMContext):
    data=await state.get_data()
    if message.photo:
        os.makedirs("images",exist_ok=True)
        photo=message.photo[-1]; file_info=await bot.get_file(photo.file_id)
        img=f"images/new_{photo.file_id[-8:]}.jpg"
        await bot.download_file(file_info.file_path, destination=img)
    elif message.text and message.text.lower()=="skip":
        img="images/1.jpg"
    else:
        await message.answer("Отправьте фото или напишите skip:"); return
    ok=add_service_db(data["new_name"],data["new_price"],data["new_duration"],data["new_dur_min"],img)
    await state.clear()
    if ok: await message.answer(f"✅ Услуга добавлена!\n\n💅 {data['new_name']}\n💰 {data['new_price']} | ⏱ {data['new_duration']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К услугам", callback_data="admin_services")]]))
    else: await message.answer("❌ Ошибка — услуга с таким названием уже существует.")

@dp.callback_query(F.data.startswith("sched_"))
async def schedule_actions(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    action=call.data
    if action=="sched_block_day": await call.message.answer("Выберите месяц:", reply_markup=schedule_months_kb("bday"))
    elif action=="sched_unblock_day": await call.message.answer("Выберите месяц:", reply_markup=schedule_months_kb("uday"))
    elif action=="sched_block_slots": await call.message.answer("Выберите месяц:", reply_markup=schedule_months_kb("bslot"))
    elif action=="sched_unblock_slots": await call.message.answer("Выберите месяц:", reply_markup=schedule_months_kb("uslot"))
    elif action=="sched_unblock_all_confirm":
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Да", callback_data="sched_unblock_all_yes"),InlineKeyboardButton(text="❌ Отмена", callback_data="admin_schedule")]])
        await call.message.answer("❗️ Разблокировать все дни и часы?", reply_markup=kb)
    elif action=="sched_unblock_all_yes":
        con=db_connect(); con.execute("DELETE FROM schedule"); con.commit(); con.close()
        await call.message.answer("✅ Всё разблокировано.", reply_markup=schedule_main_kb())
    elif action=="sched_show":
        blocked=get_blocked_days(); slots=get_all_blocked_slots()
        text="📋 Текущие ограничения:\n\n"
        if blocked: text+="Заблокированные дни:\n"+"".join(f"  🚫 {d}\n" for d in sorted(blocked))+"\n"
        if slots: text+="Заблокированные часы:\n"+"".join(f"  📅 {d}: {', '.join(sl)}\n" for d,sl in sorted(slots.items()))
        if not blocked and not slots: text+="Ограничений нет."
        await call.message.answer(text, reply_markup=schedule_main_kb())
    await call.answer()

@dp.callback_query(F.data.startswith("sm_"))
async def schedule_month_pick(call: types.CallbackQuery):
    _,rest=call.data.split("_",1); act,mon=rest.split(":")
    await call.message.answer(f"Выберите день ({MONTHS[int(mon)]}):", reply_markup=schedule_days_kb(int(mon),act)); await call.answer()

@dp.callback_query(F.data.startswith("sd_"))
async def schedule_day_pick(call: types.CallbackQuery):
    _,rest=call.data.split("_",1); act,mon,day=rest.split(":")
    month,day=int(mon),int(day); key=date_key(now_tallinn().year,month,day)
    if act=="bday":
        yr=now_tallinn().year
        if any(b["year"]==yr and b["month"]==month and b["day"]==day for b in get_all_bookings()):
            await call.answer(f"⚠️ На {day} {MONTHS[month]} есть брони — сначала отмените их", show_alert=True); return
        block_day(key); await call.answer(f"🚫 {day} {MONTHS[month]} заблокирован")
        await call.message.edit_reply_markup(reply_markup=schedule_days_kb(month,act))
    elif act=="uday":
        unblock_day(key); await call.answer(f"✅ {day} {MONTHS[month]} разблокирован")
        await call.message.edit_reply_markup(reply_markup=schedule_days_kb(month,act))
    elif act in ("bslot","uslot"):
        await call.message.answer(f"Выберите часы для {day} {MONTHS[month]}:", reply_markup=schedule_slots_kb(month,day,act)); await call.answer()

@dp.callback_query(F.data.startswith("ss_"))
async def schedule_slot_pick(call: types.CallbackQuery):
    _,rest=call.data.split("_",1); parts=rest.split(":")
    act,mon,day,raw=parts[0],parts[1],parts[2],parts[3]
    month,day=int(mon),int(day); time_str=raw[:2]+":"+raw[2:]
    key=date_key(now_tallinn().year,month,day)
    if act=="bslot":
        yr=now_tallinn().year
        if any(b["year"]==yr and b["month"]==month and b["day"]==day and b["time"]==time_str for b in get_all_bookings()):
            await call.answer(f"⚠️ На {time_str} есть бронь", show_alert=True); return
        block_slot(key,time_str); await call.answer(f"🔒 {time_str} заблокировано")
    elif act=="uslot":
        unblock_slot(key,time_str); await call.answer(f"🔓 {time_str} разблокировано")
    await call.message.edit_reply_markup(reply_markup=schedule_slots_kb(month,day,act))

@dp.callback_query(F.data == "noop")
async def noop_cb(call: types.CallbackQuery): await call.answer()

@dp.callback_query(F.data.startswith("reschedule:"))
async def reschedule_start(call: types.CallbackQuery, state: FSMContext):
    bid=int(call.data.split(":")[1]); b=get_booking(bid)
    if not b or b["user_id"]!=call.from_user.id: await call.answer("Бронь не найдена.", show_alert=True); return
    svc=get_service(b["service"]); dur=duration_minutes(svc); year=now_tallinn().year
    await state.update_data(reschedule_bid=bid,reschedule_service=b["service"],
        reschedule_old_date=f"{b['day']} {MONTHS_GEN[b['month']]}",reschedule_old_time=b["time"],year=year)
    await call.message.answer("📅 Выберите новую дату — месяц:", reply_markup=months_kb(year))
    await state.set_state(Reschedule.month); await call.answer()

@dp.callback_query(F.data.startswith("month:"), Reschedule.month)
async def reschedule_month(call: types.CallbackQuery, state: FSMContext):
    month=int(call.data.split(":")[1]); data=await state.get_data(); year=data.get("year",now_tallinn().year)
    await state.update_data(month=month); svc=get_service(data.get("reschedule_service","")); dur=duration_minutes(svc)
    await call.message.answer(f"Выберите день ({MONTHS[month]}):", reply_markup=days_kb(year,month,dur))
    await state.set_state(Reschedule.day); await call.answer()

@dp.callback_query(F.data.startswith("day:"), Reschedule.day)
async def reschedule_day(call: types.CallbackQuery, state: FSMContext):
    day=int(call.data.split(":")[1]); data=await state.get_data()
    year=data.get("year",now_tallinn().year); month=data.get("month"); bid=data.get("reschedule_bid")
    await state.update_data(day=day); svc=get_service(data.get("reschedule_service","")); dur=duration_minutes(svc)
    await call.message.answer(f"✅ {day} {MONTHS_GEN[month]}\n\nВыберите время:",
        reply_markup=time_kb(year,month,day,exclude_bid=bid,new_dur_min=dur))
    await state.set_state(Reschedule.time); await call.answer()

@dp.callback_query(F.data.startswith("t_"), Reschedule.time)
async def reschedule_time(call: types.CallbackQuery, state: FSMContext):
    raw=call.data[2:]; new_time=raw[:2]+":"+raw[2:]; data=await state.get_data()
    bid=data["reschedule_bid"]; b=get_booking(bid)
    if not b: await call.answer("Бронь не найдена.", show_alert=True); return
    year=data.get("year",now_tallinn().year); month=data.get("month"); day=data.get("day")
    old_date=f"{b['year']}-{b['month']:02d}-{b['day']:02d}"; new_date=f"{year}-{month:02d}-{day:02d}"
    log_transfer(bid,b["user_id"],b["service"],old_date,b["time"],new_date,new_time)
    update_booking_field(bid,"year",year); update_booking_field(bid,"month",month)
    update_booking_field(bid,"day",day); update_booking_field(bid,"time",new_time)
    b_new=get_booking(bid)
    await call.message.answer(f"✅ Бронь перенесена!\n\n{format_booking(b_new)}", reply_markup=booking_actions_kb(bid, b_new))
    for _aid in ADMIN_IDS:
        try: await bot.send_message(_aid, f"🔄 Перенос брони!\n\nБыло: {data['reschedule_old_date']} {data['reschedule_old_time']}\nСтало: {day} {MONTHS_GEN[month]} {new_time}\n\n{format_booking(b_new)}")
        except Exception as _e: print(f"[WARN] {_e}")
    await state.clear(); await call.answer()

def tip_amounts_kb(bid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 ⭐", callback_data=f"tip_send:{bid}:1"),
         InlineKeyboardButton(text="100 ⭐", callback_data=f"tip_send:{bid}:100")],
        [InlineKeyboardButton(text="300 ⭐", callback_data=f"tip_send:{bid}:300"),
         InlineKeyboardButton(text="500 ⭐", callback_data=f"tip_send:{bid}:500")],
        [InlineKeyboardButton(text="1000 ⭐", callback_data=f"tip_send:{bid}:1000"),
         InlineKeyboardButton(text="✏️ Своя сумма", callback_data=f"tip_custom:{bid}")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="tip_close")]])

def review_rating_kb(bid):
    stars=["⭐","⭐⭐","⭐⭐⭐","⭐⭐⭐⭐","⭐⭐⭐⭐⭐"]
    rows=[[InlineKeyboardButton(text=s, callback_data=f"rev_rating:{bid}:{i+1}")] for i,s in enumerate(stars)]
    rows.append([InlineKeyboardButton(text="💝 Чаевые мастеру", callback_data=f"tip_open:{bid}")])
    rows.append([InlineKeyboardButton(text="❌ Пропустить", callback_data="rev_skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.callback_query(F.data.startswith("rev_rating:"))
async def review_rating(call: types.CallbackQuery, state: FSMContext):
    _,bid_str,rating_str=call.data.split(":"); bid=int(bid_str); rating=int(rating_str)
    b=get_booking(bid)
    if not b:
        con_cb=db_connect(); cb_row=con_cb.execute("SELECT service FROM completed_bookings WHERE id=?", (bid,)).fetchone(); con_cb.close()
        svc=cb_row[0] if cb_row else ""
    else:
        svc=b["service"]
    username = call.from_user.username or call.from_user.first_name or "Аноним"
    review_id=add_review(bid,call.from_user.id,svc,rating,"",username)
    await state.update_data(review_bid=bid,review_rating=rating,review_service=svc,review_id=review_id)
    stars="⭐"*rating
    for _aid in ADMIN_IDS:
        try: await bot.send_message(_aid, f"⭐ Новый отзыв!\n\n💅 {svc}\n{stars}")
        except Exception as _e: print(f"[WARN] {_e}")
    await call.message.answer(f"Вы поставили {stars}\n\nНапишите комментарий (или нажмите «Пропустить»):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Пропустить", callback_data="rev_skip")]]))
    await state.set_state(Review.text); await call.answer()

@dp.callback_query(F.data == "rev_skip")
async def review_skip(call: types.CallbackQuery, state: FSMContext):
    await state.clear(); await call.message.answer("Спасибо! Ждём вас снова 💆"); await call.answer()

@dp.message(Review.text)
async def review_text(message: types.Message, state: FSMContext):
    data=await state.get_data(); text=message.text.strip() if message.text and message.text!="/skip" else ""
    review_id=data.get("review_id")
    if review_id and text:
        con=db_connect(); con.execute("UPDATE reviews SET text=? WHERE id=?", (text,review_id)); con.commit(); con.close()
    stars="⭐"*data["review_rating"]
    await message.answer("🙏 Спасибо за отзыв! Ждём вас снова 💆")
    if text:
        for _aid in ADMIN_IDS:
            try: await bot.send_message(_aid, f"💬 Комментарий к отзыву:\n\n💅 {data['review_service']}\n{stars}\n{text}")
            except Exception as _e: print(f"[WARN] {_e}")
    await state.clear()

@dp.message(AdminReview.text)
async def admin_rev_edit_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    rev_id = data.get("edit_rev_id")
    new_text = "" if (not message.text or message.text == "/skip") else message.text.strip()
    con = db_connect()
    con.execute("UPDATE reviews SET text=? WHERE id=?", (new_text, rev_id))
    con.commit(); con.close()
    await state.clear()
    await message.answer("✅ Отзыв обновлён!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К отзывам", callback_data="admin_reviews")]]))

@dp.message(AdminReview.add_text)
async def admin_rev_add_text(message: types.Message, state: FSMContext):
    await state.update_data(new_rev_text=message.text.strip() if message.text else "")
    await state.set_state(AdminReview.add_rating)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=s, callback_data=f"admin_rev_rating:{i+1}")]
        for i, s in enumerate(["⭐","⭐⭐","⭐⭐⭐","⭐⭐⭐⭐","⭐⭐⭐⭐⭐"])])
    await message.answer("Выберите рейтинг:", reply_markup=kb)

@dp.callback_query(F.data.startswith("admin_rev_rating:"), AdminReview.add_rating)
async def admin_rev_add_rating(call: types.CallbackQuery, state: FSMContext):
    rating = int(call.data.split(":")[1])
    await state.update_data(new_rev_rating=rating)
    await state.set_state(AdminReview.add_service)
    services = get_all_services_db()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=s["name"], callback_data=f"admin_rev_svc:{s['name']}")]
        for s in services])
    await call.message.answer("Выберите услугу для отзыва:", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("admin_rev_svc:"), AdminReview.add_service)
async def admin_rev_add_service(call: types.CallbackQuery, state: FSMContext):
    svc_name = call.data.split(":", 1)[1]
    data = await state.get_data()
    con = db_connect()
    con.execute(
        "INSERT INTO reviews (booking_id, user_id, service, rating, text, created_at) VALUES (?,?,?,?,?,?)",
        (0, 0, svc_name, data["new_rev_rating"], data.get("new_rev_text", ""), now_tallinn().strftime("%Y-%m-%d %H:%M")))
    con.commit(); con.close()
    await state.clear()
    await call.message.answer("✅ Отзыв добавлен!", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К отзывам", callback_data="admin_reviews")]]))
    await call.answer()

# ── Broadcast handlers ────────────────────────────────────────────────────────
@dp.callback_query(F.data == "broadcast_history")
async def broadcast_history(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    broadcasts = get_broadcasts()
    if not broadcasts:
        await call.answer("История пуста", show_alert=True); return
    text = "📋 История рассылок (последние 20):\n\n"
    for b in broadcasts:
        preview = b["text"][:60] + ("..." if len(b["text"]) > 60 else "")
        btn_info = f"\n🔗 Кнопка: {b['btn_text']}" if b["btn_text"] else ""
        text += f"📅 {b['created_at']} | 👥 отправлено: {b['sent_count']}\n📝 {preview}{btn_info}\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_broadcast")]])
    await call.message.answer(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "broadcast_start")
async def broadcast_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast")]])
    await call.message.answer(
        "✉️ Создание рассылки\n\nВведите текст сообщения:\n\n💡 Можно использовать эмодзи и переносы строк",
        reply_markup=cancel_kb)
    await state.set_state(Broadcast.text)
    await call.answer()

@dp.message(Broadcast.text)
async def broadcast_get_text(message: types.Message, state: FSMContext):
    await state.update_data(broadcast_text=message.text)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Без кнопки", callback_data="broadcast_no_btn")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast")]])
    await message.answer(
        "🔘 Введите текст кнопки (например: 💅 Записаться)\n\nИли нажмите «Без кнопки»:",
        reply_markup=cancel_kb)
    await state.set_state(Broadcast.btn_text)

@dp.callback_query(F.data == "broadcast_no_btn", Broadcast.btn_text)
async def broadcast_no_btn(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(broadcast_btn_text="", broadcast_btn_url="")
    data = await state.get_data()
    await _broadcast_preview(call.message, state, data)
    await call.answer()

@dp.message(Broadcast.btn_text)
async def broadcast_get_btn_text(message: types.Message, state: FSMContext):
    await state.update_data(broadcast_btn_text=message.text.strip())
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📝 Кнопка «Записаться»", callback_data="broadcast_btn_booking")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcast")]])
    await message.answer(
        "🔗 Введите ссылку для кнопки (например: https://t.me/username)\n\nИли нажмите «Записаться» — откроет меню бота:",
        reply_markup=cancel_kb)
    await state.set_state(Broadcast.btn_url)

@dp.callback_query(F.data == "broadcast_btn_booking", Broadcast.btn_url)
async def broadcast_btn_booking(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(broadcast_btn_url="booking")
    data = await state.get_data()
    await _broadcast_preview(call.message, state, data)
    await call.answer()

@dp.message(Broadcast.btn_url)
async def broadcast_get_btn_url(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("⚠️ Ссылка должна начинаться с https://\n\nПопробуйте снова:"); return
    await state.update_data(broadcast_btn_url=url)
    data = await state.get_data()
    await _broadcast_preview(message, state, data)

async def _broadcast_preview(message, state, data):
    text = data["broadcast_text"]
    btn_text = data.get("broadcast_btn_text", "")
    btn_url = data.get("broadcast_btn_url", "")
    users_count = get_users_count()
    preview = f"👁 Предпросмотр рассылки:\n\n{text}"
    if btn_text:
        url_display = "→ откроет меню записи" if btn_url == "booking" else btn_url
        preview += f"\n\n🔘 Кнопка: [{btn_text}] {url_display}"
    preview += f"\n\n👥 Будет отправлено: {users_count} пользователям"
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast_confirm"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")]])
    await message.answer(preview, reply_markup=confirm_kb)
    await state.set_state(Broadcast.confirm)

@dp.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer("❌ Рассылка отменена.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_broadcast")]]))
    await call.answer()

@dp.callback_query(F.data == "broadcast_confirm", Broadcast.confirm)
async def broadcast_confirm(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    data = await state.get_data()
    await state.clear()
    text = data.get("broadcast_text") or ""
    btn_text = data.get("broadcast_btn_text") or ""
    btn_url = data.get("broadcast_btn_url") or ""
    user_ids = get_all_user_ids()
    sent = 0; failed = 0
    progress_msg = await call.message.answer(f"📤 Отправляю... 0/{len(user_ids)}")
    for i, uid in enumerate(user_ids):
        try:
            if btn_text and btn_url:
                if btn_url == "booking":
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, callback_data="main_menu")]])
                else:
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=btn_url)]])
                await bot.send_message(uid, text, reply_markup=kb)
            else:
                await bot.send_message(uid, text)
            sent += 1
        except: failed += 1
        if (i+1) % 10 == 0:
            try: await progress_msg.edit_text(f"📤 Отправляю... {i+1}/{len(user_ids)}")
            except Exception as _e: print(f"[WARN] {_e}")
        await asyncio.sleep(0.05)
    save_broadcast(text, btn_text, btn_url, sent)
    await progress_msg.edit_text(f"✅ Рассылка завершена!\n\n✉️ Отправлено: {sent}\n❌ Не доставлено: {failed}")
    await call.answer()
# ─────────────────────────────────────────────────────────────────────────────

# ── Ban handlers ──────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "admin_ban_menu")
# ── Stats handlers ────────────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("stats_month:"))
async def stats_month_handler(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    _, year_str, month_str = call.data.split(":")
    year, month = int(year_str), int(month_str)
    now = now_tallinn()
    try: s = get_stats_month(year, month)
    except Exception as e: await call.answer(f"Ошибка: {e}", show_alert=True); return
    text = f"📊 {MONTHS[month]} {year}\n\n"
    text += f"📋 Записей: {s['bookings']}\n"
    text += f"🗑 Отмен: {s['cancelled']}\n"
    text += f"⭐ Отзывов: {s['reviews']}"
    if s["avg_rating"]: text += f" | {s['avg_rating']} ⭐"
    text += f"\n💰 Выручка: {s['revenue']}€"
    text += f"\n⭐ Чаевые: {s.get('tips_count',0)} раз | {s.get('tips_stars',0)} ⭐ ≈ {s.get('tips_eur',0.0)}€"
    if s["by_service"]:
        text += "\n\n💅 По услугам:\n"
        for svc_name, cnt in s["by_service"]:
            text += f"  • {svc_name}: {cnt} шт.\n"
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    kb_rows = []
    if not (next_year == now.year and next_month > now.month):
        kb_rows.append([InlineKeyboardButton(text=f"{MONTHS[next_month]} ▶️", callback_data=f"stats_month:{next_year}:{next_month}")])
    kb_rows.append([InlineKeyboardButton(text=f"◀️ {MONTHS[prev_month]}", callback_data=f"stats_month:{prev_year}:{prev_month}")])
    kb_rows.append([InlineKeyboardButton(text="📊 За всё время", callback_data="stats_all")])
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

@dp.callback_query(F.data == "stats_all")
async def stats_all_handler(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    try: s = get_stats_all()
    except Exception as e: await call.answer(f"Ошибка: {e}", show_alert=True); return
    text = "📊 За всё время\n\n"
    text += f"📋 Всего записей: {s['total_bookings']}\n"
    text += f"🗑 Отмен: {s['total_cancelled']} (клиент: {s['cancelled_by_client']}, мастер: {s['cancelled_by_master']})\n"
    text += f"🔄 Переносов: {s['total_transfers']}\n"
    text += f"⭐ Отзывов: {s['total_reviews']}"
    if s["avg_rating"]: text += f" | {s['avg_rating']} ⭐"
    text += f"\n💰 Общая выручка: {s['total_revenue']}€"
    text += f"\n⭐ Чаевые за всё время: {s.get('tips_count',0)} раз | {s.get('tips_stars',0)} ⭐ ≈ {s.get('tips_eur',0.0)}€"
    if s["by_service"]:
        text += "\n\n💅 По услугам:\n"
        for svc_name, cnt in s["by_service"]:
            text += f"  • {svc_name}: {cnt} шт.\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]])
    await call.message.answer(text, reply_markup=kb)
    await call.answer()
# ─────────────────────────────────────────────────────────────────────────────

async def admin_ban_menu(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    banned = get_banned_users()
    text = f"🚫 Бан пользователей\n\nЗабанено: {len(banned)} чел.\n\n"
    kb_rows = [[InlineKeyboardButton(text="🚫 Забанить по @username", callback_data="ban_start")]]
    if banned:
        for b in banned[:10]:
            uname = f"@{b['username']}" if b['username'] else f"id:{b['user_id']}"
            kb_rows.append([InlineKeyboardButton(text=f"🔓 Разбанить {uname}", callback_data=f"unban:{b['user_id']}")])
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
    await call.message.answer(text + ("Нет забаненных." if not banned else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

@dp.callback_query(F.data == "ban_start")
async def ban_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_ban_menu")]])
    await call.message.answer("Введите @username пользователя для бана:", reply_markup=cancel_kb)
    await state.set_state(BanUser.username)
    await call.answer()

@dp.message(BanUser.username)
async def ban_by_username(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    username = message.text.strip().lstrip("@")
    user = find_user_by_username(username)
    if not user:
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_ban_menu")]])
        await message.answer(f"❌ Пользователь @{username} не найден в базе.\n\nВведите другой @username:", reply_markup=cancel_kb); return
    await state.clear()
    ban_user(user["user_id"], user["username"])
    try: await bot.send_message(user["user_id"], "⛔️ Ваш доступ к боту ограничен.")
    except Exception as _e: print(f"[WARN] {_e}")
    await message.answer(
        f"✅ Пользователь @{username} забанен.\nВсе его брони удалены.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К бан-листу", callback_data="admin_ban_menu")]]))

@dp.callback_query(F.data.startswith("unban:"))
async def unban_user_handler(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: await call.answer("⛔️", show_alert=True); return
    user_id = int(call.data.split(":")[1])
    unban_user(user_id)
    try: await bot.send_message(user_id, "✅ Ваш доступ к боту восстановлен!")
    except Exception as _e: print(f"[WARN] {_e}")
    await call.answer("✅ Разбанен")
    await admin_ban_menu(call)
# ─────────────────────────────────────────────────────────────────────────────

async def reminder_loop():
    while True:
        try:
            now=now_tallinn()
            con=db_connect(); rows_all=[_row_to_booking(r) for r in con.execute("SELECT * FROM bookings").fetchall()]; con.close()
            for b in rows_all:
                try: appt=datetime(b["year"],b["month"],b["day"],int(b["time"].split(":")[0]),int(b["time"].split(":")[1]))
                except: continue
                delta=appt-now; total_min=delta.total_seconds()/60
                svc=get_service(b["service"]); dur_str=svc["duration"] if svc else ""
                if not b["reminded_24"] and 1430<=total_min<=1450:
                    try:
                        await bot.send_message(b["user_id"], f"🔔 Напоминание!\n\nЗавтра у вас запись:\n💅 {b['service']} ({dur_str})\n📅 {b['day']} {MONTHS_GEN[b['month']]}\n⏱ {b['time']}\n\n📍 {CONTACTS_SHORT}")
                        con2=db_connect(); con2.execute("UPDATE bookings SET reminded_24=1 WHERE id=?", (b["id"],)); con2.commit(); con2.close()
                    except Exception as _e: print(f"[WARN] {_e}")
                if not b["reminded_2"] and 110<=total_min<=130:
                    try:
                        await bot.send_message(b["user_id"], f"⏰ Через 2 часа ваша запись!\n\n💅 {b['service']} ({dur_str})\n📅 {b['day']} {MONTHS_GEN[b['month']]}\n⏱ {b['time']}\n\n📍 {CONTACTS_SHORT}")
                        con2=db_connect(); con2.execute("UPDATE bookings SET reminded_2=1 WHERE id=?", (b["id"],)); con2.commit(); con2.close()
                    except Exception as _e: print(f"[WARN] {_e}")
                svc_obj=get_service(b["service"]); dur_m=duration_minutes(svc_obj)
                end_appt=appt+timedelta(minutes=dur_m); mins_after=(now-end_appt).total_seconds()/60
                if not b.get("review_sent") and 15<=mins_after<=35:
                    try:
                        await bot.send_message(b["user_id"], f"😊 Как прошёл визит?\n\n💅 {b['service']}\n\nОставьте оценку 👇", reply_markup=review_rating_kb(b["id"]))
                        con2=db_connect(); con2.execute("UPDATE bookings SET review_sent=1 WHERE id=?", (b["id"],)); con2.commit(); con2.close()
                    except Exception as _e: print(f"[WARN] {_e}")
                hours_after = (now - end_appt).total_seconds() / 3600
                if not b.get("rebooking_sent") and 335.5 <= hours_after <= 336.5:
                    try:
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💆 Записаться снова", callback_data="main_menu")]])
                        await bot.send_message(
                            b["user_id"],
                            f"💅 Привет, {b['name']}!\n\nПрошло 2 недели после вашего визита к Сергею 🌸\n\nПора на массаж? Записывайтесь — мы всегда рады вас видеть 💕",
                            reply_markup=kb)
                        con2=db_connect(); con2.execute("UPDATE bookings SET rebooking_sent=1 WHERE id=?", (b["id"],)); con2.commit(); con2.close()
                    except Exception as _e: print(f"[WARN] {_e}")
                # После завершения услуги — выдаём промокоды по рефералу если есть
                if mins_after >= 5:
                    try:
                        con2=db_connect()
                        # Проверяем есть ли реферал для этого пользователя где промокод ещё не выдан
                        ref_row = con2.execute(
                            "SELECT id, referrer_id FROM referrals WHERE referred_id=? AND voucher_sent=0",
                            (b["user_id"],)).fetchone()
                        if ref_row:
                            ref_db_id, referrer_id = ref_row
                            # Выдаём промокод пригласившему
                            ref_code = create_voucher(referrer_id, 30)
                            ref_count = get_referral_count(referrer_id)
                            try:
                                await bot.send_message(referrer_id,
                                    f"🎀 Твой друг завершил первый визит к Сергею!\n\n"
                                    f"Держи промокод на скидку 30%:\n`{ref_code}`\n\n"
                                    f"Всего приглашено: {ref_count} чел. Введи код при следующей записи 💕",
                                    parse_mode="Markdown")
                            except Exception as _e: print(f"[WARN] {_e}")
                            # Выдаём промокод новому клиенту
                            new_code = create_voucher(b["user_id"], 30)
                            try:
                                await bot.send_message(b["user_id"],
                                    f"🎀 Спасибо за первый визит к Сергею!\n\n"
                                    f"Твой промокод на скидку 30%:\n`{new_code}`\n\n"
                                    f"Введи код при следующей записи 💅",
                                    parse_mode="Markdown")
                            except Exception as _e: print(f"[WARN] {_e}")
                            # Отмечаем что промокод выдан
                            con2.execute("UPDATE referrals SET voucher_sent=1 WHERE id=?", (ref_db_id,))
                            con2.commit()
                        # Удаляем завершённую бронь
                        log_completed_booking(b)
                        con2.execute("DELETE FROM bookings WHERE id=?", (b["id"],))
                        con2.commit(); con2.close()
                    except Exception as _e: print(f"[WARN] {_e}")
            if now.hour==8 and now.minute<5:
                today_b=[b for b in rows_all if b["year"]==now.year and b["month"]==now.month and b["day"]==now.day]
                if today_b:
                    text=f"☀️ Доброе утро! Сегодня {now.day} {MONTHS_GEN[now.month]}:\n\n"
                    for b in today_b:
                        svc2=get_service(b["service"]); dur2=svc2["duration"] if svc2 else ""
                        text+=f"⏱ {b['time']} — 💅 {b['service']} (~{dur2})\n👤 {b['name']} 📞 {b['phone']}\n\n"
                    for _aid in ADMIN_IDS:
                        try: await bot.send_message(_aid, text)
                        except Exception as _e: print(f"[WARN] {_e}")
        except Exception as e: print(f"Reminder error: {e}")
        await asyncio.sleep(300)

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        s=get_stats(); now=now_tallinn()
        await message.answer(f"📊 Статистика\n\n📋 Активных: {s['total_active']}\n🗑 Отмен: {s['total_cancelled']}\n🔄 Переносов: {s['total_transfers']}\n⭐ Отзывов: {s['total_reviews']}\n\n💰 За {MONTHS[now.month]}: {s['month_revenue']}€\n💰 Всего: {s['total_revenue']}€")
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("tip"))
async def cmd_tip(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("\u26d4\ufe0f \u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.")
        return
    await message.answer(
        "\U0001f60a \u041a\u0430\u043a \u043f\u0440\u043e\u0448\u0451\u043b \u0432\u0438\u0437\u0438\u0442?\n\n\U0001f485 \u0422\u0435\u0441\u0442\u043e\u0432\u0430\u044f \u0443\u0441\u043b\u0443\u0433\u0430\n\n\u041e\u0441\u0442\u0430\u0432\u044c\u0442\u0435 \u043e\u0446\u0435\u043d\u043a\u0443 \U0001f447",
        reply_markup=review_rating_kb(0)
    )

async def on_startup(bot: Bot):
    asyncio.create_task(reminder_loop())

async def main():
    dp.startup.register(on_startup); print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
