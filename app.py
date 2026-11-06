# Анонимный чат-бот v3 (типа NektoMe, но злее).
#
# Что добавлено в v3 относительно v2:
#   🎭 маски         — каждый чат оба получают случайные секретные личности
#   🏷 интересы      — теги в профиле, поиск предпочитает общие интересы
#   🔗 комнаты       — приватный чат по 6-значному коду (не случайный)
#   ✊ КНБ           — камень-ножницы-бумага прямо в чате
#   💡 темы          — кнопка подкидывает обоим тему для разговора
#   🏅 ачивки        — 7 достижений, выдаются автоматически
#   🔥 стрики        — дни подряд в боте
#   🏆 топ           — /top: лидеры по карме (анонимно)
#   🧼 антитокс      — мат = предупреждения, 5 = бан
# Плюс всё из v2: карма, признания с ❤️, антиспам, жалобы, админка.
#
# Архитектура прежняя: webhook -> Flask -> SQLite. Развертывание — README.md.
import os
import random
import re
import sqlite3
import time
import traceback

import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anon.db")

MAX_MSG_LEN = 1000
FLOOD_MSGS = 8
FLOOD_WINDOW = 10
LINK_RE = re.compile(r"https?://|www\.|t\.me/|@[A-Za-z0-9_]{5,}", re.I)
TOX_RE = re.compile(r"хуй|пизд|бля|еб[аи]|ёб[аи]|сука|мразот|гнид", re.I)
TOX_LIMIT = 5

app = Flask(__name__)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

# --- база данных: таблицы и миграции ------------------------------------------
conn.execute("""CREATE TABLE IF NOT EXISTS users(
    uid     INTEGER PRIMARY KEY,
    state   TEXT    DEFAULT 'idle',  -- idle|searching|chatting|writing|
                                         -- waiting_room|joining_room
    partner INTEGER DEFAULT 0,
    last    INTEGER DEFAULT 0
)""")
cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
for col, decl in [
    ("gender",  "TEXT DEFAULT ''"),     # '' | m | f
    ("seek",    "TEXT DEFAULT 'a'"),    # a|o|s
    ("karma",   "INTEGER DEFAULT 0"),
    ("chats",   "INTEGER DEFAULT 0"),
    ("reports", "INTEGER DEFAULT 0"),
    ("banned",  "INTEGER DEFAULT 0"),
    ("interests", "TEXT DEFAULT ''"),   # индексы через запятую: "0,3,5"
    ("ach",     "TEXT DEFAULT ''"),     # выданные ачивки: "first_chat,karma10"
    ("streak",  "INTEGER DEFAULT 0"),
    ("last_seen", "TEXT DEFAULT ''"),   # YYYY-MM-DD
    ("tox",     "INTEGER DEFAULT 0"),
]:
    if col not in cols:
        conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        cols.add(col)
conn.execute("""CREATE TABLE IF NOT EXISTS confessions(
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    uid   INTEGER,
    text  TEXT,
    hearts INTEGER DEFAULT 0
)""")
hearts = {r[1] for r in conn.execute("PRAGMA table_info(confessions)")}
if "hearts" not in hearts:
    conn.execute("ALTER TABLE confessions ADD COLUMN hearts INTEGER DEFAULT 0")
conn.execute("""CREATE TABLE IF NOT EXISTS rooms(
    code TEXT PRIMARY KEY,
    uid  INTEGER,
    ts   REAL
)""")
conn.commit()

# --- справочники -----------------------------------------------------------------
MASK_ADJ = ["Хрустальный", "Гневный", "Лунный", "Тихий", "Электрический",
            "Бархатный", "Космический", "Загадочный", "Смешливый", "Вежливый"]
MASK_NOUN = ["Философ", "Пингвин", "Барон", "Кактус", "Самурай",
             "Бухгалтер", "Крот", "Джазист", "Енот", "Астронавт"]
MASK_EMOJI = ["🧊", "🐧", "🎩", "🌵", "⚡", "🧣", "🚀", "🎭", "😂", "💧"]

INTERESTS = [("игры", "🎮"), ("музыка", "🎵"), ("аниме", "🍥"), ("спорт", "⚽"),
             ("кино", "🎬"), ("книги", "📚"), ("мемы", "😂"), ("техника", "💻")]

