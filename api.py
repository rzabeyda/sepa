import sqlite3, os, calendar, re, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

TZ = ZoneInfo("Europe/Tallinn")
def now_t(): return datetime.now(TZ).replace(tzinfo=None)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE  = os.path.join(BASE_DIR, "bot.db")

load_dotenv(os.path.join(BASE_DIR, ".env"))
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_IDS = {7308147004, 1865333207}  # Ромик, Сергей — как в bot.py
MONTHS_GEN = {1:"Января",2:"Февраля",3:"Марта",4:"Апреля",5:"Мая",6:"Июня",
              7:"Июля",8:"Августа",9:"Сентября",10:"Октября",11:"Ноября",12:"Декабря"}

def notify_admins(text):
    """Шлёт уведомление о новой брони админам напрямую через Telegram Bot API
    (без aiogram) — так как api.py и bot.py это два разных процесса."""
    if not API_TOKEN: return
    url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
    for aid in ADMIN_IDS:
        try:
            data = urllib.parse.urlencode({"chat_id": aid, "text": text}).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5)
        except Exception:
            pass

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def db(): return sqlite3.connect(DB_FILE)

def _ensure_duration_column():
    """На случай, если api.py стартует раньше bot.py — колонка нужна нам самим
    для записи и чтения длительности брони, не полагаемся на миграцию bot.py."""
    con = db()
    cols = [r[1] for r in con.execute("PRAGMA table_info(bookings)").fetchall()]
    if "duration_min" not in cols:
        try: con.execute("ALTER TABLE bookings ADD COLUMN duration_min INTEGER"); con.commit()
        except Exception: pass
    con.close()
_ensure_duration_column()

def get_services():
    con = db()
    rows = con.execute("SELECT name,price,duration,duration_min,img FROM services WHERE active=1 ORDER BY sort_order").fetchall(); con.close()
    return [{"name":r[0],"price":r[1],"duration":r[2],"duration_min":r[3],"img":r[4]} for r in rows]

def get_reviews():
    con = db()
    rows = con.execute("SELECT rating,text,service,created_at,username FROM reviews ORDER BY id DESC LIMIT 20").fetchall(); con.close()
    return [{"rating":r[0],"text":r[1] or "","service":r[2],"date":r[3][:10] if r[3] else "","username":r[4] or "Аноним"} for r in rows]

# Тот же график работы, что и в bot.py (WORK_SCHEDULE) — держим в одном месте
# логику, но значения должны совпадать 1-в-1, иначе сайт и бот будут расходиться.
WORK_SCHEDULE = {
    0: (17, 19),  # Понедельник
    1: (17, 19),  # Вторник
    2: (17, 19),  # Среда
    3: (17, 19),  # Четверг
    4: (17, 19),  # Пятница
    5: (11, 18),  # Суббота
    6: (11, 19),  # Воскресенье
}

REST_MINUTES = 30  # отдых Сергею после каждого массажа — то же значение, что в bot.py

def get_slots(year, month, day, dur_min):
    now = now_t(); key = f"{year}-{month:02d}-{day:02d}"
    weekday = datetime(year, month, day).weekday()
    if weekday not in WORK_SCHEDULE:
        return []
    start_hour, end_hour = WORK_SCHEDULE[weekday]
    con = db()
    if con.execute("SELECT date FROM schedule WHERE type='day' AND date=?", (key,)).fetchone():
        con.close(); return []
    blocked_slots = set(r[0] for r in con.execute("SELECT time FROM schedule WHERE type='slot' AND date=?", (key,)).fetchall())
    bookings = con.execute("SELECT time,service,duration_min FROM bookings WHERE year=? AND month=? AND day=?", (year,month,day)).fetchall()
    con.close()
    booked = []
    for btime, bsvc, bdurmin in bookings:
        if not bdurmin:
            con2 = db(); row = con2.execute("SELECT duration_min FROM services WHERE name=?", (bsvc,)).fetchone(); con2.close()
            bdurmin = row[0] if row else 60
        h,m = int(btime.split(":")[0]), int(btime.split(":")[1])
        booked.append((h*60+m, h*60+m+bdurmin+REST_MINUTES))
    def free(start, dur):
        if start + dur > end_hour*60: return False
        for bs,be in booked:
            if start < be and start+dur > bs: return False
        return f"{start//60:02d}:{start%60:02d}" not in blocked_slots
    candidates = list(range(start_hour*60, end_hour*60+1, 60))
    for _, be in booked:
        if be % 60 == 30 and start_hour*60 <= be <= end_hour*60:
            candidates.append(be)
    if year==now.year and month==now.month and day==now.day:
        candidates = [c for c in candidates if c > now.hour*60+now.minute]
    return [f"{c//60:02d}:{c%60:02d}" for c in sorted(set(c for c in candidates if free(c, dur_min)))]

