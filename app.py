# Анонимный чат-бот для Telegram (типа NektoMe): соединяет случайных
# незнакомцев в анонимный диалог. Работает через webhook: Flask принимает
# апдейты от Telegram, SQLite хранит, кто что ищет и с кем в чате.
# Развертывание — см. README.md.
import os
import sqlite3
import traceback

import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anon.db")

app = Flask(__name__)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS users(
    uid     INTEGER PRIMARY KEY,
    state   TEXT    DEFAULT 'idle',   -- idle | searching | chatting
    partner INTEGER DEFAULT 0,
    last    INTEGER DEFAULT 0        -- прошлый собеседник, чтобы не соединять с ним же
)""")
conn.commit()

MENU = {"inline_keyboard": [
    [{"text": "🔍 Найти собеседника", "callback_data": "search"}],
    [{"text": "🚪 Стоп", "callback_data": "stop"}],
]}

HELP = ("Это анонимный чат: соединяю тебя со случайным незнакомцем. "
        "Ни имя, ни фото не видны.\n\n"
        "🔍 Найти собеседника — начать\n"
        "/next — новый собеседник\n"
        "/stop — выйти из чата\n"
        "/report — пожаловаться на собеседника\n\n"
        "Правила: без 18+, спама и травли — за нарушения бан.")


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
    row = conn.execute("SELECT state, partner, last FROM users WHERE uid=?", (uid,)).fetchone()
    return row  # None или (state, partner, last)


def ensure_user(uid):
    conn.execute("INSERT OR IGNORE INTO users(uid) VALUES(?)", (uid,))
    conn.commit()


def end_chat(a, b, notice_b=None):
    conn.execute("UPDATE users SET state='idle', partner=0 WHERE uid IN (?,?)", (a, b))
    conn.commit()
    if notice_b:
        send(b, notice_b)


def try_pair(uid):
    # Ищем другого ищущего; прошлого собеседника не даём снова.
    me = get_user(uid)
    last = me[2] if me else 0
    row = conn.execute(
        "SELECT uid FROM users WHERE state='searching' AND uid<>? AND uid<>? "
        "ORDER BY RANDOM() LIMIT 1",
        (uid, last),
    ).fetchone()
    if not row:
        return False
    other = row[0]
    prev_a = (get_user(uid) or (0, 0, 0))[1]
    prev_b = (get_user(other) or (0, 0, 0))[1]
    conn.execute("UPDATE users SET state='chatting', partner=?, last=? WHERE uid=?",
                 (other, prev_a, uid))
    conn.execute("UPDATE users SET state='chatting', partner=?, last=? WHERE uid=?",
                 (uid, prev_b, other))
    conn.commit()
    hello = ("Нашёл собеседника! Просто пиши — он увидит сообщения, но не тебя.\n"
             "/next — новый, /stop — выйти, /report — жалоба.")
    send(uid, hello)
    send(other, hello)
    return True


def start_search(uid):
    ensure_user(uid)
    state, partner, _ = get_user(uid)
    if state == "chatting" and partner:
        end_chat(uid, partner, "Собеседник ушёл искать нового. Нажми 🔍, чтобы тоже искать.")
    conn.execute("UPDATE users SET state='searching', partner=0 WHERE uid=?", (uid,))
    conn.commit()
    if not try_pair(uid):
        send(uid, "Ищу свободного собеседника... Как только кто-то нажмёт 🔍 — соединю.\n/stop — отмена.")


def stop(uid):
    state, partner, _ = get_user(uid) or ("idle", 0, 0)
    if state == "chatting" and partner:
        end_chat(uid, partner, "Собеседник вышел из чата.")
        send(uid, "Вышел из чата.")
    elif state == "searching":
        conn.execute("UPDATE users SET state='idle' WHERE uid=?", (uid,))
        conn.commit()
        send(uid, "Поиск остановлен.")
    else:
        send(uid, "Ты и так не в чате.", MENU)


def report(uid):
    state, partner, _ = get_user(uid) or ("idle", 0, 0)
    if state == "chatting" and partner:
        end_chat(uid, partner, "Собеседник пожаловался — чат завершён.")
        send(uid, "Спасибо, чат завершён.")
        if ADMIN_ID:
            send(ADMIN_ID, f"🚨 Жалоба: {uid} на {partner}. Бан: /ban в админке или вручную.")
    else:
        send(uid, "Жаловаться можно только во время чата.")


def relay(msg):
    uid = msg["chat"]["id"]
    state, partner, _ = get_user(uid) or ("idle", 0, 0)
    if state == "chatting" and partner:
        res = tg("copyMessage", chat_id=partner, from_chat_id=uid, message_id=msg["message_id"])
        if not res or not res.get("ok"):
            end_chat(uid, partner)
            send(uid, "Не удалось доставить сообщение — чат завершён. Нажми 🔍 для нового.")
    elif state == "searching":
        send(uid, "Ещё ищу... Подожди чуть-чуть. /stop — отмена.")
    else:
        send(uid, "Сначала найди собеседника 👇", MENU)


def handle_update(u):
    if "callback_query" in u:
        cb = u["callback_query"]
        uid = cb["from"]["id"]
        data = cb.get("data", "")
        if data == "search":
            start_search(uid)
        elif data == "stop":
            stop(uid)
        tg("answerCallbackQuery", callback_query_id=cb["id"])
        return

    msg = u.get("message")
    if not msg:
        return
    uid = msg["chat"]["id"]
    ensure_user(uid)
    text = msg.get("text", "")

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0]
        if cmd == "/start":
            send(uid, "Привет! Я анонимный чат — соединю тебя со случайным "
                      "незнакомцем. Никто не видит ни имя, ни фото.", MENU)
        elif cmd in ("/next", "/search"):
            start_search(uid)
        elif cmd == "/stop":
            stop(uid)
        elif cmd == "/report":
            report(uid)
        elif cmd == "/help":
            send(uid, HELP)
        elif cmd == "/stats" and uid == ADMIN_ID:
            q = lambda s: conn.execute(
                "SELECT COUNT(*) FROM users WHERE state=?", (s,)).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            send(uid, f"Всего юзеров: {total}\nИщут: {q('searching')}\n"
                      f"В чатах: {q('chatting') // 2}")
    else:
        relay(msg)


@app.route("/", methods=["GET"])
def index():
    return "anon chat bot is running"


@app.route("/webhook", methods=["POST"])
def webhook():
    u = request.get_json(force=True, silent=True)
    if u:
        try:
            handle_update(u)
        except Exception:
            traceback.print_exc()
    return "ok"


if __name__ == "__main__":
    # Локальная отладка без вебхука: сообщения сами не придут,
    # но приложение поднимется и на / будет отвечать.
    app.run(port=5000)
