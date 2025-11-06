# server/Bot_poll.py
# --- Telegram Bot + Flask server (polling architecture) ---
import os, time, threading, sqlite3, base64, io, json
from flask import Flask, request, jsonify
from telebot import TeleBot, types
from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    print("❌ ERROR: Set TOKEN in .env")
    exit(1)
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")
API_PORT = int(os.getenv("API_PORT", "5000"))

# ---------------- DATABASE ----------------
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
        status TEXT DEFAULT 'pending',
        result TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )""")
    conn.commit()
    conn.close()

# ---------------- FLASK API ----------------
app = Flask(__name__)
devices = {}

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

@app.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    dev_id = data.get("device_id")
    if not dev_id:
        return jsonify({"success": False, "error": "no device_id"}), 400
    devices[dev_id] = {"last_seen": "now"}
    return jsonify({"success": True})

@app.route("/devices", methods=["GET"])
def get_devices():
    return jsonify({"count": len(devices), "devices": list(devices.keys())})
    
@app.route("/poll", methods=["POST"])
def poll():
    """Agent polls with its computer_hash"""
    data = request.json or {}
    ch = data.get("computer_hash")
    if not ch:
        return jsonify({"success": False, "message": "no computer_hash"}), 400

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO computers (computer_hash, last_seen) VALUES (?, datetime('now'))", (ch,))
    cur.execute("UPDATE computers SET last_seen = datetime('now') WHERE computer_hash = ?", (ch,))
    cur.execute("SELECT id, command, payload FROM commands WHERE computer_hash = ? AND status = 'pending'", (ch,))
    rows = cur.fetchall()

    commands = []
    ids = []
    for r in rows:
        commands.append({"id": r["id"], "command": r["command"], "payload": r["payload"]})
        ids.append(r["id"])

    if ids:
        cur.executemany("UPDATE commands SET status='running', updated_at=datetime('now') WHERE id=?", [(i,) for i in ids])
        conn.commit()
    conn.close()
    return jsonify({"success": True, "commands": commands})

@app.route("/result", methods=["POST"])
def result():
    data = request.json or {}
    cid = data.get("id")
    status = data.get("status")
    result = data.get("result")
    if not cid:
        return jsonify({"success": False, "message": "no id"}), 400

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE commands SET status=?, result=?, updated_at=datetime('now') WHERE id=?", (status, result, cid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ---------------- TELEGRAM BOT ----------------
bot = TeleBot(TOKEN, parse_mode="HTML")

states = {}

def ensure_user(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def get_user_computers(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT computer_hash, last_seen FROM computers ORDER BY last_seen DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

# ---------------- COMMANDS ----------------
@bot.message_handler(commands=['start','help'])
def cmd_start(m):
    ensure_user(m.chat.id)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📱 Устройства", "🖥️ Система", "📁 Файлы")
    bot.send_message(
        m.chat.id,
        "👋 Добро пожаловать!\n\n"
        "Этот бот позволяет подключать ваши устройства и управлять ими безопасно.\n"
        "Выберите действие ниже:",
        reply_markup=kb
    )

# ---------- УСТРОЙСТВА ----------
@bot.message_handler(func=lambda msg: msg.text=="📱 Устройства")
def menu_devices(m):
    rows = get_user_computers(m.chat.id)
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"{r['computer_hash'][:12]}... last {r['last_seen']}", callback_data=f"select:{r['computer_hash']}"))
    kb.add(types.InlineKeyboardButton("📦 Скачать агент", callback_data="download_agent"))

    if not rows:
        bot.send_message(m.chat.id, "Пока нет зарегистрированных устройств.\n👇 Скачайте агент и запустите его на вашем ПК:", reply_markup=kb)
    else:
        bot.send_message(m.chat.id, "Выберите устройство или скачайте агент:", reply_markup=kb)

@bot.callback_query_handler(lambda c: c.data == "download_agent")
def send_agent_zip(call):
    bot.answer_callback_query(call.id)
    agent_path = "/home/dmitry/Pocket-access/agent/SystemAgent.zip"
    try:
        with open(agent_path, "rb") as f:
            bot.send_document(
                call.message.chat.id,
                f,
                visible_file_name="SystemAgent.zip",
                caption=(
                    "⬇️ <b>Агент для Windows</b>\n\n"
                    "1️⃣ Скачайте архив и распакуйте.\n"
                    "2️⃣ Запустите файл <code>Start Agent.bat</code>.\n"
                    "3️⃣ Агент подключится к серверу автоматически.\n\n"
                    "После этого вернитесь сюда и выберите устройство."
                )
            )
    except FileNotFoundError:
        bot.send_message(call.message.chat.id, "⚠️ Агент пока не загружен на сервер.")

@bot.callback_query_handler(lambda c: c.data and c.data.startswith("select:"))
def on_select_device(call):
    ch = call.data.split(":",1)[1]
    states[call.message.chat.id] = {"computer_hash": ch}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, f"✅ Устройство выбрано:\n<code>{ch}</code>")

# ---------- СИСТЕМА ----------
@bot.message_handler(func=lambda msg: msg.text=="🖥️ Система")
def menu_system(m):
    st = states.get(m.chat.id)
    if not st:
        return bot.send_message(m.chat.id, "Сначала выберите устройство.")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📸 Скриншот", "💻 Инфо", "🔄 Процессы", "🔙 Назад")
    bot.send_message(m.chat.id, "Системные команды:", reply_markup=kb)

def add_command(ch, command, payload=""):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO commands (computer_hash, command, payload) VALUES (?, ?, ?)", (ch, command, payload))
    conn.commit()
    conn.close()

@bot.message_handler(func=lambda msg: msg.text=="📸 Скриншот")
def cmd_screenshot(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    add_command(st["computer_hash"], "screenshot")
    bot.send_message(m.chat.id, "📸 Скриншот запрошен.")

@bot.message_handler(func=lambda msg: msg.text=="💻 Инфо")
def cmd_info(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    add_command(st["computer_hash"], "system_info")
    bot.send_message(m.chat.id, "💻 Информация о системе запрошена.")

@bot.message_handler(func=lambda msg: msg.text=="🔄 Процессы")
def cmd_procs(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    add_command(st["computer_hash"], "processes")
    bot.send_message(m.chat.id, "🔄 Список процессов запрошен.")

# ---------- ФАЙЛЫ ----------
@bot.message_handler(func=lambda msg: msg.text=="📁 Файлы")
def menu_files(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Сначала выберите устройство.")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📂 Список", "⬇️ Скачать", "⬆️ Загрузить", "🗑️ Удалить", "🔙 Назад")
    bot.send_message(m.chat.id, "Файловые операции:", reply_markup=kb)

@bot.message_handler(func=lambda msg: msg.text in ["📂 Список","⬇️ Скачать","🗑️ Удалить"])
def ask_path(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    states[m.chat.id]["pending"] = m.text
    bot.send_message(m.chat.id, "Введите путь (например C:\\\\Users\\\\Public):")

@bot.message_handler(content_types=['document'])
def received_document(m):
    st = states.get(m.chat.id)
    if not st or not st.get("pending_upload"):
        return
    file_info = bot.get_file(m.document.file_id)
    downloaded = bot.download_file(file_info.file_path)
    st["upload_bytes"] = downloaded
    st["upload_name"] = m.document.file_name
    bot.send_message(m.chat.id, f"Файл <b>{m.document.file_name}</b> получен. Введите путь на ПК для сохранения:")

@bot.message_handler(func=lambda msg: msg.text=="⬆️ Загрузить")
def upload_start(m):
    st = states.get(m.chat.id)
    if not st: return bot.send_message(m.chat.id, "Выберите устройство.")
    st["pending_upload"] = True
    bot.send_message(m.chat.id, "Отправьте файл в чат (как документ).")

@bot.message_handler(func=lambda msg: True)
def handle_text(m):
    st = states.get(m.chat.id)
    if not st: return
    # upload target path
    if st.get("pending_upload") and st.get("upload_bytes"):
        target = m.text.strip()
        ch = st["computer_hash"]
        b = st["upload_bytes"]
        name = st["upload_name"]
        payload_b64 = base64.b64encode(b).decode()
        payload = {"target_path": target, "name": name, "file_b64": payload_b64}
        add_command(ch, "upload", str(payload))
        bot.send_message(m.chat.id, "⬆️ Файл отправлен агенту для сохранения.")
        st.pop("pending_upload", None)
        st.pop("upload_bytes", None)
        st.pop("upload_name", None)
        return

    pending = st.get("pending")
    if not pending:
        return
    ch = st["computer_hash"]
    path = m.text.strip()
    if pending == "📂 Список":
        add_command(ch, "list_files", path)
        bot.send_message(m.chat.id, "📂 Список файлов запрошен.")
    elif pending == "⬇️ Скачать":
        add_command(ch, "download", path)
        bot.send_message(m.chat.id, "⬇️ Файл запрошен.")
    elif pending == "🗑️ Удалить":
        add_command(ch, "delete", path)
        bot.send_message(m.chat.id, "🗑️ Удаление файла запрошено.")
    st.pop("pending", None)

# ---------- ФОН: Проверка результатов ----------
def poll_results():
    while True:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, computer_hash, command, result FROM commands WHERE status IN ('done','failed')")
        rows = cur.fetchall()
        for r in rows:
            cid = r["id"]; cmd = r["command"]; res = r["result"]
            cur2 = conn.cursor()
            cur2.execute("SELECT telegram_id FROM users")
            users = cur2.fetchall()
            for u in users:
                try:
                    if cmd == "screenshot" and res:
                        img = base64.b64decode(res)
                        bot.send_photo(u["telegram_id"], img, caption=f"📸 Скриншот ({r['computer_hash'][:10]})")
                    elif cmd == "download" and res:
                        data = json.loads(res)
                        file_b64 = data.get("file_b64")
                        name = data.get("name","file.bin")
                        bot.send_document(u["telegram_id"], io.BytesIO(base64.b64decode(file_b64)), visible_file_name=name)
                    else:
                        bot.send_message(u["telegram_id"], f"✅ {cmd} ({r['computer_hash'][:10]}):\n{res}")
                except Exception as e:
                    print("Send error:", e)
            cur.execute("DELETE FROM commands WHERE id=?", (cid,))
            conn.commit()
        conn.close()
        time.sleep(3)

# ---------- ЗАПУСК ----------
def run_flask(): app.run(host="0.0.0.0", port=API_PORT)
def run_bot(): bot.infinity_polling()

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=poll_results, daemon=True).start()
    print("✅ Server started on port", API_PORT)
    print("✅ Demo server started on port 5000")
    app.run(host="0.0.0.0", port=5000)
    run_bot()
