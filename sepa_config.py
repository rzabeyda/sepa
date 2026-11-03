"""Настройки, которые редактируются в веб-админке: график работы и содержимое сайта.

Общий код для сайта (api.py) и бота (bot.py): они читают одно и то же из таблицы settings,
поэтому изменения в админке сразу видны и там, и там. Если в базе ничего нет, действуют значения по умолчанию.
"""
import copy
import json
import sqlite3

DEFAULT_SCHEDULE = {"work_days": [0, 2, 4, 5], "slots": ["12:00", "14:00", "16:00", "18:00"], "rest": 30}

DEFAULT_SITE = {
    "name": {"ru": "Сергей", "et": "Sergei", "en": "Sergei"},
    "grade": {"ru": "Массажист, 5 разряд", "et": "Massöör, 5. tase", "en": "Massage therapist, Level 5"},
    "city": {"ru": "Таллин", "et": "Tallinn", "en": "Tallinn"},
    "experience": {"ru": "2+ года", "et": "2+ aastat", "en": "2+ years"},
    "company": "SKF-MASSAGE OÜ",
    "cert": "№245245",
    "address": "Linnamäe tee 24, Tallinn",
    "phone": "+372 53 730 882",
    "telegram": "SKF_Massage",
    "photo": "images/sergey.jpg",
    "socials": {
        "stebby": "https://app.stebby.eu/pos/skf.massage.oy",
        "facebook": "https://www.facebook.com/sergey.kolesnikov.7140",
        "instagram": "https://www.instagram.com/sergey.kolesnikov.7140",
        "tiktok": "https://www.tiktok.com/@serega.17..31",
        "youtube": "",
    },
}

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
SLOTS_MAX = 12


def _load(con, key):
    try:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:   # таблицу создаёт бот или сайт при запуске
        return {}
    if not row:
        return {}
    try:
        data = json.loads(row[0])
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def save_json(con, key, obj):
    con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, json.dumps(obj, ensure_ascii=False)))


def _merge(default, override):
    """Накладывает сохранённые значения на значения по умолчанию, не позволяя поменять тип поля."""
    out = copy.deepcopy(default)
    for k, v in (override or {}).items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif isinstance(v, type(out[k])):
            out[k] = v
    return out


def valid_time(s):
    if not isinstance(s, str) or len(s) != 5 or s[2] != ":":
        return False
    h, m = s[:2], s[3:]
    return h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59


def normalize_schedule(cfg):
    """Приводит график к безопасному виду: дни 0..6, время ЧЧ:ММ без повторов, отдых 0..180 минут."""
    days = sorted({d for d in cfg.get("work_days", []) if isinstance(d, int) and 0 <= d <= 6}) or list(DEFAULT_SCHEDULE["work_days"])
    slots = sorted({s for s in cfg.get("slots", []) if valid_time(s)})[:SLOTS_MAX] or list(DEFAULT_SCHEDULE["slots"])
    rest = cfg.get("rest", DEFAULT_SCHEDULE["rest"])
    rest = rest if isinstance(rest, int) and 0 <= rest <= 180 else DEFAULT_SCHEDULE["rest"]
    slot_min = [int(s[:2]) * 60 + int(s[3:]) for s in slots]
    return {"work_days": days, "slots": slots, "rest": rest,
            "slot_min": slot_min, "close_min": slot_min[-1] + 90}   # закрытие: последний сеанс + 90 минут


def get_schedule(con):
    return normalize_schedule(_merge(DEFAULT_SCHEDULE, _load(con, "schedule_config")))


def get_site(con):
    return _merge(DEFAULT_SITE, _load(con, "site_config"))


def describe_schedule_ru(cfg):
    """«Пн, Ср, Пт, Сб: запись на 12:00, 14:00, 16:00, 18:00» — для приветствия в боте."""
    return f"{', '.join(WEEKDAYS_RU[d] for d in cfg['work_days'])}: запись на {', '.join(cfg['slots'])}"
