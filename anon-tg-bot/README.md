# Анонимный чат-бот для Telegram (типа NektoMe)

Соединяет случайных людей в анонимный диалог. Код бесплатный, хостинг
бесплатный, карта не нужна.

**Как устроено:** код лежит на GitHub, бот крутится 24/7 на PythonAnywhere
(бесплатный тариф), Telegram присылает сообщения боту через webhook.

> Почему не GitHub Actions: у Actions лимит 6 часов на задачу и минимальный
> интервал 5 минут — постоянный бот там не живёт, а такие трюки против
> правил GitHub и заканчиваются баном аккаунта.

## Что умеет

- 🔍 поиск случайного собеседника, анонимная пересылка сообщений (текст,
  фото, стикеры, голосовые — всё, что угодно, через `copyMessage`)
- `/next` — новый собеседник (не попадётся тот же, что только что был)
- `/stop` — выйти, `/report` — жалоба (уходит админу)
- `/stats` — статистика (только для админа)

## Шаг 1. Создай бота

1. В Telegram открой [@BotFather](https://t.me/BotFather) → `/newbot` →
   придумай имя и username. Получишь **токен** вида `1234567:AAE...` —
   никому его не показывай.
2. Узнай свой Telegram ID: напиши [@userinfobot](https://t.me/userinfobot) —
   он пришлёт число. Это будет **ADMIN_ID**.

## Шаг 2. Залей код на GitHub

1. На github.com нажми **New repository**, назови например `anon-tg-bot`,
   включи `.gitignore: Python` (не обязательно), **Create repository**.
2. Нажми **uploading an existing file** и перетащи `app.py`,
   `requirements.txt` и `.gitignore` → **Commit changes**.

## Шаг 3. Подними сервер на PythonAnywhere

1. Зарегистрируйся на [pythonanywhere.com](https://www.pythonanywhere.com) —
   тариф **Beginner** (бесплатный, карта не нужна).
2. Открой вкладку **Consoles → Bash** и скачай код (подставь свой ник на GitHub):

   ```bash
   git clone https://github.com/ТВОЙ_НИК/anon-tg-bot.git
   ```

3. Вкладка **Web → Add a new web app** → Next → **Manual configuration** →
   **Python 3.10** → Next.
4. На странице веб-приложения найди **WSGI configuration file** (ссылка вида
   `/var/www/ТВОЙ_НИК_pythonanywhere_com_wsgi.py`) и замени всё содержимое на:

   ```python
   import os
   import sys

   sys.path.insert(0, "/home/ТВОЙ_НИК/anon-tg-bot")
   os.environ["BOT_TOKEN"] = "сюда_токен_от_BotFather"
   os.environ["ADMIN_ID"] = "сюда_свой_id"

   from app import app as application
   ```

   Файл лежит **вне git-репозитория**, поэтому токен в коде на GitHub не
   окажется. **Save**, затем зелёная кнопка **Reload** на вкладке Web.
5. Проверка: открой `https://ТВОЙ_НИК.pythonanywhere.com/` — должно быть
   `anon chat bot is running`.

## Шаг 4. Подключи Telegram к серверу

В Bash-консоли PythonAnywhere (или в любом терминале) подставь свой токен
и адрес:

```bash
curl "https://api.telegram.org/botТОКЕН/setWebhook?url=https://ТВОЙ_НИК.pythonanywhere.com/webhook"
```

В ответе должно быть `"ok":true`. Открой бота в Telegram, `/start` —
должно прийти меню.

**Тест:** попроси друга тоже нажать 🔍 — вы соединитесь, сообщения пойдут.

## Обновление кода

Поменял файлы на GitHub → в Bash-консоли:

```bash
cd ~/anon-tg-bot && git pull
```

→ кнопка **Reload** на вкладке Web.

## Нюансы бесплатного тарифа

- Раз в ~3 месяца PythonAnywhere просит продлить веб-приложение кнопкой
  **Run until 3 months from today** на вкладке Web — просто жми, это
  бесплатно.
- Домен будет `ник.pythonanywhere.com` — этого достаточно, HTTPS уже есть.
- Не рассылай спам и инвайты через бота — Telegram банит таких ботов.

## Идеи для развития (делай сам, спроси меня — помогу)

- «Признания»: кнопка, текст уходит в публичный канал анонимно
- фильтр ссылок и @упоминаний (антиспам) перед пересылкой
- выбор пола собеседника, счётчик «дней без жалоб»
- бан по uid для админа: `/ban 123456` → юзер больше не ищется