def get_days(year, month, dur_min):
    now = now_t(); _, dim = calendar.monthrange(year, month); result = []
    for d in range(1, dim+1):
        if year==now.year and month==now.month and d<now.day: continue
        if get_slots(year, month, d, dur_min): result.append(d)
    return result

@app.get("/api/services")
def api_services(): return get_services()

@app.get("/api/reviews")
def api_reviews(): return get_reviews()

@app.get("/api/slots")
def api_slots(year: int, month: int, day: int, dur_min: int = 60):
    return get_slots(year, month, day, dur_min)

@app.get("/api/days")
def api_days(year: int, month: int, dur_min: int = 60):
    return get_days(year, month, dur_min)

class BookingIn(BaseModel):
    user_id: int = 0
    service: str
    year: int; month: int; day: int; time: str
    name: str; phone: str; dur_min: int = 60

@app.post("/api/book")
def api_book(b: BookingIn):
    if b.time not in get_slots(b.year, b.month, b.day, b.dur_min):
        return {"ok": False, "error": "Время уже занято"}
    con = db(); cur = con.execute(
        "INSERT INTO bookings (user_id,service,year,month,day,time,name,phone,duration_min) VALUES (?,?,?,?,?,?,?,?,?)",
        (b.user_id, b.service, b.year, b.month, b.day, b.time, b.name, b.phone, b.dur_min))
    con.commit(); con.close()
    svc = next((s for s in get_services() if s["name"] == b.service), None)
    price = svc["price"] if svc else "?"
    notify_admins(
        "🔔 Новая бронь (с сайта)!\n\n"
        f"💆 {b.service}\n"
        f"⏱ {b.dur_min} мин | 💰 {price}\n"
        f"🕐 {b.time} | {b.day} {MONTHS_GEN.get(b.month,'')}\n"
        f"👤 {b.name} 📞 {b.phone}"
    )
    return {"ok": True, "id": cur.lastrowid}

if os.path.exists(os.path.join(BASE_DIR, "images")):
    app.mount("/images", StaticFiles(directory=os.path.join(BASE_DIR, "images")), name="images")

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(BASE_DIR, "webapp", "index.html"), encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), headers={"Cache-Control": "no-store, must-revalidate"})

def _norm_phone(p):
    return re.sub(r"[^\d+]", "", p or "")

@app.get("/api/user_bookings")
def api_user_bookings(user_id: int = 0, phone: str = ""):
    con = db()
    if phone:
        target = _norm_phone(phone)
        rows = con.execute(
            "SELECT id,service,year,month,day,time,name,phone FROM bookings ORDER BY year,month,day,time"
        ).fetchall()
        con.close()
        rows = [r for r in rows if target and _norm_phone(r[7]) == target]
        return [{"id":r[0],"service":r[1],"year":r[2],"month":r[3],"day":r[4],"time":r[5],"name":r[6],"phone":r[7]} for r in rows]
    rows = con.execute(
        "SELECT id,service,year,month,day,time,name,phone FROM bookings WHERE user_id=? ORDER BY year,month,day,time",
        (user_id,)).fetchall()
    con.close()
    return [{"id":r[0],"service":r[1],"year":r[2],"month":r[3],"day":r[4],"time":r[5],"name":r[6],"phone":r[7]} for r in rows]

class CancelIn(BaseModel):
    id: int
    user_id: int = 0
    phone: str = ""

@app.post("/api/cancel_booking")
def api_cancel_booking(c: CancelIn):
    con = db()
    row = con.execute(
        "SELECT user_id,service,year,month,day,time,name,phone FROM bookings WHERE id=?", (c.id,)
    ).fetchone()
    if not row:
        con.close(); return {"ok": False, "error": "Бронь не найдена"}
    b_user_id, b_service, b_year, b_month, b_day, b_time, b_name, b_phone = row
    authorized = (c.user_id and b_user_id == c.user_id) or (c.phone and _norm_phone(c.phone) == _norm_phone(b_phone))
    if not authorized:
        con.close(); return {"ok": False, "error": "Нет доступа к этой брони"}
    con.execute(
        "INSERT INTO cancelled (user_id,service,year,month,day,time,name,phone,cancelled_by,cancelled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (b_user_id, b_service, b_year, b_month, b_day, b_time, b_name, b_phone, "client_web", now_t().strftime("%Y-%m-%d %H:%M")))
    con.execute("DELETE FROM bookings WHERE id=?", (c.id,))
    con.commit(); con.close()
    notify_admins(
        "🗑 Клиент отменил бронь (с сайта)!\n\n"
        f"💆 {b_service}\n"
        f"🕐 {b_time} | {b_day} {MONTHS_GEN.get(b_month,'')}\n"
        f"👤 {b_name} 📞 {b_phone}"
    )
    return {"ok": True}