TOPICS = [
    "Какое приложение ты открываешь первым, когда скучно?",
    "Фильм или сериал, который можешь пересматривать бесконечно?",
    "Самый странный сон, который помнишь?",
    "Если бы у тебя была лишняя жизнь — на что бы её потратил?",
    "Какая музыка играет у тебя прямо сейчас?",
    "Еда, за которую ты готов продать душу?",
    "Какой предмет в школе ты бы отменил навсегда?",
    "Место, куда мечтаешь поехать?",
    "Твой самый бесполезный талант?",
    "Игра, в которую залипаешь дольше всего?",
    "Какой мем твоего детства до сих пор смешит?",
    "Что ты умеешь делать лучше большинства знакомых?",
    "Животное, которое больше всего похоже на тебя по характеру?",
    "Самая глупая покупка в твоей жизни?",
    "Один факт о себе, который мало кто знает.",
    "Если бы завтра отменили школу/работу — как бы прошёл день?",
]

ACH = {
    "first_chat": ("🎓", "Первый чат"),
    "vet":        ("🔥", "10 чатов"),
    "karma10":    ("🌟", "Карма 10+"),
    "writer":     ("✍️", "Первое признание"),
    "streak3":    ("📅", "3 дня подряд"),
    "room":       ("🔗", "Комната по коду"),
    "player":     ("🎲", "Первая КНБ"),
}

# --- клавиатуры --------------------------------------------------------------------
MAIN_KB = {"keyboard": [[{"text": "🔍 Найти собеседника"}],
                        [{"text": "❤️ Признания"}, {"text": "👤 Профиль"}],
                        [{"text": "🔗 Комната"}]],
           "resize_keyboard": True}
CHAT_KB = {"keyboard": [[{"text": "💡 Тема"}, {"text": "✊ КНБ"}],
                        [{"text": "/next"}, {"text": "/stop"}]],
           "resize_keyboard": True}
GENDER_KB = {"inline_keyboard": [[
    {"text": "Я парень", "callback_data": "g:m"},
    {"text": "Я девушка", "callback_data": "g:f"}]]}
SEEK_KB = {"inline_keyboard": [[
    {"text": "Все равно", "callback_data": "s:a"},
    {"text": "Другой пол", "callback_data": "s:o"},
    {"text": "Свой пол", "callback_data": "s:s"}]]}
ROOM_KB = {"inline_keyboard": [[
    {"text": "Создать", "callback_data": "room_new"},
    {"text": "Войти по коду", "callback_data": "room_join"}]]}
CONF_KB = {"inline_keyboard": [[
    {"text": "📝 Написать", "callback_data": "conf_write"},
    {"text": "🎲 Смотреть", "callback_data": "conf_read"}]]}
RPS_KB = {"inline_keyboard": [[
    {"text": "✊", "callback_data": "rps:r"},
    {"text": "✋", "callback_data": "rps:p"},
    {"text": "✌️", "callback_data": "rps:s"}]]}

HELP = ("Анонимный чат со случайными людьми. Имя и фото не видны, вместо "
        "них — 🎭 маски.\n\n"
        "🔍 Найти собеседника — случайный чат (учитывает интересы)\n"
        "🔗 Комната — приватный чат с другом по 6-значному коду\n"
        "❤️ Признания — анонимные записи, можно ставить ❤️\n"
        "✊ КНБ и 💡 Тема — кнопки во время чата\n"
        "/next — новый собеседник, /stop — выйти\n"
        "/report — жалоба (3 жалобы = автобан), /top — топ анонов\n"
        "👤 Профиль — карма, ачивки, стрик\n\n"
        "Правила: без 18+, ссылок, мата, спама и травли.")


# --- низкоуровневые помощники --------------------------------------------------------
def tg(method, **payload):
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=15)
        return r.json()
    except Exception:
        traceback.print_exc()
        return None


def send(chat_id, text, kb=None):
    params = {"chat_id": chat_id, "text": text}
    if kb:
        params["reply_markup"] = kb
    tg("sendMessage", **params)


def get_user(uid):
    return conn.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()


def ensure_user(uid):
    conn.execute("INSERT OR IGNORE INTO users(uid) VALUES(?)", (uid,))
    conn.commit()


