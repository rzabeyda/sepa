"""Веб-админка сайта: вход по одноразовой ссылке из Telegram-бота, управление бронями и услугами.

Вход: адрес /admin и пароль. Пароль каждый админ задаёт себе сам (в базе хранится только хэш scrypt).
Первый вход и «забыл пароль» — одноразовая ссылка из бота (её может запросить только админ из ADMIN_IDS)
или письмо со ссылкой на почту, которую админ сам привязал (нужны SMTP_USER и SMTP_PASSWORD в .env).

Безопасность:
 • пароль минимум 10 символов, простые пароли отклоняются;
 • перебор ограничен: не больше 8 неудачных попыток с одного адреса за 15 минут, плюс задержка на каждую ошибку;
   админам в Telegram приходит предупреждение только при подозрительном подборе пароля;
 • сессия — случайный токен в cookie (HttpOnly, Secure, SameSite=Strict), в базе только его хэш;
   она живёт год и продлевается при каждом заходе, так что пароль вводят один раз на устройство;
   смена пароля закрывает все остальные устройства;
 • изменяющие запросы требуют заголовок X-Requested-With и (если он есть) Origin своего сайта;
 • картинки пересохраняются через Pillow, из текстов вырезается HTML.
"""
import base64
import hashlib
import hmac
import io
import os
import re
import secrets
import shutil
import smtplib
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import sepa_config
from admin_seed import SEED

router = APIRouter()
_ctx = {}

COOKIE = "skf_admin"
SESSION_SECONDS = 365 * 24 * 3600    # вход запоминается на год и продлевается при каждом заходе в админку
TOKEN_SECONDS = 10 * 60              # ссылка для входа — 10 минут
DURATIONS = (60, 90)
PRICE_90 = 50                        # цена 90 минут одинакова для всех услуг (как в боте и на сайте)
DEFAULT_IMG = "images/service-default.jpg"
MAX_IMAGE_BYTES = 2_500_000


def init(db, now_t, notify_user, base_dir, admin_ids, site_url="https://skffmassage.com"):
    _ctx.update(db=db, now_t=now_t, notify_user=notify_user, base_dir=base_dir,
                admin_ids=set(admin_ids), site_url=site_url.rstrip("/"))
    _ensure_schema()


def _db():
    return _ctx["db"]()


