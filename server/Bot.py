# server/Bot.py
# Сервер: Telegram-бот + Flask API (на ВМ)
import os, io, zipfile, tempfile, threading, time, logging, uuid, base64
from flask import Flask, request, jsonify
import mysql.connector
from telebot import TeleBot, types
from dotenv import load_dotenv
import requests

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# --- Настройки из .env ---
TOKEN = os.getenv('TOKEN')
DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_NAME = os.getenv('DB_NAME', 'bot_db')
API_PORT = int(os.getenv('API_PORT', 5000))
SERVER_PUBLIC = os.getenv('SERVER_PUBLIC', 'http://158.160.164.124:5000')  # ваш публичный адрес

if not TOKEN:
    logger.error("Укажите TOKEN в .env")
    exit(1)

# --- DB helper ---
def get_db():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, autocommit=True
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        telegram_id BIGINT UNIQUE,
        username VARCHAR(100),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS computers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        computer_hash VARCHAR(128) UNIQUE,
        agent_url VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS user_computers (
        user_id INT,
        computer_id INT,
        is_active BOOLEAN DEFAULT FALSE,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, computer_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (computer_id) REFERENCES computers(id) ON DELETE CASCADE
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS activation_keys (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT,
        activation_key VARCHAR(64) UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )""")
    conn.close()
    logger.info("DB initialized")

# --- Flask API (для регистрации агента) ---
app = Flask(__name__)

@app.route('/register_computer', methods=['POST'])
def api_register_computer():
    try:
        data = request.json
        activation_key = data.get('activation_key')
        computer_hash = data.get('computer_hash')
        agent_url = data.get('agent_url')
        if not activation_key or not computer_hash or not agent_url:
            return jsonify({'success': False, 'message': 'Missing fields'}), 400

        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT user_id FROM activation_keys WHERE activation_key = %s", (activation_key,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'message': 'Invalid activation key'}), 401

        user_id = row['user_id']
        # delete key (one-time)
        cur.execute("DELETE FROM activation_keys WHERE activation_key = %s", (activation_key,))

        # ensure computer exists
        cur.execute("SELECT id FROM computers WHERE computer_hash = %s", (computer_hash,))
        c = cur.fetchone()
        if c:
            computer_id = c['id']
            cur.execute("UPDATE computers SET agent_url = %s WHERE id = %s", (agent_url, computer_id))
        else:
            cur.execute("INSERT INTO computers (computer_hash, agent_url) VALUES (%s, %s)", (computer_hash, agent_url))
            computer_id = cur.lastrowid

        # link to user if not linked
        cur.execute("SELECT 1 FROM user_computers WHERE user_id = %s AND computer_id = %s", (user_id, computer_id))
        if not cur.fetchone():
            cur.execute("INSERT INTO user_computers (user_id, computer_id, is_active) VALUES (%s, %s, TRUE)", (user_id, computer_id))
            # make only this active: set others false
            cur.execute("UPDATE user_computers SET is_active = FALSE WHERE user_id = %s AND computer_id != %s", (user_id, computer_id))
        conn.close()

        return jsonify({'success': True, 'computer_id': computer_id})
    except Exception as e:
        logger.exception("register_computer error")
        return jsonify({'success': False, 'message': str(e)}), 500

# --- Telegram bot ---
bot = TeleBot(TOKEN, parse_mode='HTML')

def create_main_markup():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📱 Устройства", "🖥️ Система", "📁 Файлы", "ℹ️ Аккаунт")
    return kb

def create_devices_markup():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Скачать агент", "📋 Мои устройства", "🔀 Сменить устройство", "🔙 Назад")
    return kb

@bot.message_handler(commands=['start','help'])
def cmd_start(m):
    bot.send_message(m.chat.id, "Добро пожаловать! Нажмите кнопку '📱 Устройства' чтобы начать.", reply_markup=create_main_markup())

@bot.message_handler(commands=['register'])
def cmd_register(m):
    # create user if not exists
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (m.chat.id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (telegram_id) VALUES (%s)", (m.chat.id,))
        user_id = cur.lastrowid
    else:
        user_id = row[0]
    # generate activation key
    activation_key = str(uuid.uuid4())
    cur.execute("INSERT INTO activation_keys (user_id, activation_key) VALUES (%s, %s)", (user_id, activation_key))
    conn.close()

    # create agent zip in-memory and send
    send_agent_zip_to_chat(m.chat.id, activation_key)

@bot.message_handler(func=lambda msg: msg.text == "📱 Устройства")
def menu_devices(m):
    if not ensure_user(m):
        return
    bot.send_message(m.chat.id, "Меню устройств:", reply_markup=create_devices_markup())

@bot.message_handler(func=lambda msg: msg.text == "➕ Скачать агент")
def download_agent_cmd(m):
    # create user if not exists and create activation key for this user
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (m.chat.id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (telegram_id) VALUES (%s)", (m.chat.id,))
        user_id = cur.lastrowid
    else:
        user_id = row[0]
    activation_key = str(uuid.uuid4())
    cur.execute("INSERT INTO activation_keys (user_id, activation_key) VALUES (%s, %s)", (user_id, activation_key))
    conn.close()
    send_agent_zip_to_chat(m.chat.id, activation_key)

def send_agent_zip_to_chat(chat_id, activation_key):
    # Build agent files (agent.py, config.json, Start Agent.bat)
    agent_py = AGENT_TEMPLATE.replace("{{SERVER_URL}}", SERVER_PUBLIC).replace("{{PLACEHOLDER_KEY}}", activation_key)
    config_json = '{"SERVER_URL": "%s", "ACTIVATION_KEY": "%s", "AGENT_PORT": 7000}' % (SERVER_PUBLIC, activation_key)
    start_bat = f"""@echo off
echo Запуск агента...
python agent.py
pause
"""
    # zip into memory
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('agent.py', agent_py)
        z.writestr('config.json', config_json)
        z.writestr('Start Agent.bat', start_bat)
    bio.seek(0)
    bot.send_document(chat_id, bio, visible_file_name='SystemManagerAgent.zip', caption='Скачайте архив, распакуйте и запустите Start Agent.bat')

def ensure_user(m):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (m.chat.id,))
    if not cur.fetchone():
        bot.send_message(m.chat.id, "Сначала зарегистрируйтесь: нажмите '➕ Скачать агент' или используйте /register")
        conn.close()
        return False
    conn.close()
    return True

# Helper: get active computer for user
def get_active_computer_for_chat(chat_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT uc.computer_id, c.agent_url FROM user_computers uc JOIN users u ON uc.user_id = u.id JOIN computers c ON uc.computer_id = c.id WHERE u.telegram_id = %s AND uc.is_active = TRUE", (chat_id,))
    r = cur.fetchone()
    conn.close()
    return r

# Handlers: system operations (simple)
@bot.message_handler(func=lambda msg: msg.text == "🖥️ Система")
def menu_system(m):
    if not ensure_user(m):
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📸 Скриншот", "💻 Инфо о системе", "🔄 Процессы", "🔙 Назад")
    bot.send_message(m.chat.id, "Выберите действие:", reply_markup=kb)

@bot.message_handler(func=lambda msg: msg.text == "📸 Скриншот")
def handle_screenshot(m):
    if not ensure_user(m):
        return
    comp = get_active_computer_for_chat(m.chat.id)
    if not comp:
        bot.send_message(m.chat.id, "Нет активного устройства. Привяжите агент и сделайте его активным.")
        return
    agent_url = comp['agent_url']
    try:
        r = requests.get(f"{agent_url}/screenshot", timeout=20)
        data = r.json()
        if data.get('success'):
            img_b = base64.b64decode(data['image'])
            bot.send_photo(m.chat.id, img_b, caption="📸 Скриншот")
        else:
            bot.send_message(m.chat.id, "Ошибка от агента: " + str(data.get('error')))
    except Exception as e:
        bot.send_message(m.chat.id, f"Ошибка связи с агентом: {e}")

@bot.message_handler(func=lambda msg: msg.text == "💻 Инфо о системе")
def handle_sysinfo(m):
    if not ensure_user(m):
        return
    comp = get_active_computer_for_chat(m.chat.id)
    if not comp:
        bot.send_message(m.chat.id, "Нет активного устройства.")
        return
    try:
        r = requests.get(f"{comp['agent_url']}/system_info", timeout=15)
        data = r.json()
        if data.get('success'):
            info = data['info']
            text = ("💻 <b>Информация:</b>\n"
                    f"OS: {info.get('os')}\n"
                    f"CPU: {info.get('cpu')}\n"
                    f"Memory(GB): {info.get('memory')}\n"
                    f"Hostname: {info.get('hostname')}\n"
                    f"IP: {info.get('ip')}\n")
            bot.send_message(m.chat.id, text)
        else:
            bot.send_message(m.chat.id, "Агент вернул ошибку: " + str(data.get('error')))
    except Exception as e:
        bot.send_message(m.chat.id, f"Ошибка связи с агентом: {e}")

@bot.message_handler(func=lambda msg: msg.text == "🔄 Процессы")
def handle_processes(m):
    if not ensure_user(m):
        return
    comp = get_active_computer_for_chat(m.chat.id)
    if not comp:
        bot.send_message(m.chat.id, "Нет активного устройства.")
        return
    try:
        r = requests.get(f"{comp['agent_url']}/processes", timeout=20)
        data = r.json()
        if data.get('success'):
            procs = data['processes'][:30]
            out = "🔄 Процессы (топ):\n"
            for p in procs:
                out += f"PID:{p.get('pid')} {p.get('name')} CPU:{p.get('cpu_percent')}% MEM:{p.get('memory_percent')}%\n"
            bot.send_message(m.chat.id, out)
        else:
            bot.send_message(m.chat.id, "Ошибка: " + str(data.get('error')))
    except Exception as e:
        bot.send_message(m.chat.id, f"Ошибка связи с агентом: {e}")

# Devices menu: list and switch
@bot.message_handler(func=lambda msg: msg.text == "📋 Мои устройства")
def list_my_devices(m):
    if not ensure_user(m): return
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT u.id FROM users u WHERE u.telegram_id = %s", (m.chat.id,))
    user = cur.fetchone()
    if not user:
        bot.send_message(m.chat.id, "Пользователь не найден")
        conn.close()
        return
    user_id = user['id']
    cur.execute("SELECT c.id, c.computer_hash, uc.is_active, c.agent_url FROM user_computers uc JOIN computers c ON uc.computer_id = c.id WHERE uc.user_id = %s", (user_id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        bot.send_message(m.chat.id, "У вас нет привязанных устройств")
        return
    text = "📱 Ваши устройства:\n"
    for r in rows:
        text += f"ID:{r['id']} Active:{'✅' if r['is_active'] else '❌'} URL:{r['agent_url']}\nHash:{r['computer_hash']}\n\n"
    bot.send_message(m.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == "🔀 Сменить устройство")
def start_switch(m):
    if not ensure_user(m): return
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT u.id FROM users u WHERE u.telegram_id = %s", (m.chat.id,))
    u = cur.fetchone()
    if not u: conn.close(); bot.send_message(m.chat.id, "Пользователь не найден"); return
    cur.execute("SELECT c.id, c.computer_hash, uc.is_active FROM user_computers uc JOIN computers c ON uc.computer_id = c.id WHERE uc.user_id = %s", (u['id'],))
    rows = cur.fetchall(); conn.close()
    if not rows: bot.send_message(m.chat.id, "Нет устройств"); return
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"{'✅' if r['is_active'] else '❌'} ID:{r['id']}", callback_data=f"switch:{r['id']}"))
    bot.send_message(m.chat.id, "Выберите устройство:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith('switch:'))
def switch_callback(call):
    device_id = int(call.data.split(':',1)[1])
    chat_id = call.message.chat.id
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (chat_id,))
    u = cur.fetchone()
    if not u: conn.close(); bot.answer_callback_query(call.id, "Пользователь не найден"); return
    user_id = u['id']
    cur.execute("UPDATE user_computers SET is_active = FALSE WHERE user_id = %s", (user_id,))
    cur.execute("UPDATE user_computers SET is_active = TRUE WHERE user_id = %s AND computer_id = %s", (user_id, device_id))
    conn.close()
    bot.answer_callback_query(call.id, "Устройство переключено")
    bot.edit_message_text("Устройство выбрано", chat_id, call.message.message_id)

# --- Agent template: максимально простой WIndows-агент (вставится в ZIP) ---
AGENT_TEMPLATE = r'''# agent.py (auto-generated)
import os, json, socket, base64, io
from flask import Flask, jsonify, request
import platform, psutil, uuid
from PIL import ImageGrab

app = Flask(__name__)
# config is hard-coded here by server when generating ZIP
SERVER_URL = "{{SERVER_URL}}"
ACTIVATION_KEY = "{{PLACEHOLDER_KEY}}"
AGENT_PORT = 7000

def generate_computer_hash():
    try:
        parts = [
            platform.node(),
            str(psutil.virtual_memory().total),
            str(uuid.getnode())
        ]
        return "_".join(parts)
    except:
        return str(uuid.uuid4())

COMPUTER_HASH = generate_computer_hash()

def register_to_server():
    try:
        host = socket.gethostbyname(socket.gethostname())
        agent_url = f"http://{host}:{AGENT_PORT}"
        payload = {
            "activation_key": ACTIVATION_KEY,
            "computer_hash": COMPUTER_HASH,
            "agent_url": agent_url
        }
        import requests
        r = requests.post(f"{SERVER_URL}/register_computer", json=payload, timeout=15)
        print("Register response:", r.text)
    except Exception as e:
        print("Register error:", e)

@app.route('/screenshot', methods=['GET'])
def screenshot():
    try:
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        data = base64.b64encode(buf.getvalue()).decode()
        return jsonify({'success': True, 'image': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/system_info', methods=['GET'])
def system_info():
    try:
        info = {
            'os': platform.platform(),
            'cpu': platform.processor(),
            'memory': psutil.virtual_memory().total // (1024**3),
            'hostname': socket.gethostname(),
            'ip': socket.gethostbyname(socket.gethostname())
        }
        return jsonify({'success': True, 'info': info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/processes', methods=['GET'])
def processes():
    try:
        out = []
        for p in psutil.process_iter(['pid','name','cpu_percent','memory_percent']):
            try:
                out.append(p.info)
            except: pass
        out = sorted(out, key=lambda x: x.get('cpu_percent',0), reverse=True)
        return jsonify({'success': True, 'processes': out})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/files', methods=['GET'])
def list_files():
    # path parameter
    path = request.args.get('path', 'C:\\\\')
    try:
        items = []
        for name in os.listdir(path):
            full = os.path.join(path, name)
            items.append({'name': name, 'is_dir': os.path.isdir(full), 'size': os.path.getsize(full) if os.path.isfile(full) else None})
        return jsonify({'success': True, 'files': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download', methods=['GET'])
def download_file():
    path = request.args.get('path')
    if not path:
        return jsonify({'success': False, 'error': 'no path'}), 400
    try:
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode()
        return jsonify({'success': True, 'file': data, 'name': os.path.basename(path)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/delete', methods=['POST'])
def delete_file():
    data = request.json or {}
    path = data.get('path')
    if not path:
        return jsonify({'success': False, 'error': 'no path'}), 400
    try:
        os.remove(path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    print("Agent starting... registering to server")
    register_to_server()
    print("Agent listening on port", AGENT_PORT)
    app.run(host='0.0.0.0', port=AGENT_PORT)
'''

# --- Run functions ---
def run_flask():
    app.run(host='0.0.0.0', port=API_PORT)

def run_bot():
    bot.infinity_polling()

if __name__ == '__main__':
    init_db()
    # start Flask in thread
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    logger.info(f"Flask started on port {API_PORT}")
    run_bot()