def set_state(uid, state):
    conn.execute("UPDATE users SET state=? WHERE uid=?", (state, uid))
    conn.commit()


def parse_ids(raw):
    return {int(x) for x in (raw or "").split(",") if x != ""}


def gender_ok(seek, seeker_gender, candidate_gender):
    if seek == "a" or not seeker_gender:
        return True
    if seek == "o":
        return candidate_gender == ("f" if seeker_gender == "m" else "m")
    return candidate_gender == seeker_gender


# --- ачивки и стрики --------------------------------------------------------------------
def grant_ach(uid, key):
    u = get_user(uid)
    if not u:
        return
    have = set((u["ach"] or "").split(",")) if u["ach"] else set()
    if key in have or not u:
        return
    have.add(key)
    conn.execute("UPDATE users SET ach=? WHERE uid=?", (",".join(sorted(have)), uid))
    conn.commit()
    emoji, name = ACH[key]
    send(uid, f"🏅 Новая ачивка: {emoji} {name}!")


def touch_streak(uid):
    today = time.strftime("%Y-%m-%d")
    u = get_user(uid)
    if u["last_seen"] == today:
        return
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    ns = (u["streak"] + 1) if u["last_seen"] == yesterday else 1
    conn.execute("UPDATE users SET streak=?, last_seen=? WHERE uid=?", (ns, today, uid))
    conn.commit()
    if ns >= 3:
        grant_ach(uid, "streak3")
    if ns > 1:
        send(uid, f"🔥 Стрик: {ns} дн. подряд")


# --- маски и приветствие пары -----------------------------------------------------------------
def make_masks():
    while True:
        a = (random.randrange(len(MASK_ADJ)), random.randrange(len(MASK_NOUN)),
             random.randrange(len(MASK_EMOJI)))
        b = (random.randrange(len(MASK_ADJ)), random.randrange(len(MASK_NOUN)),
             random.randrange(len(MASK_EMOJI)))
        if a != b:
            def fmt(m):
                return f"{MASK_ADJ[m[0]]} {MASK_NOUN[m[1]]} {MASK_EMOJI[m[2]]}"
            return fmt(a), fmt(b)


def pair_hello(uid_a, uid_b, mask_a, mask_b, via_room=False):
    shared = parse_ids(get_user(uid_a)["interests"]) & parse_ids(get_user(uid_b)["interests"])
    shared_line = ""
    if shared:
        names = " ".join(INTERESTS[i][1] for i in sorted(shared) if 0 <= i < len(INTERESTS))
        shared_line = f"\nОбщие интересы: {names}"
    extra = " (комната по коду)" if via_room else ""
    for uid, mine, theirs in ((uid_a, mask_a, mask_b), (uid_b, mask_b, mask_a)):
        send(uid,
             f"🎭{extra} Соединил! Пиши — имя и фото не видны.\n"
             f"Ты сегодня — {mine}.\nСобеседник — {theirs}.{shared_line}\n"
             f"/next — новый, /stop — выйти, /report — жалоба.",
             CHAT_KB)
    conn.execute("UPDATE users SET chats = chats + 1 WHERE uid IN (?,?)", (uid_a, uid_b))
    conn.commit()
    grant_ach(uid_a, "first_chat")
    grant_ach(uid_b, "first_chat")
    for uid in (uid_a, uid_b):
        if get_user(uid)["chats"] >= 10:
            grant_ach(uid, "vet")


# --- поиск случайного собеседника ---------------------------------------------------------------
def try_pair(uid):
    me = get_user(uid)
    my_ints = parse_ids(me["interests"])
    cands = conn.execute(
        "SELECT * FROM users WHERE state='searching' AND uid<>? AND uid<>? AND banned=0",
        (uid, me["last"])).fetchall()
    cands.sort(key=lambda r: -len(my_ints & parse_ids(r["interests"])))
    for b in cands:
        if gender_ok(me["seek"], me["gender"], b["gender"]) and \
           gender_ok(b["seek"], b["gender"], me["gender"]):
            mask_a, mask_b = make_masks()
            conn.execute("UPDATE users SET state='chatting', partner=?, last=? WHERE uid=?",
                         (b["uid"], me["partner"], uid))
            conn.execute("UPDATE users SET state='chatting', partner=?, last=? WHERE uid=?",
                         (uid, b["partner"], b["uid"]))
            conn.commit()
            pair_hello(uid, b["uid"], mask_a, mask_b)
            return True
    return False