def _ensure_schema():
    con = _db()
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS admin_tokens (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_users (
                user_id INTEGER PRIMARY KEY, pw_hash TEXT NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gallery (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, file TEXT NOT NULL,
                status TEXT DEFAULT 'ready', sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '', tg_file_id TEXT DEFAULT '');
        """)
        # видео, которое раньше было прописано в коде, переносим в галерею один раз
        done = con.execute("SELECT 1 FROM settings WHERE key='gallery_seeded'").fetchone()
        if not done:
            if os.path.isfile(os.path.join(_ctx["base_dir"], "videos", "massage01.mp4")) \
                    and not con.execute("SELECT 1 FROM gallery").fetchone():
                con.execute("INSERT INTO gallery (kind,file,status,sort_order,created_at) VALUES ('video','massage01.mp4','ready',1,?)",
                            (_ctx["now_t"]().strftime("%Y-%m-%d %H:%M"),))
            con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('gallery_seeded','1')")
        # обработка видео прервалась (перезапуск сервера) — помечаем, чтобы не висело вечно
        con.execute("UPDATE gallery SET status='error' WHERE status='processing'")
        if "email" not in {r[1] for r in con.execute("PRAGMA table_info(admin_users)")}:
            con.execute("ALTER TABLE admin_users ADD COLUMN email TEXT DEFAULT ''")
        if "via_link" not in {r[1] for r in con.execute("PRAGMA table_info(admin_sessions)")}:
            con.execute("ALTER TABLE admin_sessions ADD COLUMN via_link INTEGER DEFAULT 0")
        cols = {r[1] for r in con.execute("PRAGMA table_info(services)")}
        if cols:
            for c in ("name_et", "name_en", "desc_et", "desc_en"):
                if c not in cols:
                    con.execute(f"ALTER TABLE services ADD COLUMN {c} TEXT DEFAULT ''")
            # переносим в базу переводы, которые раньше жили в коде сайта (только там, где они ещё пусты)
            for name, v in SEED.items():
                con.execute(
                    "UPDATE services SET name_et=?, name_en=?, desc_et=?, desc_en=? WHERE name=? "
                    "AND IFNULL(name_et,'')='' AND IFNULL(name_en,'')='' AND IFNULL(desc_et,'')='' AND IFNULL(desc_en,'')=''",
                    (v.get("name_et", ""), v.get("name_en", ""), v.get("desc_et", ""), v.get("desc_en", ""), name))
        con.commit()
    except Exception as e:   # не мешаем запуску сайта, если база ещё не готова
        print(f"[WARN] admin schema: {e}")
    finally:
        con.close()


# ── вспомогательное ───────────────────────────────────────────────────────────
def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _ip(request: Request):
    peer = request.client.host if request.client else "?"
    if peer in ("127.0.0.1", "::1"):   # nginx на этом же сервере подставляет настоящий адрес посетителя
        return request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip() or peer
    return peer


_login_hits = {}      # ip -> время попыток по ссылке из бота
_fail_times = {}      # ip -> время неудачных попыток ввода пароля
_alerted = {}         # ip -> когда последний раз предупреждали админов
FAIL_LIMIT, FAIL_WINDOW = 8, 15 * 60


def _rate_limited(ip, limit=10, window=600):
    now = time.time()
    hits = [t for t in _login_hits.get(ip, []) if now - t < window]
    _login_hits[ip] = hits
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False


def _recent_failures(ip):
    now = time.time()
    lst = [t for t in _fail_times.get(ip, []) if now - t < FAIL_WINDOW]
    _fail_times[ip] = lst
    return lst


def _notify_admins(text):
    for uid in _ctx["admin_ids"]:
        try:
            _ctx["notify_user"](uid, text)
        except Exception as e:
            print(f"[WARN] notify admin: {e}")


# ── пароли ───────────────────────────────────────────────────────────────────
_COMMON = {"password", "password1", "password12", "password123", "qwertyuiop", "1234567890", "12345678910",
           "qwerty1234", "qwerty123", "admin12345", "administrator", "massage123", "massage1234", "skfmassage",
           "sergei1234", "sergey1234", "1q2w3e4r5t", "iloveyou123", "letmein123"}


def _weak_password(pw):
    if len(pw) < 10:
        return "Пароль должен быть не короче 10 символов"
    if len(pw) > 128:
        return "Пароль слишком длинный (до 128 символов)"
    low = pw.lower()
    if low in _COMMON or any(x in low for x in ("password", "qwerty", "123456", "skfmassage")):
        return "Слишком простой пароль, придумай другой"
    if pw.isdigit():
        return "Пароль из одних цифр не подходит"
    if len(set(pw)) < 5:
        return "В пароле слишком много одинаковых символов"
    return None


def _hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${2 ** 14}$8$1${salt.hex()}${h.hex()}"


def _verify_password(pw, stored):
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(h) // 2, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


_DUMMY_HASH = None


def _issue_session(con, uid, via_link=False):
    """Создаёт сессию: в базу идёт хэш токена, сам токен — только в cookie браузера."""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    con.execute("DELETE FROM admin_sessions WHERE expires_at<?", (now,))
    con.execute("INSERT INTO admin_sessions (token_hash, user_id, created_at, expires_at, via_link) VALUES (?,?,?,?,?)",
                (_hash(token), uid, now, now + SESSION_SECONDS, 1 if via_link else 0))
    return token


def _set_cookie(resp, token):
    resp.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=True, samesite="strict", path="/")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


_BAD = re.compile(r"[<>\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(s, maxlen):
    return _BAD.sub("", str(s or "")).replace("\r", "").strip()[:maxlen]


def _err(code, msg):
    raise HTTPException(status_code=code, detail=msg)


def _auth(request: Request):
    """Проверка сессии. Возвращает id админа или бросает 401/403."""
    token = request.cookies.get(COOKIE, "")
    uid = None
    if token:
        con = _db()
        row = con.execute("SELECT user_id, expires_at FROM admin_sessions WHERE token_hash=?", (_hash(token),)).fetchone()
        con.close()
        if row and row[1] > time.time() and row[0] in _ctx["admin_ids"]:
            uid = row[0]
    if uid is None:
        _err(401, "Нужен вход через бота")
    if request.method not in ("GET", "HEAD"):
        if request.headers.get("x-requested-with") != "skf-admin":
            _err(403, "Запрос отклонён")
        origin = request.headers.get("origin")
        if origin and origin != _ctx["site_url"] and not re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$", origin):
            _err(403, "Запрос отклонён")
    return uid


# ── вход / выход ─────────────────────────────────────────────────────────────
_LOGIN_FAIL = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#070707;color:#ece7df;font-family:system-ui,sans-serif;text-align:center;padding:24px">
<div><h1 style="font-weight:600;font-size:24px">Ссылка не подходит</h1>
<p style="color:#8f897f;line-height:1.6">Она уже использована или устарела (действует 10 минут).<br>Получить новую можно в боте: «Админка» → «Веб-админка».<br><br><a href="/admin" style="color:#dcc592">Ко входу по паролю</a></p></div>"""


@router.get("/admin/login")
def admin_login_link(t: str, request: Request):
    """Одноразовая ссылка из бота: первый вход и «забыл пароль». После входа админка предложит задать пароль."""
    if _rate_limited(_ip(request)):
        return HTMLResponse(_LOGIN_FAIL, status_code=429, headers={"Cache-Control": "no-store"})
    con = _db()
    row = con.execute("SELECT user_id, expires_at FROM admin_tokens WHERE token_hash=? AND used=0", (_hash(t),)).fetchone()
    ok = bool(row and row[1] > time.time() and row[0] in _ctx["admin_ids"])
    if ok:   # одноразовость: помечаем использованной атомарно
        ok = con.execute("UPDATE admin_tokens SET used=1 WHERE token_hash=? AND used=0", (_hash(t),)).rowcount == 1
    if not ok:
        con.close()
        return HTMLResponse(_LOGIN_FAIL, status_code=400, headers={"Cache-Control": "no-store"})
    con.execute("DELETE FROM admin_tokens WHERE expires_at<?", (int(time.time()) - 3600,))
    token = _issue_session(con, row[0], via_link=True)
    con.commit(); con.close()
    return _set_cookie(RedirectResponse("/admin", status_code=303), token)


class LoginIn(BaseModel):
    password: str


@router.post("/api/admin/login")
def password_login(p: LoginIn, request: Request):
    global _DUMMY_HASH
    if request.headers.get("x-requested-with") != "skf-admin":
        _err(403, "Запрос отклонён")
    ip = _ip(request)
    fails = _recent_failures(ip)
    if len(fails) >= FAIL_LIMIT:
        return JSONResponse({"ok": False, "error": "Слишком много попыток. Подожди 15 минут и попробуй снова."}, status_code=429)
    pw = (p.password or "")[:200]
    con = _db()
    rows = con.execute("SELECT user_id, pw_hash FROM admin_users").fetchall()
    uid = None
    if not rows:   # пароли ещё не заданы: тратим то же время, чтобы не выдавать это по скорости ответа
        _DUMMY_HASH = _DUMMY_HASH or _hash_password("dummy-password")
        _verify_password(pw, _DUMMY_HASH)
    for u, h in rows:   # проверяем всех, не останавливаясь на первом совпадении
        if _verify_password(pw, h) and u in _ctx["admin_ids"]:
            uid = u
    if uid is None:
        con.close()
        fails.append(time.time())
        time.sleep(0.7)
        now = time.time()
        if len(fails) >= 5 and now - _alerted.get(ip, 0) > 3600:   # не спамим: не чаще раза в час с одного адреса
            _alerted[ip] = now
            _notify_admins(f"⚠️ Неудачные попытки входа в веб-админку: {len(fails)} с адреса {ip}. "
                           f"Если это не вы, ничего делать не нужно: после 8 попыток адрес блокируется на 15 минут.")
        return JSONResponse({"ok": False, "error": "Неверный пароль"}, status_code=401)
    _fail_times.pop(ip, None)
    token = _issue_session(con, uid)
    con.commit(); con.close()
    return _set_cookie(JSONResponse({"ok": True}), token)


class PasswordIn(BaseModel):
    password: str
    current: str = ""


@router.post("/api/admin/password")
def set_password(p: PasswordIn, request: Request):
    uid = _auth(request)
    weak = _weak_password(p.password or "")
    if weak:
        _err(400, weak)
    con = _db()
    row = con.execute("SELECT pw_hash FROM admin_users WHERE user_id=?", (uid,)).fetchone()
    sess = con.execute("SELECT via_link FROM admin_sessions WHERE token_hash=?", (_hash(request.cookies.get(COOKIE, "")),)).fetchone()
    via_link = bool(sess and sess[0])
    if row and not via_link:   # сменить пароль, зная старый; забыл — войди по ссылке из бота
        if not _verify_password((p.current or "")[:200], row[0]):
            con.close(); time.sleep(0.7); _err(403, "Текущий пароль неверный")
    con.execute("INSERT OR REPLACE INTO admin_users (user_id, pw_hash, updated_at) VALUES (?,?,?)",
                (uid, _hash_password(p.password), int(time.time())))
    # при смене пароля все остальные сессии этого админа закрываются
    con.execute("DELETE FROM admin_sessions WHERE user_id=? AND token_hash<>?", (uid, _hash(request.cookies.get(COOKIE, ""))))
    con.execute("UPDATE admin_sessions SET via_link=0 WHERE token_hash=?", (_hash(request.cookies.get(COOKIE, "")),))
    con.commit(); con.close()
    return {"ok": True}


# ── почта: привязка и восстановление доступа ─────────────────────────────────
def _smtp_config():
    user, pw = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", "")
    if not (user and pw):
        return None
    return (os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")), user, pw, os.getenv("SMTP_FROM", user))


def _send_email(to, subject, body):
    cfg = _smtp_config()
    if not cfg:
        raise RuntimeError("SMTP не настроен")
    host, port, user, pw, sender = cfg
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            s.login(user, pw); s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls(); s.login(user, pw); s.send_message(msg)


def _send_email_safe(to, subject, body):
    try:
        _send_email(to, subject, body)
    except Exception as e:
        print(f"[WARN] email: {e}")


_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,200}\.[^@\s]{2,}")
_forgot_ip = {}
_forgot_mail = {}


class EmailIn(BaseModel):
    email: str


@router.post("/api/admin/email")
def set_email(p: EmailIn, request: Request):
    """Почта для восстановления доступа. Пустое значение отвязывает почту."""
    uid = _auth(request)
    email = clean(p.email, 120).lower()
    if email and not _EMAIL_RE.fullmatch(email):
        _err(400, "Похоже, в почте опечатка")
    con = _db()
    if not con.execute("SELECT 1 FROM admin_users WHERE user_id=?", (uid,)).fetchone():
        con.close(); _err(400, "Сначала задай пароль")
    if email and con.execute("SELECT 1 FROM admin_users WHERE LOWER(email)=? AND user_id<>?", (email, uid)).fetchone():
        con.close(); _err(409, "Эта почта уже привязана к другому админу")
    con.execute("UPDATE admin_users SET email=? WHERE user_id=?", (email, uid))
    con.commit(); con.close()
    return {"ok": True}


@router.post("/api/admin/forgot")
def forgot_password(p: EmailIn, request: Request):
    """Письмо со ссылкой для входа. Отвечаем одинаково, есть такая почта в системе или нет."""
    if request.headers.get("x-requested-with") != "skf-admin":
        _err(403, "Запрос отклонён")
    ip = _ip(request)
    now = time.time()
    hits = [t for t in _forgot_ip.get(ip, []) if now - t < 3600]
    _forgot_ip[ip] = hits
    if len(hits) >= 5:
        return JSONResponse({"ok": False, "error": "Слишком много запросов. Попробуй через час."}, status_code=429)
    hits.append(now)
    if not _smtp_config():
        return JSONResponse({"ok": False, "error": "Восстановление по почте пока не настроено. Используй бота: «Админка» → «Веб-админка»."}, status_code=503)
    email = clean(p.email, 120).lower()
    sent = [t for t in _forgot_mail.get(email, []) if now - t < 3600]
    _forgot_mail[email] = sent
    con = _db()
    row = con.execute("SELECT user_id FROM admin_users WHERE LOWER(email)=? AND email<>''", (email,)).fetchone() if email else None
    if row and row[0] in _ctx["admin_ids"] and len(sent) < 3:
        sent.append(now)
        token = secrets.token_urlsafe(24)
        con.execute("INSERT INTO admin_tokens (token_hash,user_id,expires_at,used) VALUES (?,?,?,0)",
                    (_hash(token), row[0], int(now) + TOKEN_SECONDS))
        con.commit()
        link = f"{_ctx['site_url']}/admin/login?t={token}"
        threading.Thread(target=_send_email_safe, daemon=True, args=(
            email, "Вход в админку SKF Massage",
            "Здравствуйте!\n\nСсылка для входа в админку SKF Massage (одноразовая, действует 10 минут):\n\n"
            f"{link}\n\nПо ней вы войдёте без пароля и сможете задать новый.\n"
            "Если вы не запрашивали вход, просто проигнорируйте это письмо.")).start()
    con.close()
    return {"ok": True, "message": "Если эта почта привязана к админке, мы отправили на неё ссылку для входа. Она действует 10 минут."}


@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    with open(os.path.join(_ctx["base_dir"], "webapp", "admin.html"), encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


@router.get("/api/admin/me")
def admin_me(request: Request):
    uid = _auth(request)
    token = request.cookies.get(COOKIE, "")
    con = _db()
    urow = con.execute("SELECT email FROM admin_users WHERE user_id=?", (uid,)).fetchone()
    has = bool(urow)
    sess = con.execute("SELECT via_link FROM admin_sessions WHERE token_hash=?", (_hash(token),)).fetchone()
    # скользящая сессия: каждый заход продлевает вход ещё на год (и в базе, и в cookie)
    con.execute("UPDATE admin_sessions SET expires_at=? WHERE token_hash=?", (int(time.time()) + SESSION_SECONDS, _hash(token)))
    con.commit(); con.close()
    return _set_cookie(JSONResponse({"ok": True, "user_id": uid, "has_password": has, "via_link": bool(sess and sess[0]),
                                       "email": (urow[0] if urow and urow[0] else ""), "email_enabled": bool(_smtp_config())}), token)


@router.post("/api/admin/logout")
def admin_logout(request: Request):
    _auth(request)
    token = request.cookies.get(COOKIE, "")
    con = _db(); con.execute("DELETE FROM admin_sessions WHERE token_hash=?", (_hash(token),)); con.commit(); con.close()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ── брони ────────────────────────────────────────────────────────────────────
class AdminBookingIn(BaseModel):
    service: str
    year: int
    month: int
    day: int
    time: str
    name: str
    phone: str = ""
    dur_min: int = 60
    force: bool = False
    notify: bool = True


class AdminBookingPatch(BaseModel):
    service: str | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    time: str | None = None
    name: str | None = None
    phone: str | None = None
    dur_min: int | None = None
    force: bool = False
    notify: bool = True


MONTHS_GEN = {1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
              7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"}


def _check_when(year, month, day, time_s):
    try:
        datetime(year, month, day)
    except Exception:
        _err(400, "Неверная дата")
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", time_s or "")
    if not m:
        _err(400, "Неверное время (нужно ЧЧ:ММ)")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _service_defaults(con):
    return {r[0]: (r[1], r[2]) for r in con.execute("SELECT name, price, duration_min FROM services")}


def _price_for(con, service, dur):
    if dur == 90:
        return PRICE_90
    row = con.execute("SELECT price FROM services WHERE name=?", (service,)).fetchone()
    m = re.search(r"\d+", row[0]) if row else None
    return int(m.group()) if m else None


def _conflicts(con, year, month, day, time_s, dur, exclude_id=None):
    """Пересечения с другими бронями (с учётом отдыха после сеанса) — та же логика, что в боте и на сайте."""
    h, mi = map(int, time_s.split(":"))
    start = h * 60 + mi
    defaults = _service_defaults(con)
    rest = sepa_config.get_schedule(con)["rest"]
    found = []
    for bid, btime, bsvc, bdur, bname in con.execute(
            "SELECT id,time,service,duration_min,name FROM bookings WHERE year=? AND month=? AND day=?", (year, month, day)):
        if exclude_id and bid == exclude_id:
            continue
        bd = bdur or (defaults.get(bsvc, ("", 60))[1]) or 60
        bh, bm = map(int, btime.split(":"))
        bs = bh * 60 + bm
        be = bs + bd + rest
        if start < be and start + dur > bs:
            found.append(f"{btime} — {bname}")
    return found


def _booking_dict(r, defaults, now):
    bid, uid, svc, y, m, d, tm, name, phone, dur, price = r
    dur = dur or defaults.get(svc, ("", 60))[1] or 60
    h, mi = map(int, tm.split(":"))
    end = h * 60 + mi + dur
    try:
        past = datetime(y, m, d, h, mi) < now
    except Exception:
        past = False
    return {"id": bid, "service": svc, "year": y, "month": m, "day": d, "time": tm,
            "end_time": f"{end // 60:02d}:{end % 60:02d}", "dur_min": dur, "price_eur": price,
            "name": name, "phone": phone, "telegram": bool(uid and uid > 0), "past": past,
            "date": f"{y}-{m:02d}-{d:02d}"}


@router.get("/api/admin/bookings")
def list_bookings(request: Request, scope: str = "upcoming"):
    _auth(request)
    now = _ctx["now_t"]()
    con = _db()
    defaults = _service_defaults(con)
    rows = con.execute("SELECT id,user_id,service,year,month,day,time,name,phone,duration_min,price_eur FROM bookings "
                       "ORDER BY year,month,day,time").fetchall()
    con.close()
    out = [_booking_dict(r, defaults, now) for r in rows]
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if scope == "today":
        out = [b for b in out if b["date"] == today]
    elif scope == "tomorrow":
        out = [b for b in out if b["date"] == tomorrow]
    elif scope == "upcoming":
        out = [b for b in out if not b["past"]]
    return {"ok": True, "bookings": out, "now": now.strftime("%Y-%m-%d %H:%M")}


def _notify_client(uid, text):
    if uid and uid > 0:
        try:
            _ctx["notify_user"](uid, text)
        except Exception as e:
            print(f"[WARN] notify client: {e}")


@router.post("/api/admin/bookings")
def create_booking(b: AdminBookingIn, request: Request):
    _auth(request)
    time_s = _check_when(b.year, b.month, b.day, b.time)
    if b.dur_min not in DURATIONS:
        _err(400, "Длительность: 60 или 90 минут")
    name = clean(b.name, 60)
    if not name:
        _err(400, "Укажи имя клиента")
    con = _db()
    if not con.execute("SELECT 1 FROM services WHERE name=?", (b.service,)).fetchone():
        con.close(); _err(400, "Такой услуги нет")
    conflicts = _conflicts(con, b.year, b.month, b.day, time_s, b.dur_min)
    if conflicts and not b.force:
        con.close()
        return JSONResponse({"ok": False, "conflict": conflicts, "error": "Время пересекается с другой бронью"}, status_code=409)
    cur = con.execute(
        "INSERT INTO bookings (user_id,service,year,month,day,time,name,phone,duration_min,price_eur) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (0, b.service, b.year, b.month, b.day, time_s, name, clean(b.phone, 30), b.dur_min, _price_for(con, b.service, b.dur_min)))
    con.commit(); con.close()
    return {"ok": True, "id": cur.lastrowid}


@router.put("/api/admin/bookings/{bid}")
def update_booking(bid: int, p: AdminBookingPatch, request: Request):
    _auth(request)
    con = _db()
    row = con.execute("SELECT user_id,service,year,month,day,time,name,phone,duration_min FROM bookings WHERE id=?", (bid,)).fetchone()
    if not row:
        con.close(); _err(404, "Бронь не найдена")
    uid, svc, y, m, d, tm, name, phone, dur = row
    defaults = _service_defaults(con)
    dur = dur or defaults.get(svc, ("", 60))[1] or 60
    new = dict(service=p.service or svc, year=p.year or y, month=p.month or m, day=p.day or d,
               time=p.time or tm, name=name if p.name is None else clean(p.name, 60),
               phone=phone if p.phone is None else clean(p.phone, 30), dur=p.dur_min or dur)
    time_s = _check_when(new["year"], new["month"], new["day"], new["time"])
    if new["dur"] not in DURATIONS:
        con.close(); _err(400, "Длительность: 60 или 90 минут")
    if not new["name"]:
        con.close(); _err(400, "Укажи имя клиента")
    if not con.execute("SELECT 1 FROM services WHERE name=?", (new["service"],)).fetchone():
        con.close(); _err(400, "Такой услуги нет")
    conflicts = _conflicts(con, new["year"], new["month"], new["day"], time_s, new["dur"], exclude_id=bid)
    if conflicts and not p.force:
        con.close()
        return JSONResponse({"ok": False, "conflict": conflicts, "error": "Время пересекается с другой бронью"}, status_code=409)
    moved = (new["year"], new["month"], new["day"], time_s) != (y, m, d, tm)
    price_changed = new["service"] != svc or new["dur"] != dur
    sets = ["service=?", "year=?", "month=?", "day=?", "time=?", "name=?", "phone=?", "duration_min=?"]
    vals = [new["service"], new["year"], new["month"], new["day"], time_s, new["name"], new["phone"], new["dur"]]
    if moved:   # новое время — напоминания должны прийти заново
        sets += ["reminded_24=0", "reminded_2=0"]
    if price_changed:
        sets.append("price_eur=?"); vals.append(_price_for(con, new["service"], new["dur"]))
    con.execute(f"UPDATE bookings SET {', '.join(sets)} WHERE id=?", vals + [bid])
    con.commit(); con.close()
    if moved and p.notify:
        _notify_client(uid, f"🔄 Ваша запись изменена мастером.\n\n💆 {new['service']}\n"
                            f"🕐 {time_s} | {new['day']} {MONTHS_GEN[new['month']]}")
    return {"ok": True}


@router.delete("/api/admin/bookings/{bid}")
def cancel_booking(bid: int, request: Request, notify: bool = True):
    _auth(request)
    con = _db()
    row = con.execute("SELECT user_id,service,year,month,day,time,name,phone FROM bookings WHERE id=?", (bid,)).fetchone()
    if not row:
        con.close(); _err(404, "Бронь не найдена")
    uid, svc, y, m, d, tm, name, phone = row
    con.execute("INSERT INTO cancelled (user_id,service,year,month,day,time,name,phone,cancelled_by,cancelled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, svc, y, m, d, tm, name, phone, "master", _ctx["now_t"]().strftime("%Y-%m-%d %H:%M")))
    con.execute("DELETE FROM bookings WHERE id=?", (bid,))
    con.commit(); con.close()
    if notify:
        _notify_client(uid, f"❌ Ваша бронь отменена мастером.\n\n💆 {svc}\n🕐 {tm} | {d} {MONTHS_GEN[m]}")
    return {"ok": True}


# ── услуги ───────────────────────────────────────────────────────────────────
class AdminServiceIn(BaseModel):
    name: str
    price: str
    duration_min: int = 60
    description: str = ""
    name_et: str = ""
    name_en: str = ""
    desc_et: str = ""
    desc_en: str = ""
    active: bool = True


class AdminServicePatch(BaseModel):
    name: str | None = None
    price: str | None = None
    duration_min: int | None = None
    description: str | None = None
    name_et: str | None = None
    name_en: str | None = None
    desc_et: str | None = None
    desc_en: str | None = None
    active: bool | None = None


def _check_name(name):
    name = clean(name, 40)
    if not (2 <= len(name) <= 25) or len(name.encode()) > 48:
        _err(400, "Название: от 2 до 25 символов (иначе не поместится в кнопки бота)")
    if re.search(r"[:_*`\[\]\\]", name):
        _err(400, "В названии нельзя использовать : _ * ` [ ] \\")
    return name


def _check_price(price):
    m = re.fullmatch(r"\s*(\d{1,4})\s*€?\s*", price or "")
    if not m:
        _err(400, "Цена: число, например 35")
    return f"{int(m.group(1))}€"


def _service_dict(r, upcoming):
    m = re.search(r"\d+", r[2] or "")
    return {"id": r[0], "name": r[1], "price": r[2], "price_eur": int(m.group()) if m else None,
            "duration_min": r[3], "img": r[4], "sort_order": r[5], "active": bool(r[6]),
            "description": r[7] or "", "name_et": r[8] or "", "name_en": r[9] or "",
            "desc_et": r[10] or "", "desc_en": r[11] or "", "upcoming": upcoming.get(r[1], 0)}


_SERVICE_COLS = "id,name,price,duration_min,img,sort_order,active,description,name_et,name_en,desc_et,desc_en"


@router.get("/api/admin/services")
def list_services(request: Request):
    _auth(request)
    now = _ctx["now_t"]()
    con = _db()
    rows = con.execute(f"SELECT {_SERVICE_COLS} FROM services ORDER BY sort_order, id").fetchall()
    upcoming = {}
    for svc, y, m, d, tm in con.execute("SELECT service,year,month,day,time FROM bookings"):
        try:
            if datetime(y, m, d, *map(int, tm.split(":"))) >= now:
                upcoming[svc] = upcoming.get(svc, 0) + 1
        except Exception:
            pass
    con.close()
    return {"ok": True, "services": [_service_dict(r, upcoming) for r in rows], "price_90": PRICE_90}


@router.post("/api/admin/services")
def create_service(s: AdminServiceIn, request: Request):
    _auth(request)
    name = _check_name(s.name)
    price = _check_price(s.price)
    if s.duration_min not in DURATIONS:
        _err(400, "Длительность: 60 или 90 минут")
    con = _db()
    if con.execute("SELECT 1 FROM services WHERE name=?", (name,)).fetchone():
        con.close(); _err(409, "Услуга с таким названием уже есть")
    order = (con.execute("SELECT MAX(sort_order) FROM services").fetchone()[0] or 0) + 1
    cur = con.execute(
        "INSERT INTO services (name,price,duration,duration_min,img,sort_order,active,description,name_et,name_en,desc_et,desc_en) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, price, f"{s.duration_min} мин", s.duration_min, DEFAULT_IMG, order, 1 if s.active else 0,
         clean(s.description, 600), clean(s.name_et, 60), clean(s.name_en, 60), clean(s.desc_et, 600), clean(s.desc_en, 600)))
    con.commit(); con.close()
    return {"ok": True, "id": cur.lastrowid}


@router.put("/api/admin/services/{sid}")
def update_service(sid: int, p: AdminServicePatch, request: Request):
    _auth(request)
    con = _db()
    row = con.execute("SELECT name FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        con.close(); _err(404, "Услуга не найдена")
    old_name = row[0]
    sets, vals = [], []
    if p.name is not None:
        name = _check_name(p.name)
        if name != old_name:
            if con.execute("SELECT 1 FROM services WHERE name=? AND id<>?", (name, sid)).fetchone():
                con.close(); _err(409, "Услуга с таким названием уже есть")
            sets.append("name=?"); vals.append(name)
    if p.price is not None:
        sets.append("price=?"); vals.append(_check_price(p.price))
    if p.duration_min is not None:
        if p.duration_min not in DURATIONS:
            con.close(); _err(400, "Длительность: 60 или 90 минут")
        sets += ["duration_min=?", "duration=?"]; vals += [p.duration_min, f"{p.duration_min} мин"]
    for field, limit in (("description", 600), ("name_et", 60), ("name_en", 60), ("desc_et", 600), ("desc_en", 600)):
        v = getattr(p, field)
        if v is not None:
            sets.append(f"{field}=?"); vals.append(clean(v, limit))
    if p.active is not None:
        sets.append("active=?"); vals.append(1 if p.active else 0)
    if sets:
        con.execute(f"UPDATE services SET {', '.join(sets)} WHERE id=?", vals + [sid])
        if p.name is not None and name != old_name:   # переименование: старые брони и отзывы остаются привязаны к услуге
            for table in ("bookings", "completed_bookings", "reviews"):
                con.execute(f"UPDATE {table} SET service=? WHERE service=?", (name, old_name))
        con.commit()
    con.close()
    return {"ok": True}


@router.delete("/api/admin/services/{sid}")
def delete_service(sid: int, request: Request, force: bool = False):
    _auth(request)
    now = _ctx["now_t"]()
    con = _db()
    row = con.execute("SELECT name,img FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        con.close(); _err(404, "Услуга не найдена")
    name, img = row
    future = 0
    for y, m, d, tm in con.execute("SELECT year,month,day,time FROM bookings WHERE service=?", (name,)):
        try:
            if datetime(y, m, d, *map(int, tm.split(":"))) >= now:
                future += 1
        except Exception:
            pass
    if future and not force:
        con.close()
        return JSONResponse({"ok": False, "upcoming": future,
                             "error": f"На эту услугу есть предстоящие брони: {future}"}, status_code=409)
    con.execute("DELETE FROM services WHERE id=?", (sid,))
    con.commit(); con.close()
    _remove_uploaded(img)
    return {"ok": True}


def _remove_uploaded(img):
    """Удаляем только картинки, загруженные через админку (svc_*), и только если ими больше никто не пользуется."""
    name = os.path.basename(img or "")
    if not name.startswith("svc_"):
        return
    con = _db()
    used = con.execute("SELECT 1 FROM services WHERE img=?", (f"images/{name}",)).fetchone()
    con.close()
    if not used:
        try:
            os.remove(os.path.join(_ctx["base_dir"], "images", name))
        except OSError:
            pass


class PhotoIn(BaseModel):
    image: str   # base64 (можно data:image/...;base64,...)


def _load_image(data, max_side):
    """Читает присланную картинку, проверяет её и возвращает безопасную копию (RGB, не больше max_side)."""
    from PIL import Image, ImageOps
    raw_b64 = data.split(",", 1)[1] if data.startswith("data:") else data
    if len(raw_b64) > MAX_IMAGE_BYTES * 4 // 3 + 100:
        _err(413, "Фото слишком большое")
    try:
        raw = base64.b64decode(raw_b64, validate=False)
        Image.MAX_IMAGE_PIXELS = 40_000_000
        im = Image.open(io.BytesIO(raw))
        if im.format not in ("JPEG", "PNG", "WEBP"):
            _err(400, "Нужен файл JPG, PNG или WebP")
        im = ImageOps.exif_transpose(im).convert("RGB")
    except HTTPException:
        raise
    except Exception:
        _err(400, "Не удалось прочитать картинку")
    im.thumbnail((max_side, max_side))
    return im


@router.post("/api/admin/services/{sid}/photo")
def upload_photo(sid: int, p: PhotoIn, request: Request):
    _auth(request)
    im = _load_image(p.image, 1200)
    con = _db()
    row = con.execute("SELECT img FROM services WHERE id=?", (sid,)).fetchone()
    if not row:
        con.close(); _err(404, "Услуга не найдена")
    old = row[0]
    fname = f"svc_{sid}_{secrets.token_hex(4)}.jpg"
    path = os.path.join(_ctx["base_dir"], "images", fname)
    im.save(path, "JPEG", quality=86, optimize=True)
    con.execute("UPDATE services SET img=? WHERE id=?", (f"images/{fname}", sid))
    con.commit(); con.close()
    _remove_uploaded(old)
    return {"ok": True, "img": f"images/{fname}"}


class MoveIn(BaseModel):
    dir: str


@router.post("/api/admin/services/{sid}/move")
def move_service(sid: int, m: MoveIn, request: Request):
    _auth(request)
    con = _db()
    ids = [r[0] for r in con.execute("SELECT id FROM services ORDER BY sort_order, id")]
    if sid not in ids:
        con.close(); _err(404, "Услуга не найдена")
    i = ids.index(sid)
    j = i - 1 if m.dir == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    for pos, x in enumerate(ids, 1):
        con.execute("UPDATE services SET sort_order=? WHERE id=?", (pos, x))
    con.commit(); con.close()
    return {"ok": True}


# ── отзывы ───────────────────────────────────────────────────────────────────
class AdminReviewIn(BaseModel):
    name: str
    service: str = ""
    rating: int
    text: str = ""


class AdminReviewPatch(BaseModel):
    name: str | None = None
    service: str | None = None
    rating: int | None = None
    text: str | None = None


def _review_dict(r):
    return {"id": r[0], "rating": r[1], "text": r[2] or "", "service": r[3] or "",
            "date": (r[4] or "")[:10], "name": r[5] or ""}


@router.get("/api/admin/reviews")
def list_reviews(request: Request):
    _auth(request)
    con = _db()
    rows = con.execute("SELECT id,rating,text,service,created_at,username FROM reviews ORDER BY id DESC LIMIT 500").fetchall()
    con.close()
    return {"ok": True, "reviews": [_review_dict(r) for r in rows]}


@router.post("/api/admin/reviews")
def create_review(r: AdminReviewIn, request: Request):
    _auth(request)
    name = clean(r.name, 60)
    if not name:
        _err(400, "Укажи имя автора")
    if not 1 <= r.rating <= 5:
        _err(400, "Оценка от 1 до 5")
    con = _db()
    cur = con.execute("INSERT INTO reviews (booking_id,user_id,service,rating,text,created_at,username) VALUES (?,?,?,?,?,?,?)",
                      (0, 0, clean(r.service, 60) or "Массаж", r.rating, clean(r.text, 1000),
                       _ctx["now_t"]().strftime("%Y-%m-%d %H:%M"), name))
    con.commit(); con.close()
    return {"ok": True, "id": cur.lastrowid}


@router.put("/api/admin/reviews/{rid}")
def update_review(rid: int, p: AdminReviewPatch, request: Request):
    _auth(request)
    con = _db()
    if not con.execute("SELECT 1 FROM reviews WHERE id=?", (rid,)).fetchone():
        con.close(); _err(404, "Отзыв не найден")
    sets, vals = [], []
    if p.name is not None:
        name = clean(p.name, 60)
        if not name:
            con.close(); _err(400, "Укажи имя автора")
        sets.append("username=?"); vals.append(name)
    if p.service is not None:
        sets.append("service=?"); vals.append(clean(p.service, 60) or "Массаж")
    if p.rating is not None:
        if not 1 <= p.rating <= 5:
            con.close(); _err(400, "Оценка от 1 до 5")
        sets.append("rating=?"); vals.append(p.rating)
    if p.text is not None:
        sets.append("text=?"); vals.append(clean(p.text, 1000))
    if sets:
        con.execute(f"UPDATE reviews SET {', '.join(sets)} WHERE id=?", vals + [rid])
        con.commit()
    con.close()
    return {"ok": True}


@router.delete("/api/admin/reviews/{rid}")
def delete_review(rid: int, request: Request):
    _auth(request)
    con = _db()
    n = con.execute("DELETE FROM reviews WHERE id=?", (rid,)).rowcount
    con.commit(); con.close()
    if not n:
        _err(404, "Отзыв не найден")
    return {"ok": True}


# ── галерея (фото и видео) ───────────────────────────────────────────────────
MAX_VIDEO_BYTES = 300 * 1024 * 1024
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
_uploads = {}                      # upload_id -> состояние загрузки видео по частям
_video_lock = threading.Lock()     # сжимаем по одному видео за раз, чтобы не нагружать сервер


def _upload_dir():
    d = os.path.join(_ctx["base_dir"], "uploads_tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _gallery_dict(r):
    return {"id": r[0], "kind": r[1], "file": r[2], "status": r[3], "sort_order": r[4]}


@router.get("/api/gallery")
def public_gallery():
    """Публичный список для сайта: только готовые фото и видео, в нужном порядке."""
    con = _db()
    rows = con.execute("SELECT id,kind,file,status,sort_order FROM gallery WHERE status='ready' ORDER BY sort_order, id").fetchall()
    con.close()
    return [{"id": r[0], "kind": r[1], "file": r[2]} for r in rows]


@router.get("/api/admin/gallery")
def admin_gallery(request: Request):
    _auth(request)
    con = _db()
    rows = con.execute("SELECT id,kind,file,status,sort_order FROM gallery ORDER BY sort_order, id").fetchall()
    con.close()
    return {"ok": True, "items": [_gallery_dict(r) for r in rows]}


def _next_gallery_order(con):
    return (con.execute("SELECT MAX(sort_order) FROM gallery").fetchone()[0] or 0) + 1


@router.post("/api/admin/gallery/photo")
def gallery_add_photo(p: PhotoIn, request: Request):
    _auth(request)
    im = _load_image(p.image, 1600)
    fname = f"gal_{secrets.token_hex(5)}.jpg"
    im.save(os.path.join(_ctx["base_dir"], "images", fname), "JPEG", quality=86, optimize=True)
    con = _db()
    cur = con.execute("INSERT INTO gallery (kind,file,status,sort_order,created_at) VALUES ('photo',?,'ready',?,?)",
                      (fname, _next_gallery_order(con), _ctx["now_t"]().strftime("%Y-%m-%d %H:%M")))
    con.commit(); con.close()
    return {"ok": True, "id": cur.lastrowid, "file": fname}


class VideoStart(BaseModel):
    name: str
    size: int


class VideoChunk(BaseModel):
    data: str   # base64 одной части (до ~512 КБ)


@router.post("/api/admin/gallery/video/start")
def video_start(v: VideoStart, request: Request):
    _auth(request)
    ext = os.path.splitext(v.name or "")[1].lower()
    if ext not in VIDEO_EXT:
        _err(400, "Нужно видео: MP4, MOV, M4V, WebM")
    if not 0 < v.size <= MAX_VIDEO_BYTES:
        _err(413, f"Видео должно быть не больше {MAX_VIDEO_BYTES // 1024 // 1024} МБ")
    now = time.time()
    for uid, u in list(_uploads.items()):   # убираем брошенные загрузки старше 2 часов
        if now - u["t"] > 7200:
            try: os.remove(u["path"])
            except OSError: pass
            _uploads.pop(uid, None)
    uid = secrets.token_hex(8)
    path = os.path.join(_upload_dir(), f"{uid}{ext}")
    open(path, "wb").close()
    _uploads[uid] = {"size": v.size, "received": 0, "next": 0, "path": path, "ext": ext, "t": now}
    return {"ok": True, "upload_id": uid, "chunk": 512 * 1024}


@router.post("/api/admin/gallery/video/{uid}/chunk/{index}")
def video_chunk(uid: str, index: int, c: VideoChunk, request: Request):
    _auth(request)
    u = _uploads.get(uid)
    if not u:
        _err(404, "Загрузка не найдена, начни заново")
    if index != u["next"]:
        _err(409, "Нарушен порядок частей")
    try:
        raw = base64.b64decode(c.data, validate=False)
    except Exception:
        _err(400, "Битая часть файла")
    if not raw or u["received"] + len(raw) > u["size"]:
        _err(400, "Размер части не сходится с заявленным")
    with open(u["path"], "ab") as f:
        f.write(raw)
    u["received"] += len(raw)
    u["next"] += 1
    u["t"] = time.time()
    return {"ok": True, "received": u["received"]}


def _run_ffmpeg(src, dst):
    """Сжимаем в MP4 (H.264, до 1280 px по длинной стороне), чтобы видео быстро грузилось на телефонах."""
    vf = "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    cmd = ["nice", "-n", "19", "ffmpeg", "-y", "-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "26", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", dst]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=3600)
        return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 1000
    except FileNotFoundError:   # ffmpeg не установлен: обычный mp4 оставляем как есть
        if src.lower().endswith((".mp4", ".m4v")):
            shutil.copyfile(src, dst)
            return True
        return False
    except Exception:
        return False


def _process_video(gid, src, dst):
    ok = False
    try:
        with _video_lock:
            ok = _run_ffmpeg(src, dst)
    finally:
        try: os.remove(src)
        except OSError: pass
        if not ok:
            try: os.remove(dst)
            except OSError: pass
        con = _db()
        con.execute("UPDATE gallery SET status=? WHERE id=?", ("ready" if ok else "error", gid))
        con.commit(); con.close()


@router.post("/api/admin/gallery/video/{uid}/finish")
def video_finish(uid: str, request: Request):
    _auth(request)
    u = _uploads.get(uid)
    if not u:
        _err(404, "Загрузка не найдена, начни заново")
    if u["received"] != u["size"]:
        _err(400, "Файл загружен не полностью")
    _uploads.pop(uid, None)
    fname = f"gal_{secrets.token_hex(5)}.mp4"
    dst = os.path.join(_ctx["base_dir"], "videos", fname)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    con = _db()
    cur = con.execute("INSERT INTO gallery (kind,file,status,sort_order,created_at) VALUES ('video',?,'processing',?,?)",
                      (fname, _next_gallery_order(con), _ctx["now_t"]().strftime("%Y-%m-%d %H:%M")))
    gid = cur.lastrowid
    con.commit(); con.close()
    threading.Thread(target=_process_video, args=(gid, u["path"], dst), daemon=True).start()
    return {"ok": True, "id": gid}


@router.delete("/api/admin/gallery/{gid}")
def gallery_delete(gid: int, request: Request):
    _auth(request)
    con = _db()
    row = con.execute("SELECT kind,file FROM gallery WHERE id=?", (gid,)).fetchone()
    if not row:
        con.close(); _err(404, "Не найдено")
    con.execute("DELETE FROM gallery WHERE id=?", (gid,))
    con.commit(); con.close()
    folder = "videos" if row[0] == "video" else "images"
    try:
        os.remove(os.path.join(_ctx["base_dir"], folder, os.path.basename(row[1])))
    except OSError:
        pass
    return {"ok": True}


@router.post("/api/admin/gallery/{gid}/move")
def gallery_move(gid: int, m: MoveIn, request: Request):
    _auth(request)
    con = _db()
    ids = [r[0] for r in con.execute("SELECT id FROM gallery ORDER BY sort_order, id")]
    if gid not in ids:
        con.close(); _err(404, "Не найдено")
    i = ids.index(gid)
    j = i - 1 if m.dir == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    for pos, x in enumerate(ids, 1):
        con.execute("UPDATE gallery SET sort_order=? WHERE id=?", (pos, x))
    con.commit(); con.close()
    return {"ok": True}


# ── график работы ────────────────────────────────────────────────────────────
class ScheduleIn(BaseModel):
    work_days: list[int] | None = None
    slots: list[str] | None = None
    rest: int | None = None
    open_all: bool | None = None


class BlockIn(BaseModel):
    date: str
    time: str | None = None


def _open_all(con):
    row = con.execute("SELECT value FROM settings WHERE key='open_all'").fetchone()
    return bool(row and row[0] == "1")


def _blocked(con, today):
    days = [r[0] for r in con.execute("SELECT DISTINCT date FROM schedule WHERE type='day' AND date>=? ORDER BY date", (today,))]
    slots = {}
    for d, t in con.execute("SELECT date,time FROM schedule WHERE type='slot' AND date>=? ORDER BY date,time", (today,)):
        slots.setdefault(d, set()).add(t)
    return {"days": days, "slots": {d: sorted(v) for d, v in slots.items()}}


@router.get("/api/admin/schedule")
def get_schedule(request: Request):
    _auth(request)
    con = _db()
    cfg = sepa_config.get_schedule(con)
    out = {"ok": True, "config": {"work_days": cfg["work_days"], "slots": cfg["slots"], "rest": cfg["rest"]},
           "open_all": _open_all(con), "blocked": _blocked(con, _ctx["now_t"]().strftime("%Y-%m-%d"))}
    con.close()
    return out


@router.put("/api/admin/schedule")
def put_schedule(p: ScheduleIn, request: Request):
    _auth(request)
    con = _db()
    cur = sepa_config.get_schedule(con)
    new = {"work_days": cur["work_days"], "slots": cur["slots"], "rest": cur["rest"]}
    if p.work_days is not None:
        days = sorted({d for d in p.work_days if isinstance(d, int) and 0 <= d <= 6})
        if not days:
            con.close(); _err(400, "Выбери хотя бы один рабочий день")
        new["work_days"] = days
    if p.slots is not None:
        slots = sorted({t for t in p.slots if sepa_config.valid_time(t)})
        if not slots:
            con.close(); _err(400, "Добавь хотя бы одно время начала сеанса")
        if len(slots) > sepa_config.SLOTS_MAX:
            con.close(); _err(400, f"Не больше {sepa_config.SLOTS_MAX} времён")
        new["slots"] = slots
    if p.rest is not None:
        if not 0 <= p.rest <= 180:
            con.close(); _err(400, "Отдых между сеансами: от 0 до 180 минут")
        new["rest"] = p.rest
    sepa_config.save_json(con, "schedule_config", new)
    if p.open_all is not None:
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('open_all',?)", ("1" if p.open_all else "0",))
    con.commit(); con.close()
    return {"ok": True}


def _check_block(b, con):
    try:
        d = datetime.strptime(b.date, "%Y-%m-%d")
    except ValueError:
        con.close(); _err(400, "Неверная дата")
    now = _ctx["now_t"]()
    if d.date() < now.date() or (d - now).days > 800:
        con.close(); _err(400, "Дата должна быть в будущем, не дальше двух лет")
    if b.time is not None and b.time not in sepa_config.get_schedule(con)["slots"]:
        con.close(); _err(400, "Такого времени нет в графике")


@router.post("/api/admin/schedule/block")
def block(b: BlockIn, request: Request):
    _auth(request)
    con = _db()
    _check_block(b, con)
    if b.time is None:
        if not con.execute("SELECT 1 FROM schedule WHERE type='day' AND date=?", (b.date,)).fetchone():
            con.execute("INSERT INTO schedule (type,date,time) VALUES ('day',?,NULL)", (b.date,))
    elif not con.execute("SELECT 1 FROM schedule WHERE type='slot' AND date=? AND time=?", (b.date, b.time)).fetchone():
        con.execute("INSERT INTO schedule (type,date,time) VALUES ('slot',?,?)", (b.date, b.time))
    con.commit(); con.close()
    return {"ok": True}


@router.post("/api/admin/schedule/unblock")
def unblock(b: BlockIn, request: Request):
    _auth(request)
    con = _db()
    try:
        datetime.strptime(b.date, "%Y-%m-%d")
    except ValueError:
        con.close(); _err(400, "Неверная дата")
    if b.time is None:
        con.execute("DELETE FROM schedule WHERE type='day' AND date=?", (b.date,))
    else:
        con.execute("DELETE FROM schedule WHERE type='slot' AND date=? AND time=?", (b.date, b.time))
    con.commit(); con.close()
    return {"ok": True}


# ── содержимое сайта: имя, подписи, контакты, соцсети, фото ───────────────────
_PHONE_RE = re.compile(r"^[+0-9 ()\-]{5,25}$")
_TG_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")


def _text(v, limit, field, required=False):
    t = clean(v, limit)
    if required and not t:
        _err(400, f"Поле «{field}» не должно быть пустым")
    return t


def _clean_site(data):
    """Проверяет присланные поля. Возвращает только то, что разрешено менять, в безопасном виде."""
    if not isinstance(data, dict):
        _err(400, "Неверные данные")
    out = {}
    names = {"name": "Имя", "grade": "Подпись под именем", "city": "Город", "experience": "Опыт"}
    for key, title in names.items():
        if key in data:
            d = data[key]
            if not isinstance(d, dict):
                _err(400, f"Поле «{title}» заполнено неверно")
            out[key] = {"ru": _text(d.get("ru"), 80, title + " (русский)", True),
                        "et": _text(d.get("et"), 80, title), "en": _text(d.get("en"), 80, title)}
    if "company" in data: out["company"] = _text(data["company"], 60, "Компания", True)
    if "cert" in data: out["cert"] = _text(data["cert"], 30, "Сертификат")
    if "address" in data: out["address"] = _text(data["address"], 100, "Адрес", True)
    if "phone" in data:
        ph = _text(data["phone"], 25, "Телефон", True)
        if not _PHONE_RE.match(ph):
            _err(400, "Телефон: только цифры, +, пробелы, скобки и дефис")
        out["phone"] = ph
    if "telegram" in data:
        tg = _text(data["telegram"], 80, "Telegram", True)
        tg = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", tg).lstrip("@").strip("/")
        if not _TG_RE.match(tg):
            _err(400, "Telegram: имя пользователя, например SKF_Massage")
        out["telegram"] = tg
    if "socials" in data:
        if not isinstance(data["socials"], dict):
            _err(400, "Соцсети заполнены неверно")
        soc = {}
        for k in ("stebby", "facebook", "instagram", "tiktok", "youtube"):
            if k in data["socials"]:
                u = clean(data["socials"][k], 200)
                if u:
                    pu = urlparse(u)
                    if pu.scheme != "https" or not pu.netloc or re.search(r"[\s\"'`<>]", u):
                        _err(400, f"Ссылка «{k}» должна начинаться с https://")
                soc[k] = u
        out["socials"] = soc
    return out


@router.get("/api/site")
def public_site():
    con = _db(); cfg = sepa_config.get_site(con); con.close()
    return cfg


@router.get("/api/admin/site")
def admin_site(request: Request):
    _auth(request)
    con = _db(); cfg = sepa_config.get_site(con); con.close()
    return {"ok": True, "site": cfg}


@router.put("/api/admin/site")
def put_site(request: Request, data: dict):
    _auth(request)
    upd = _clean_site(data)
    con = _db()
    cur = sepa_config.get_site(con)
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(cur.get(k), dict):
            cur[k].update(v)
        else:
            cur[k] = v
    sepa_config.save_json(con, "site_config", cur)
    con.commit(); con.close()
    return {"ok": True}


@router.post("/api/admin/site/photo")
def site_photo(p: PhotoIn, request: Request):
    _auth(request)
    im = _load_image(p.image, 1600)
    fname = f"site_{secrets.token_hex(4)}.jpg"
    im.save(os.path.join(_ctx["base_dir"], "images", fname), "JPEG", quality=88, optimize=True)
    con = _db()
    cur = sepa_config.get_site(con)
    old = cur.get("photo", "")
    cur["photo"] = f"images/{fname}"
    sepa_config.save_json(con, "site_config", cur)
    con.commit(); con.close()
    if os.path.basename(old).startswith("site_"):   # прежнее загруженное фото удаляем, исходное sergey.jpg не трогаем
        try: os.remove(os.path.join(_ctx["base_dir"], "images", os.path.basename(old)))
        except OSError: pass
    return {"ok": True, "photo": f"images/{fname}"}
