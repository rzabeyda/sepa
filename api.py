import sqlite3, os, calendar
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

TZ = ZoneInfo("Europe/Tallinn")
def now_t(): return datetime.now(TZ).replace(tzinfo=None)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE  = os.path.join(BASE_DIR, "bot.db")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def db(): return sqlite3.connect(DB_FILE)

def get_services():
    con = db()
    rows = con.execute("SELECT name,price,duration,duration_min,img FROM services WHERE active=1 ORDER BY sort_order").fetchall(); con.close()
    return [{"name":r[0],"price":r[1],"duration":r[2],"duration_min":r[3],"img":r[4]} for r in rows]

def get_reviews():
    con = db()
    rows = con.execute("SELECT rating,text,service,created_at,username FROM reviews ORDER BY id DESC LIMIT 20").fetchall(); con.close()
    return [{"rating":r[0],"text":r[1] or "","service":r[2],"date":r[3][:10] if r[3] else "","username":r[4] or "Аноним"} for r in rows]

def get_slots(year, month, day, dur_min):
    now = now_t(); key = f"{year}-{month:02d}-{day:02d}"
    con = db()
    if con.execute("SELECT date FROM schedule WHERE type='day' AND date=?", (key,)).fetchone():
        con.close(); return []
    blocked_slots = set(r[0] for r in con.execute("SELECT time FROM schedule WHERE type='slot' AND date=?", (key,)).fetchall())
    bookings = con.execute("SELECT time,service FROM bookings WHERE year=? AND month=? AND day=?", (year,month,day)).fetchall()
    con.close()
    booked = []
    for btime, bsvc in bookings:
        con2 = db(); row = con2.execute("SELECT duration_min FROM services WHERE name=?", (bsvc,)).fetchone(); con2.close()
        bdur = row[0] if row else 60; h,m = int(btime.split(":")[0]), int(btime.split(":")[1])
        booked.append((h*60+m, h*60+m+bdur))
    def free(start, dur):
        if start > 18*60: return False
        for bs,be in booked:
            if start < be and start+dur > bs: return False
        return f"{start//60:02d}:{start%60:02d}" not in blocked_slots
    candidates = list(range(9*60, 18*60+1, 60))
    if year==now.year and month==now.month and day==now.day:
        candidates = [c for c in candidates if c > now.hour*60+now.minute]
    return [f"{c//60:02d}:{c%60:02d}" for c in sorted(c for c in candidates if free(c, dur_min))]

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
        "INSERT INTO bookings (user_id,service,year,month,day,time,name,phone) VALUES (?,?,?,?,?,?,?,?)",
        (b.user_id, b.service, b.year, b.month, b.day, b.time, b.name, b.phone))
    con.commit(); con.close()
    return {"ok": True, "id": cur.lastrowid}

if os.path.exists(os.path.join(BASE_DIR, "images")):
    app.mount("/images", StaticFiles(directory=os.path.join(BASE_DIR, "images")), name="images")

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(BASE_DIR, "webapp", "index.html"), encoding="utf-8") as f:
        return f.read()

@app.get("/api/user_bookings")
def api_user_bookings(user_id: int):
    con = db()
    rows = con.execute(
        "SELECT service,year,month,day,time,name,phone FROM bookings WHERE user_id=? ORDER BY year,month,day,time",
        (user_id,)).fetchall()
    con.close()
    return [{"service":r[0],"year":r[1],"month":r[2],"day":r[3],"time":r[4],"name":r[5],"phone":r[6]} for r in rows]