def start_search(uid):
    ensure_user(uid)
    u = get_user(uid)
    if u["banned"]:
        send(uid, "Ты заблокирован администрацией.")
        return
    if not u["gender"]:
        send(uid, "Сначала настрой профиль — два вопроса.")
        send(uid, "Выбери пол:", GENDER_KB)
        return
    conn.execute("DELETE FROM rooms WHERE uid=?", (uid,))  # свои комнаты гасим
    conn.commit()
    if u["state"] == "chatting" and u["partner"]:
        end_chat(uid, u["partner"], None, "Собеседник ушёл искать нового.")
    set_state(uid, "searching")
    if not try_pair(uid):
        waiting = conn.execute(
            "SELECT COUNT(*) FROM users WHERE state='searching'").fetchone()[0]
        send(uid, f"Ищу собеседника... В очереди: {waiting}. /stop — отмена.")


def end_chat(a, b, notice_a=None, notice_b=None, rate=True):
    conn.execute("UPDATE users SET state='idle', partner=0 WHERE uid IN (?,?)", (a, b))
    conn.commit()
    if notice_a:
        send(a, notice_a)
    if notice_b:
        send(b, notice_b)
    if rate:
        send(a, "Оцени собеседника — это его карма:", rate_kb(b))
        send(b, "Оцени собеседника — это его карма:", rate_kb(a))


def rate_kb(partner):
    return {"inline_keyboard": [[
        {"text": "👍", "callback_data": f"rate:{partner}:1"},
        {"text": "👎", "callback_data": f"rate:{partner}:-1"}]]}


def stop(uid):
    u = get_user(uid)
    s = u["state"] if u else "idle"
    if s == "chatting" and u["partner"]:
        end_chat(uid, u["partner"], "Ты вышел из чата.", "Собеседник вышел из чата.")
    elif s == "searching":
        set_state(uid, "idle")
        send(uid, "Поиск остановлен.", MAIN_KB)
    elif s == "writing":
        set_state(uid, "idle")
        send(uid, "Запись признания отменена.", MAIN_KB)
    elif s == "waiting_room":
        conn.execute("DELETE FROM rooms WHERE uid=?", (uid,))
        set_state(uid, "idle")
        send(uid, "Комната закрыта.", MAIN_KB)
    elif s == "joining_room":
        set_state(uid, "idle")
        send(uid, "Вход отменён.", MAIN_KB)
    else:
        send(uid, "Жми 👇", MAIN_KB)


def report(uid):
    u = get_user(uid)
    if u["state"] != "chatting" or not u["partner"]:
        send(uid, "Жаловаться можно только во время чата.")
        return
    offender = u["partner"]
    conn.execute("UPDATE users SET reports = reports + 1 WHERE uid=?", (offender,))
    conn.commit()
    o = get_user(offender)
    end_chat(uid, offender, "Спасибо, чат завершён.",
             "Собеседник пожаловался — чат завершён.", rate=False)
    if o["reports"] >= 3 and not o["banned"]:
        conn.execute("UPDATE users SET banned=1 WHERE uid=?", (offender,))
        conn.commit()
        send(offender, "Ты заблокирован по жалобам.")
        if ADMIN_ID:
            send(ADMIN_ID, f"🤖 Автобан {offender}: набрал {o['reports']} жалоб.")
    if ADMIN_ID:
        send(ADMIN_ID, f"🚨 Жалоба: {uid} на {offender} (всего жалоб: {o['reports']}).")


# --- пересылка сообщений: антиспам + антитокс ------------------------------------------------------
flood = {}


