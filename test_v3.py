# Дымовой тест v3: гоняет реальную логику бота с фейковым Telegram.
# Telegram API подменяется записывающей заглушкой, SQLite — настоящий.
# Запуск: py test_v3.py
import sys
import os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pythone boyt")
sys.path.insert(0, REPO)

# чистая база на каждый прогон
dbfile = os.path.join(REPO, "anon.db")
if os.path.exists(dbfile):
    os.remove(dbfile)

import app  # noqa: E402

# --- подмена Telegram: все исходящие собираем в outbox -------------------------
outbox = []


def fake_tg(method, **payload):
    outbox.append((method, payload))
    return {"ok": True}


app.tg = fake_tg


def sent_to(uid, contains=None):
    hits = [p for m, p in outbox if m == "sendMessage" and p["chat_id"] == uid]
    if contains is None:
        return hits
    return [p for p in hits if contains in p.get("text", "")]


def msg(uid, text):
    outbox.clear()
    app.handle_message({"chat": {"id": uid}, "from": {"id": uid}, "text": text,
                        "message_id": 1})


def cb(uid, data, message_id=None):
    outbox.clear()
    app.handle_callback({"from": {"id": uid}, "data": data, "id": "cb",
                         "message": {"message_id": message_id} if message_id else None})


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


A, B, C, D, ADMIN = 1001, 1002, 1003, 1004, 9999
app.ADMIN_ID = ADMIN

# --- настройка профилей ---
msg(A, "/start")
cb(A, "g:m", 5)
cb(A, "s:o", 6)                      # парень ищет девушек
cb(A, "int:0", 7)                    # интерес: игры
cb(A, "int_done")
check("A профиль собран", app.get_user(A)["gender"] == "m"
      and app.get_user(A)["seek"] == "o" and "0" in app.get_user(A)["interests"])

msg(B, "/start")
cb(B, "g:f", 5)
cb(B, "s:a", 6)                      # девушка ищет всех
cb(B, "int:0", 7)                    # тоже игры -> общий интерес
cb(B, "int_done")

# --- поиск и соединение ---
msg(A, "/start")                     # второй /start за стрик
msg(A, "🔍 Найти собеседника")
check("A в поиске", app.get_user(A)["state"] == "searching")
msg(B, "🔍 Найти собеседника")
check("A и B в чате", app.get_user(A)["state"] == "chatting"
      and app.get_user(B)["partner"] == A)
check("приветствие с масками дошло", bool(sent_to(A, "Ты сегодня")))
check("общие интересы показаны", bool(sent_to(A, "Общие интересы")))
check("ачивка первого чата", "first_chat" in app.get_user(A)["ach"])

# --- пересылка и фильтры ---
msg(A, "привет, это тест")
check("сообщение доставлено B", any(p["chat_id"] == B for m, p in outbox
                                    if m == "copyMessage"))
msg(A, "заходи https://spam.ru")
check("ссылка не доставлена", not any(m == "copyMessage" for m, _ in outbox))
check("предупреждение об антиспаме", bool(sent_to(A, "антиспам")))
msg(A, "сука тест")
check("антитокс: предупреждение", bool(sent_to(A, "предупреждение 1/5")))
check("мат не доставлен", not any(m == "copyMessage" for m, _ in outbox))
msg(A, "ещё обычное сообщение")
check("нормальное сообщение снова доставлено",
      any(m == "copyMessage" for m, _ in outbox))

# --- КНБ ---
msg(A, "✊ КНБ")
cb(A, "rps:r", 5)
cb(B, "rps:s", 5)
check("КНБ разрешилась", any(p["chat_id"] == A and "победил" in p.get("text", "")
                             for m, p in outbox if m == "sendMessage"))
check("ачивка игрока", "player" in app.get_user(A)["ach"])

# --- тема ---
msg(A, "💡 Тема")
check("тема пришла обоим", bool(sent_to(A, "Тема для разговора"))
      and bool(sent_to(B, "Тема для разговора")))

# --- выход и оценка ---
msg(A, "/stop")
check("оба вышли", app.get_user(A)["state"] == "idle"
      and app.get_user(B)["state"] == "idle")
cb(A, f"rate:{B}:1", 5)
check("карма B выросла", app.get_user(B)["karma"] == 1)

# --- комната по коду ---
msg(C, "/start")
cb(C, "g:m", 5)
cb(C, "s:a", 6)
cb(C, "int_done")
msg(C, "🔗 Комната")
cb(C, "room_new")
codes = [p for m, p in outbox if m == "sendMessage" and "Код:" in p.get("text", "")]
check("код выдан", bool(codes))
import re
code = re.search(r"Код: <b>(\d{6})</b>", codes[0]["text"]).group(1)

msg(D, "/start")
cb(D, "g:f", 5)
cb(D, "s:a", 6)
cb(D, "int_done")
msg(D, "🔗 Комната")
cb(D, "room_join")
msg(D, code)
check("C и D соединились в комнате", app.get_user(C)["partner"] == D
      and app.get_user(D)["state"] == "chatting")
check("в комнате есть маски", bool(sent_to(C, "комната по коду")))
check("ачивка комнаты", "room" in app.get_user(C)["ach"])

# --- признания ---
msg(A, "/stop")  # A idle
msg(A, "❤️ Признания")
cb(A, "conf_write")
msg(A, "я тайно люблю математику")
check("признание сохранено", app.conn.execute(
    "SELECT COUNT(*) FROM confessions").fetchone()[0] == 1)
check("ачивка автора", "writer" in app.get_user(A)["ach"])
cb(B, "conf_read")
check("B увидел признание", bool(sent_to(B, "математику")))
cb(B, "heart:1")
check("сердечко засчитано", app.conn.execute(
    "SELECT hearts FROM confessions WHERE id=1").fetchone()[0] == 1)

# --- топ и профиль ---
msg(A, "/top")
check("топ отвечает", bool(sent_to(A, "Топ анонов")))
msg(A, "👤 Профиль")
check("в профиле есть ачивки", bool(sent_to(A, "Ачивки")))

# --- повторный чат и второе предупреждение за мат ---
msg(A, "🔍 Найти собеседника")
msg(B, "🔍 Найти собеседника")
check("A и B снова в чате", app.get_user(A)["state"] == "chatting")
msg(A, "бля вот это да")
check("второе предупреждение", bool(sent_to(A, "предупреждение 2/5")))

# --- админка ---
msg(ADMIN, "/stats")
check("статистика админу", bool(sent_to(ADMIN, "Юзеров:")))

print("\nВСЕ ТЕСТЫ ПРОШЛИ ✅")