def relay(msg):
    uid = msg["chat"]["id"]
    u = get_user(uid)
    if not u or u["state"] != "chatting" or not u["partner"]:
        send(uid, "Ты не в чате. Жми 👇", MAIN_KB)
        return
    partner = u["partner"]

    now = time.time()
    stamps = [t for t in flood.get(uid, []) if now - t < FLOOD_WINDOW]
    stamps.append(now)
    flood[uid] = stamps
    if len(stamps) > FLOOD_MSGS:
        if len(stamps) == FLOOD_MSGS + 1:
            send(uid, f"Не флуди: больше {FLOOD_MSGS} сообщений "
                      f"за {FLOOD_WINDOW} секунд не дойдут.")
        return

    text = msg.get("text") or msg.get("caption") or ""
    if text:
        if LINK_RE.search(text):
            send(uid, "Ссылки и @упоминания запрещены (антиспам). Убери их.")
            return
        if TOX_RE.search(text):
            tox = u["tox"] + 1
            conn.execute("UPDATE users SET tox=? WHERE uid=?", (tox, uid))
            conn.commit()
            if tox >= TOX_LIMIT:
                if u["partner"]:
                    end_chat(uid, partner, None, "Собеседник забанен за мат.", rate=False)
                conn.execute("UPDATE users SET banned=1 WHERE uid=?", (uid,))
                conn.commit()
                send(uid, "Ты заблокирован за мат.")
                if ADMIN_ID:
                    send(ADMIN_ID, f"🧼 Автобан {uid}: мат ({tox} предупреждений).")
            else:
                send(uid, f"Мат — предупреждение {tox}/{TOX_LIMIT}. "
                          f"Ещё немного и бан.")
            return
        if len(text) > MAX_MSG_LEN:
            send(uid, f"Максимум {MAX_MSG_LEN} символов за сообщение.")
            return

    tg("sendChatAction", chat_id=partner, action="typing")
    res = tg("copyMessage", chat_id=partner, from_chat_id=uid, message_id=msg["message_id"])
    if not res or not res.get("ok"):
        end_chat(uid, partner, "Доставка не удалась — чат завершён.",
                 "Собеседник недоступен.", rate=False)
        send(uid, "Жми 🔍 для нового поиска.", MAIN_KB)


# --- комнаты по коду ----------------------------------------------------------------------------------
def room_menu(uid):
    send(uid, "🔗 Комната: приватный чат с тем, кому передашь код. "
              "Никто случайный не попадёт.", ROOM_KB)


def room_create(uid):
    conn.execute("DELETE FROM rooms WHERE ts < ? OR uid=?", (time.time() - 3600, uid))
    conn.commit()
    while True:
        code = str(random.randint(100000, 999999))
        if not conn.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
            break
    conn.execute("INSERT INTO rooms(code, uid, ts) VALUES(?,?,?)", (code, uid, time.time()))
    set_state(uid, "waiting_room")
    grant_ach(uid, "room")
    send(uid, f"Твоя комната создана.\n\n🔑 Код: <b>{code}</b>\n\n"
              f"Передай его другу — он войдёт по нему, и вы будете вдвоём.\n"
              f"Ждём. /cancel — закрыть комнату.")


def room_join_start(uid):
    set_state(uid, "joining_room")
    send(uid, "Напиши 6-значный код комнаты. /cancel — отмена.")


def room_join(uid, code):
    row = conn.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
    if not row:
        send(uid, "Такой комнаты нет. Проверь код и попробуй ещё раз, /cancel — отмена.")
        return
    host = row["uid"]
    host_u = get_user(host)
    if host == uid or not host_u or host_u["state"] != "waiting_room":
        conn.execute("DELETE FROM rooms WHERE code=?", (code,))
        conn.commit()
        set_state(uid, "idle")
        send(uid, "Комната уже закрыта. Жми 👇", MAIN_KB)
        return
    conn.execute("DELETE FROM rooms WHERE code=?", (code,))
    conn.commit()
    set_state(uid, "chatting")
    set_state(host, "chatting")
    conn.execute("UPDATE users SET partner=?, last=0 WHERE uid=?", (host, uid))
    conn.execute("UPDATE users SET partner=?, last=0 WHERE uid=?", (uid, host))
    conn.commit()
    grant_ach(uid, "room")
    mask_a, mask_b = make_masks()
    pair_hello(host, uid, mask_a, mask_b, via_room=True)


# --- КНБ ------------------------------------------------------------------------------------------------
games = {}  # frozenset({uid, uid}) -> {uid: "r|p|s"}
RPS_NAME = {"r": "✊ камень", "p": "✋ бумага", "s": "✌️ ножницы"}
RPS_BEATS = {"r": "s", "s": "p", "p": "r"}  # камень>ножницы, ножницы>бумага, бумага>камень


def rps_start(a, b):
    games[frozenset((a, b))] = {}
    for uid in (a, b):
        send(uid, "🎲 Камень-ножницы-бумага! Выбирай — и не подглядывай.", RPS_KB)


def rps_pick(uid, choice):
    key = None
    for k in games:
        if uid in k:
            key = k
            break
    if key is None:
        send(uid, "Игра не найдена — напиши ✊ КНБ ещё раз.")
        return
    game = games[key]
    if uid in game:
        return  # уже выбрал, ждём второго
    game[uid] = choice
    if len(game) < 2:
        send(uid, "Принято! Ждём выбор собеседника...")
        return
    a, b = tuple(key)
    ca, cb = game[a], game[b]
    del games[key]
    if ca == cb:
        res = {a: "Ничья!", b: "Ничья!"}
    else:
        winner = a if RPS_BEATS[ca] == cb else b
        res = {winner: "🏆 Ты победил!", a if winner == b else b: "😣 Ты проиграл."}
    for uid2, choice2 in ((a, ca), (b, cb)):
        other = b if uid2 == a else a
        send(uid2, f"🎲 КНБ: ты — {RPS_NAME[choice2]}, собеседник — "
                   f"{RPS_NAME[game[other]]}. {res[uid2]}")
        grant_ach(uid2, "player")


# --- признания ----------------------------------------------------------------------------------------
def conf_menu(uid):
    send(uid, "❤️ Признания — анонимные записи, их видят все. Ставь ❤️ тем, "
              "что зайдёт.", CONF_KB)


def conf_write(uid):
    u = get_user(uid)
    if u["state"] in ("chatting", "searching", "waiting_room"):
        send(uid, "Сначала заверши чат/поиск/комнату (/stop).")
        return
    set_state(uid, "writing")
    send(uid, "Пиши признание одним сообщением (до 500 символов, без ссылок).\n"
              "Опубликуется анонимно. /cancel — отмена.")


def conf_save(uid, text):
    if LINK_RE.search(text) or len(text) > 500:
        send(uid, "Без ссылок и до 500 символов. Попробуй ещё раз или /cancel.")
        return
    conn.execute("INSERT INTO confessions(uid, text) VALUES(?,?)", (uid, text))
    set_state(uid, "idle")
    grant_ach(uid, "writer")
    send(uid, "Опубликовано! Анонимно 🤫", MAIN_KB)


def conf_read(uid):
    row = conn.execute(
        "SELECT * FROM confessions ORDER BY RANDOM() LIMIT 1").fetchone()
    if row:
        kb = {"inline_keyboard": [[
            {"text": f"❤️ {row['hearts']}", "callback_data": f"heart:{row['id']}"},
            {"text": "🎲 Ещё", "callback_data": "conf_read"}]]}
        send(uid, f"💬 «{row['text']}»", kb)
    else:
        send(uid, "Пока пусто. Стань первым — 📝 Написать.", CONF_KB)


def conf_heart(uid, conf_id):
    conn.execute("UPDATE confessions SET hearts = hearts + 1 WHERE id=?", (conf_id,))
    conn.commit()
    row = conn.execute("SELECT hearts FROM confessions WHERE id=?", (conf_id,)).fetchone()
    send(uid, f"❤️ У признания теперь {row['hearts']} сердечек.")


# --- тема для разговора --------------------------------------------------------------------------------
def topic(uid):
    u = get_user(uid)
    if u["state"] != "chatting" or not u["partner"]:
        send(uid, "Тема работает только в чате.")
        return
    t = random.choice(TOPICS)
    send(uid, f"💡 Тема для разговора:\n«{t}»")
    send(u["partner"], f"💡 Тема для разговора:\n«{t}»")


# --- профиль, топ, админка ---------------------------------------------------------------------------
def profile(uid):
    u = get_user(uid)
    g = {"m": "парень", "f": "девушка", "": "не указан"}[u["gender"] or ""]
    ints = " ".join(INTERESTS[i][1] for i in sorted(parse_ids(u["interests"]))
                    if 0 <= i < len(INTERESTS)) or "—"
    badges = " ".join(ACH[k][0] for k in (u["ach"] or "").split(",") if k in ACH) or "—"
    warn = "" if u["karma"] >= 0 else "\nКарма минусовая — будь вежливее."
    send(uid, f"👤 Профиль\nПол: {g}\nИнтересы: {ints}\nЧатов: {u['chats']}\n"
              f"Карма: {u['karma']}\n🔥 Стрик: {u['streak']} дн.\n"
              f"🏅 Ачивки: {badges}{warn}", MAIN_KB)


def top_list(uid):
    rows = conn.execute(
        "SELECT uid, karma, ach, chats FROM users WHERE banned=0 "
        "ORDER BY karma DESC, chats DESC LIMIT 10").fetchall()
    if not rows:
        send(uid, "Пока никого. Будь первым — 🔍.", MAIN_KB)
        return
    lines = []
    for i, r in enumerate(rows):
        name = f"Аноним-{r['uid'] % 1000:03d}" + (" (ты)" if r["uid"] == uid else "")
        stars = len([k for k in (r["ach"] or "").split(",") if k in ACH])
        lines.append(f"{i + 1}. {name} — 👍 {r['karma']}, 🏅 {stars}")
    send(uid, "🏆 Топ анонов по карме:\n" + "\n".join(lines), MAIN_KB)


def admin_stats(chat_id):
    q = lambda s: conn.execute(
        "SELECT COUNT(*) FROM users WHERE state=?", (s,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    confs = conn.execute("SELECT COUNT(*) FROM confessions").fetchone()[0]
    send(chat_id, f"Юзеров: {total}\nИщут: {q('searching')}\nВ чатах: {q('chatting') // 2}\n"
                  f"Комнат ждут: {q('waiting_room')}\nПризнаний: {confs}")


def admin_ban(chat_id, arg, ban):
    try:
        target = int(arg)
    except (ValueError, TypeError):
        send(chat_id, "Формат: /ban 123456789 (или /unban 123456789)")
        return
    u = get_user(target)
    if ban:
        if not u:
            send(chat_id, "Нет такого юзера.")
            return
        if u["state"] == "chatting" and u["partner"]:
            end_chat(target, u["partner"], None,
                     "Собеседник заблокирован администрацией.", rate=False)
        conn.execute("UPDATE users SET banned=1 WHERE uid=?", (target,))
        conn.commit()
        send(target, "Ты заблокирован администрацией.")
        send(chat_id, f"{target} забанен.")
    else:
        conn.execute("UPDATE users SET banned=0, reports=0, tox=0 WHERE uid=?", (target,))
        conn.commit()
        send(chat_id, f"{target} разбанен.")


# --- мастер настройки профиля: интересы ------------------------------------------------------------------
def build_int_kb(uid):
    sel = parse_ids(get_user(uid)["interests"])
    rows, row = [], []
    for i, (name, emo) in enumerate(INTERESTS):
        label = ("✅ " if i in sel else "") + f"{emo} {name}"
        row.append({"text": label, "callback_data": f"int:{i}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "Готово →", "callback_data": "int_done"}])
    return {"inline_keyboard": rows}


def cb_interest(uid, idx, message_id=None):
    sel = parse_ids(get_user(uid)["interests"])
    if idx in sel:
        sel.discard(idx)
    elif len(sel) < 3:
        sel.add(idx)
    conn.execute("UPDATE users SET interests=? WHERE uid=?",
                 (",".join(str(x) for x in sorted(sel)), uid))
    conn.commit()
    if message_id:
        res = tg("editMessageReplyMarkup", chat_id=uid, message_id=message_id,
                 reply_markup=build_int_kb(uid))
        if res and res.get("ok"):
            return
    send(uid, "Выбери интересы (до 3):", build_int_kb(uid))


# --- маршрутизация апдейтов --------------------------------------------------------------------------------
def handle_callback(cb):
    uid = cb["from"]["id"]
    data = cb.get("data", "")
    ensure_user(uid)
    msg_id = (cb.get("message") or {}).get("message_id")
    if data.startswith("g:"):
        conn.execute("UPDATE users SET gender=? WHERE uid=?", (data[2:], uid))
        conn.commit()
        send(uid, "Кого ищешь?", SEEK_KB)
    elif data.startswith("s:"):
        conn.execute("UPDATE users SET seek=? WHERE uid=?", (data[2:], uid))
        conn.commit()
        send(uid, "Отметь интересы (до 3) — поиск любит совпадения:", build_int_kb(uid))
    elif data.startswith("int:"):
        cb_interest(uid, int(data[4:]), msg_id)
    elif data == "int_done":
        send(uid, "Готово! Жми 🔍 Найти собеседника.", MAIN_KB)
    elif data.startswith("rate:"):
        _, target, val = data.split(":")
        conn.execute("UPDATE users SET karma = karma + ? WHERE uid=?",
                     (int(val), int(target)))
        conn.commit()
        if int(val) > 0 and get_user(int(target))["karma"] >= 10:
            grant_ach(int(target), "karma10")
        send(uid, "Оценка учтена 🙏")
    elif data.startswith("heart:"):
        conf_heart(uid, int(data[6:]))
    elif data.startswith("rps:"):
        rps_pick(uid, data[4:])
    elif data == "conf_write":
        conf_write(uid)
    elif data == "conf_read":
        conf_read(uid)
    elif data == "room_new":
        u = get_user(uid)
        if u["state"] in ("chatting", "searching"):
            send(uid, "Сначала заверши чат/поиск (/stop).")
        else:
            room_create(uid)
    elif data == "room_join":
        room_join_start(uid)
    elif data == "search":
        start_search(uid)
    elif data == "stop":
        stop(uid)
    tg("answerCallbackQuery", callback_query_id=cb.get("id", ""))


def handle_message(msg):
    uid = msg["chat"]["id"]
    ensure_user(uid)
    u = get_user(uid)
    if u["banned"]:
        send(uid, "Ты заблокирован администрацией.")
        return
    text = (msg.get("text") or "").strip()

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        args = text.split()[1:]
        if cmd == "/start":
            touch_streak(uid)
            if not u["gender"]:
                send(uid, "Привет! Я анонимный чат с масками, ачивками и признаниями. "
                          "Настроим профиль.")
                send(uid, "Выбери пол:", GENDER_KB)
            else:
                send(uid, "Привет! 🔍 — случайный чат, 🔗 — комната с другом.", MAIN_KB)
        elif cmd in ("/next", "/search"):
            start_search(uid)
        elif cmd == "/stop":
            stop(uid)
        elif cmd == "/cancel":
            stop(uid)
        elif cmd == "/report":
            report(uid)
        elif cmd == "/help":
            send(uid, HELP, MAIN_KB)
        elif cmd == "/top":
            top_list(uid)
        elif cmd == "/stats" and uid == ADMIN_ID:
            admin_stats(uid)
        elif cmd == "/ban" and uid == ADMIN_ID:
            admin_ban(uid, args[0] if args else "", True)
        elif cmd == "/unban" and uid == ADMIN_ID:
            admin_ban(uid, args[0] if args else "", False)
        return

    if text == "🔍 Найти собеседника":
        start_search(uid)
    elif text == "❤️ Признания":
        conf_menu(uid)
    elif text == "👤 Профиль":
        profile(uid)
    elif text == "🔗 Комната":
        room_menu(uid)
    elif text == "💡 Тема":
        topic(uid)
    elif text == "✊ КНБ":
        if u["state"] == "chatting" and u["partner"]:
            rps_start(uid, u["partner"])
        else:
            send(uid, "КНБ — только в чате. Жми 👇", MAIN_KB)
    elif u["state"] == "joining_room":
        room_join(uid, text)
    elif u["state"] == "waiting_room":
        send(uid, "Ждём друга с кодом 🙂 /cancel — закрыть комнату.")
    elif u["state"] == "writing":
        conf_save(uid, text)
    else:
        relay(msg)


@app.route("/", methods=["GET"])
def index():
    return "anon chat bot v3 is running"


@app.route("/webhook", methods=["POST"])
def webhook():
    u = request.get_json(force=True, silent=True)
    if u:
        try:
            if "callback_query" in u:
                handle_callback(u["callback_query"])
            elif "message" in u:
                handle_message(u["message"])
        except Exception:
            traceback.print_exc()
    return "ok"


if __name__ == "__main__":
    # Локальная отладка без вебхука: на / отвечает, апдейты сами не придут.
    app.run(port=5000)
