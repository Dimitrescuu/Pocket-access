import telebot
from telebot import types
import mysql.connector
import bcrypt
from dotenv import load_dotenv
import logging
import time
import os
import requests
import socket
import psutil
import platform
import uuid
import hashlib
from PIL import ImageGrab
from collections import defaultdict
import base64
import re
import tempfile
import zipfile
from io import BytesIO
from flask import Flask, request, jsonify
import threading
import json

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Инициализация Flask API
app = Flask(__name__)
API_PORT = int(os.getenv('API_PORT', 5000))


class Database:
    def __init__(self):
        self.connection = None
        self.connect()

    def connect(self):
        max_retries = 5
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                self.connection = mysql.connector.connect(
                    host=os.getenv('DB_HOST', 'localhost'),
                    user=os.getenv('DB_USER', 'root'),
                    password=os.getenv('DB_PASSWORD', ''),
                    database=os.getenv('DB_NAME', 'bot_db'),
                    autocommit=True,
                    connect_timeout=10
                )
                logger.info("Успешное подключение к базе данных")
                self.initialize_database()
                return
            except mysql.connector.Error as err:
                logger.error(f"Ошибка подключения к БД (попытка {attempt + 1}/{max_retries}): {err}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)

        logger.error("Не удалось подключиться к БД после нескольких попыток")
        raise ConnectionError("Не удалось установить соединение с базой данных")

    def initialize_database(self):
        try:
            cursor = self.connection.cursor()

            # Создаем таблицу компьютеров
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS computers (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    computer_hash VARCHAR(64) NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Создаем таблицу пользователей
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    telegram_id BIGINT NULL,
                    username VARCHAR(50) NOT NULL UNIQUE,
                    password_hash VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Создаем таблицу привязок пользователей к компьютерам
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_computers (
                    user_id INT NOT NULL,
                    computer_id INT NOT NULL,
                    is_active BOOLEAN DEFAULT FALSE,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, computer_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (computer_id) REFERENCES computers(id) ON DELETE CASCADE
                )
            """)

            # Создаем таблицу сессий
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    telegram_id BIGINT NOT NULL PRIMARY KEY,
                    user_id INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    current_computer_id INT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (current_computer_id) REFERENCES computers(id) ON DELETE SET NULL
                )
            """)

            # Создаем таблицу ключей активации
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activation_keys (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    activation_key VARCHAR(36) NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            self.connection.commit()

            # Добавляем недостающие столбцы
            self.add_missing_columns()

            logger.info("Таблицы базы данных инициализированы")
        except mysql.connector.Error as err:
            logger.error(f"Ошибка инициализации БД: {err}")

    def add_missing_columns(self):
        try:
            cursor = self.connection.cursor()

            # Проверяем и добавляем current_computer_id в sessions
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'sessions' 
                AND COLUMN_NAME = 'current_computer_id'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE sessions ADD COLUMN current_computer_id INT")
                cursor.execute("""
                    ALTER TABLE sessions 
                    ADD CONSTRAINT fk_sessions_computers 
                    FOREIGN KEY (current_computer_id) 
                    REFERENCES computers(id) ON DELETE SET NULL
                """)
                logger.info("Добавлен столбец current_computer_id в sessions")

            # Проверяем и добавляем last_active in user_computers
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'user_computers' 
                AND COLUMN_NAME = 'last_active'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    ALTER TABLE user_computers 
                    ADD COLUMN last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                """)
                logger.info("Добавлен столбец last_active в user_computers")

            # Проверяем и добавляем created_at в users
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'users' 
                AND COLUMN_NAME = 'created_at'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    ALTER TABLE users 
                    ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                """)
                logger.info("Добавлен столбец created_at в users")

            self.connection.commit()
        except Exception as e:
            logger.error(f"Ошибка добавления столбцов: {e}")
            self.connection.rollback()

    def get_cursor(self):
        try:
            if not self.connection.is_connected():
                self.connect()
            return self.connection.cursor(dictionary=True)
        except mysql.connector.Error as err:
            logger.error(f"Ошибка курсора: {err}")
            self.connect()
            return self.connection.cursor(dictionary=True)

    def execute_query(self, query, params=None):
        try:
            cursor = self.get_cursor()
            cursor.execute(query, params)
            return cursor
        except mysql.connector.Error as err:
            logger.error(f"Ошибка выполнения запроса: {err}")
            logger.error(f"Запрос: {query}, Параметры: {params}")
            raise


# Инициализация базы данных
try:
    logger.info("Инициализация подключения к базе данных...")
    db = Database()
except ConnectionError as e:
    logger.error(f"Критическая ошибка: {e}")
    exit(1)

# Инициализация бота
bot_token = os.getenv('TOKEN')
if not bot_token:
    logger.error("Токен бота не найден в переменных окружения")
    exit(1)

# Создаем бота
bot = telebot.TeleBot(
    bot_token,
    num_threads=5,
    skip_pending=True,
    parse_mode='HTML'
)
logger.info("Экземпляр бота создан")

# Хранилище состояний
user_states = {}
current_paths = defaultdict(lambda: "C:\\")
file_list_cache = {}


# Генерация уникального хеша компьютера
def generate_computer_hash():
    # Используем комбинацию идентификаторов
    identifiers = []

    # 1. MAC-адрес (всех интерфейсов)
    try:
        macs = []
        for name, addrs in psutil.net_if_addrs().items():
            if name != 'lo' and not name.startswith('veth'):
                for addr in addrs:
                    if addr.family == psutil.AF_LINK:
                        mac = addr.address.replace(':', '-').upper()
                        if mac and mac != '00-00-00-00-00-00':
                            macs.append(mac)
        if macs:
            identifiers.extend(sorted(macs))
    except:
        pass

    # 2. Серийный номер системного диска
    try:
        if platform.system() == 'Windows':
            import wmi
            c = wmi.WMI()
            for disk in c.Win32_DiskDrive():
                if disk.DeviceID.startswith('PHYSICALDRIVE0'):
                    identifiers.append(disk.SerialNumber.strip())
        else:
            # Для Linux
            result = os.popen("sudo dmidecode -s system-serial-number 2>/dev/null").read().strip()
            if result and not result.startswith('O.E.M'):
                identifiers.append(result)
            else:
                # Альтернатива: UUID системы
                result = os.popen("cat /etc/machine-id 2>/dev/null").read().strip()
                if result:
                    identifiers.append(result)
    except:
        pass

    # 3. Идентификатор процессора
    try:
        identifiers.append(platform.processor())
    except:
        pass

    # 4. Идентификатор материнской платы
    try:
        if platform.system() == 'Windows':
            import wmi
            c = wmi.WMI()
            for board in c.Win32_BaseBoard():
                identifiers.append(board.SerialNumber.strip())
        else:
            # Для Linux
            result = os.popen("sudo dmidecode -s baseboard-serial-number 2>/dev/null").read().strip()
            if result and not result.startswith('O.E.M'):
                identifiers.append(result)
    except:
        pass

    # 5. Размер оперативной памяти
    try:
        identifiers.append(str(psutil.virtual_memory().total))
    except:
        pass

    # 6. Идентификатор BIOS
    try:
        if platform.system() == 'Windows':
            import wmi
            c = wmi.WMI()
            for bios in c.Win32_BIOS():
                identifiers.append(bios.SerialNumber.strip())
        else:
            # Для Linux
            result = os.popen("sudo dmidecode -s bios-serial-number 2>/dev/null").read().strip()
            if result and not result.startswith('O.E.M'):
                identifiers.append(result)
    except:
        pass

    # Если не удалось получить идентификаторы, используем UUID + случайные данные
    if not identifiers:
        unique_id = str(uuid.uuid4()) + str(os.urandom(16))
        return hashlib.sha256(unique_id.encode()).hexdigest()

    # Добавляем случайные данные для гарантии уникальности
    identifiers.append(str(uuid.uuid4()))
    identifiers.append(str(os.urandom(8)))

    # Создаем хеш из всех идентификаторов
    combined = "|".join(identifiers)
    return hashlib.sha256(combined.encode()).hexdigest()


# Функции создания клавиатур
def create_main_menu_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = ["🖥️ Система", "📁 Файлы", "📱 Устройства", "👤 Аккаунт"]
    for btn in buttons:
        markup.add(types.KeyboardButton(btn))
    return markup


def create_system_menu_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = ["📸 Скриншот", "🌐 IP адрес", "💻 Инфо о системе", "🔄 Процессы", "🔙 Назад"]
    for btn in buttons:
        markup.add(types.KeyboardButton(btn))
    return markup


def create_files_menu_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = ["📋 Список файлов", "💾 Дисковое пространство", "🔙 Назад"]
    for btn in buttons:
        markup.add(types.KeyboardButton(btn))
    return markup


def create_devices_menu_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        "➕ Добавить устройство",
        "📋 Мои устройства",
        "🔀 Сменить устройство",
        "➖ Удалить устройство",
        "⬇️ Скачать агент",
        "🔙 Назад"
    ]
    for btn in buttons:
        markup.add(types.KeyboardButton(btn))
    return markup


def create_account_menu_markup(is_authenticated):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if is_authenticated:
        buttons = ["ℹ️ Информация", "🚪 Выйти"]
    else:
        buttons = ["🔑 Войти", "📝 Регистрация"]
    for btn in buttons:
        markup.add(types.KeyboardButton(btn))
    markup.add(types.KeyboardButton("🔙 Назад"))
    return markup


def create_back_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🔙 Назад"))
    return markup


def get_computer_id(computer_hash):
    try:
        # Проверяем, существует ли компьютер
        cursor = db.execute_query(
            "SELECT id FROM computers WHERE computer_hash = %s",
            (computer_hash,)
        )
        computer = cursor.fetchone()

        if computer:
            return computer['id']

        # Создаем новый компьютер
        cursor = db.execute_query(
            "INSERT INTO computers (computer_hash) VALUES (%s)",
            (computer_hash,)
        )
        return cursor.lastrowid
    except Exception as e:
        logger.error(f"Ошибка получения ID компьютера: {e}")
        return None


def is_authenticated(telegram_id):
    try:
        cursor = db.execute_query("SELECT * FROM sessions WHERE telegram_id = %s", (telegram_id,))
        return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Ошибка проверки авторизации: {e}")
        return False


def get_user_id(telegram_id):
    try:
        cursor = db.execute_query("SELECT user_id FROM sessions WHERE telegram_id = %s", (telegram_id,))
        session = cursor.fetchone()
        return session['user_id'] if session else None
    except Exception as e:
        logger.error(f"Ошибка получения user_id: {e}")
        return None


def get_current_computer_id(telegram_id):
    try:
        cursor = db.execute_query(
            "SELECT current_computer_id FROM sessions WHERE telegram_id = %s",
            (telegram_id,)
        )
        session = cursor.fetchone()
        return session['current_computer_id'] if session and session['current_computer_id'] else None
    except Exception as e:
        logger.error(f"Ошибка получения ID компьютера: {e}")
        return None


# Обработчики сообщений
@bot.message_handler(commands=['start', 'help'])
def start(message):
    logger.info(f"Обработка команды /start для чата {message.chat.id}")
    welcome_text = (
        "👋 <b>Добро пожаловать в System Manager Bot!</b>\n\n"
        "Используйте меню ниже для навигации."
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        reply_markup=create_main_menu_markup()
    )


@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    logger.info(f"Получено сообщение: '{message.text}' от {message.chat.id}")

    # Обработка текстовых команд
    if message.text == "🔙 Назад":
        back_to_main(message)
    elif message.text == "👤 Аккаунт":
        account_menu(message)
    elif message.text == "🖥️ Система":
        system_menu(message)
    elif message.text == "📁 Файлы":
        files_menu(message)
    elif message.text == "📱 Устройства":
        devices_menu(message)
    elif message.text == "🔑 Войти":
        handle_login(message)
    elif message.text == "📝 Регистрация":
        handle_register(message)
    elif message.text == "🚪 Выйти":
        handle_logout(message)
    elif message.text == "ℹ️ Информация":
        account_info(message)
    elif message.text == "⬇️ Скачать агент":
        download_agent(message)

    # Обработка команд системы
    elif message.text == "📸 Скриншот":
        take_screenshot(message)
    elif message.text == "🌐 IP адрес":
        get_ip_address(message)
    elif message.text == "💻 Инфо о системе":
        system_info(message)
    elif message.text == "🔄 Процессы":
        list_processes(message)

    # Обработка команд файлов
    elif message.text == "📋 Список файлов":
        list_files(message)
    elif message.text == "💾 Дисковое пространство":
        disk_space(message)

    # Обработка команд устройств
    elif message.text == "➕ Добавить устройство":
        add_device_start(message)
    elif message.text == "📋 Мои устройства":
        list_devices(message)
    elif message.text == "🔀 Сменить устройство":
        switch_device_start(message)
    elif message.text == "➖ Удалить устройство":
        delete_device_start(message)

    # Обработка состояний
    elif message.chat.id in user_states and user_states[message.chat.id].get('state') == 'ADD_DEVICE':
        add_device_finish(message)
    elif message.chat.id in user_states and user_states[message.chat.id].get('state') == 'DELETE_DEVICE':
        delete_device_finish(message)
    else:
        bot.send_message(
            message.chat.id,
            "Неизвестная команда. Используйте меню.",
            reply_markup=create_main_menu_markup()
        )


def back_to_main(message):
    logger.info(f"Возврат в главное меню для чата {message.chat.id}")
    bot.send_message(
        message.chat.id,
        "<b>Главное меню:</b>",
        reply_markup=create_main_menu_markup()
    )


def account_menu(message):
    logger.info(f"Открытие меню аккаунта для чата {message.chat.id}")
    auth = is_authenticated(message.chat.id)
    bot.send_message(
        message.chat.id,
        "<b>Меню аккаунта:</b>",
        reply_markup=create_account_menu_markup(auth)
    )


def system_menu(message):
    logger.info(f"Открытие меню системы для чата {message.chat.id}")
    bot.send_message(
        message.chat.id,
        "<b>Меню системы:</b>",
        reply_markup=create_system_menu_markup()
    )


def files_menu(message):
    logger.info(f"Открытие меню файлов для чата {message.chat.id}")
    if not is_authenticated(message.chat.id):
        return not_logged_in(message)
    bot.send_message(
        message.chat.id,
        "<b>Меню файлов:</b>",
        reply_markup=create_files_menu_markup()
    )


def devices_menu(message):
    logger.info(f"Открытие меню устройств для чата {message.chat.id}")
    if not is_authenticated(message.chat.id):
        return not_logged_in(message)
    bot.send_message(
        message.chat.id,
        "<b>Меню устройств:</b>",
        reply_markup=create_devices_menu_markup()
    )


def not_logged_in(message):
    bot.send_message(
        message.chat.id,
        "🔒 <b>Вы не авторизованы. Пожалуйста, войдите в систему.</b>",
        reply_markup=create_account_menu_markup(False)
    )


# Реализация функций аккаунта
def handle_login(message):
    logger.info(f"Начало процесса входа для чата {message.chat.id}")
    msg = bot.send_message(
        message.chat.id,
        "Введите имя пользователя:",
        reply_markup=create_back_markup()
    )
    if msg:
        user_states[message.chat.id] = {'state': 'LOGIN_USERNAME'}
        bot.register_next_step_handler(msg, process_login_username)


def process_login_username(message):
    logger.info(f"Обработка логина для чата {message.chat.id}")

    if message.text == "🔙 Назад":
        user_states.pop(message.chat.id, None)
        return account_menu(message)

    username = message.text.strip()
    if not username:
        msg = bot.send_message(
            message.chat.id,
            "Имя пользователя не может быть пустым!",
            reply_markup=create_back_markup()
        )
        if msg:
            bot.register_next_step_handler(msg, process_login_username)
        return

    user_states[message.chat.id] = {'state': 'LOGIN_PASSWORD', 'username': username}
    msg = bot.send_message(
        message.chat.id,
        "Введите пароль:",
        reply_markup=create_back_markup()
    )
    if msg:
        bot.register_next_step_handler(msg, process_login_password)


def process_login_password(message):
    logger.info(f"Обработка пароля для чата {message.chat.id}")

    if message.text == "🔙 Назад":
        user_states.pop(message.chat.id, None)
        return handle_login(message)

    user_data = user_states.get(message.chat.id, {})
    if user_data.get('state') != 'LOGIN_PASSWORD':
        return

    username = user_data.get('username')
    password = message.text.strip()
    user_states.pop(message.chat.id, None)

    try:
        cursor = db.execute_query(
            "SELECT id, username, password_hash FROM users WHERE username = %s",
            (username,)
        )
        user = cursor.fetchone()

        if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            # Получаем активный компьютер пользователя
            cursor = db.execute_query(
                "SELECT computer_id FROM user_computers WHERE user_id = %s AND is_active = TRUE",
                (user['id'],)
            )
            active_computer = cursor.fetchone()
            computer_id = active_computer['computer_id'] if active_computer else None

            # Создаем сессию
            db.execute_query(
                "REPLACE INTO sessions (telegram_id, user_id, current_computer_id) VALUES (%s, %s, %s)",
                (message.chat.id, user['id'], computer_id)
            )
            bot.send_message(
                message.chat.id,
                f"✅ <b>Добро пожаловать, {user['username']}!</b>",
                reply_markup=create_main_menu_markup()
            )
        else:
            bot.send_message(
                message.chat.id,
                "❌ <b>Неверные учетные данные</b>",
                reply_markup=create_account_menu_markup(False)
            )
    except Exception as e:
        logger.error(f"Ошибка входа: {e}")
        bot.send_message(
            message.chat.id,
            "⛔ <b>Ошибка сервера, попробуйте позже</b>",
            reply_markup=create_account_menu_markup(False)
        )


def handle_register(message):
    logger.info(f"Начало регистрации для чата {message.chat.id}")

    # Проверяем, не зарегистрирован ли уже пользователь
    try:
        cursor = db.execute_query(
            "SELECT id FROM users WHERE telegram_id = %s",
            (message.chat.id,)
        )
        if cursor.fetchone():
            bot.send_message(
                message.chat.id,
                "❌ Вы уже зарегистрированы! Чтобы добавить новое устройство, используйте меню 'Устройства' -> '➕ Добавить устройство'.",
                reply_markup=create_main_menu_markup()
            )
            return
    except Exception as e:
        logger.error(f"Ошибка проверки регистрации: {e}")
        bot.send_message(
            message.chat.id,
            "⛔ Ошибка проверки, попробуйте позже.",
            reply_markup=create_back_markup()
        )
        return

    # Генерируем уникальный хеш компьютера
    computer_hash = generate_computer_hash()
    logger.info(f"Сгенерирован хеш компьютера: {computer_hash}")

    # Проверяем, не зарегистрирован ли уже этот компьютер
    cursor = db.execute_query(
        "SELECT id FROM computers WHERE computer_hash = %s",
        (computer_hash,)
    )
    computer = cursor.fetchone()

    if computer:
        cursor = db.execute_query(
            "SELECT user_id FROM user_computers WHERE computer_id = %s",
            (computer['id'],)
        )
        if cursor.fetchone():
            bot.send_message(
                message.chat.id,
                "❌ Этот компьютер уже привязан к другому аккаунту!",
                reply_markup=create_account_menu_markup(False)
            )
            return

    user_states[message.chat.id] = {'state': 'REGISTER', 'computer_hash': computer_hash}

    msg = bot.send_message(
        message.chat.id,
        "Придумайте имя пользователя (мин. 4 символа):",
        reply_markup=create_back_markup()
    )
    if msg:
        bot.register_next_step_handler(msg, process_register_username)


def process_register_username(message):
    logger.info(f"Обработка имени пользователя при регистрации для чата {message.chat.id}")

    if message.text == "🔙 Назад":
        user_states.pop(message.chat.id, None)
        return account_menu(message)

    username = message.text.strip()
    if len(username) < 4:
        bot.send_message(
            message.chat.id,
            "❌ Имя должно быть не менее 4 символов!",
            reply_markup=create_back_markup()
        )
        return handle_register(message)

    try:
        cursor = db.execute_query(
            "SELECT id FROM users WHERE username = %s",
            (username,)
        )
        if cursor.fetchone():
            bot.send_message(
                message.chat.id,
                "❌ Это имя уже занято",
                reply_markup=create_back_markup()
            )
            return handle_register(message)
    except Exception as e:
        logger.error(f"Ошибка проверки имени: {e}")
        bot.send_message(
            message.chat.id,
            "⛔ Ошибка проверки",
            reply_markup=create_back_markup()
        )
        return handle_register(message)

    user_states[message.chat.id] = {
        'state': 'REGISTER_PASSWORD',
        'username': username,
        'computer_hash': user_states[message.chat.id]['computer_hash']
    }
    msg = bot.send_message(
        message.chat.id,
        "Придумайте пароль (мин. 6 символов):",
        reply_markup=create_back_markup()
    )
    if msg:
        bot.register_next_step_handler(msg, process_register_password)


def process_register_password(message):
    logger.info(f"Обработка пароля при регистрации для чата {message.chat.id}")

    if message.text == "🔙 Назад":
        user_states.pop(message.chat.id, None)
        return handle_register(message)

    user_data = user_states.get(message.chat.id, {})
    if user_data.get('state') != 'REGISTER_PASSWORD':
        return

    username = user_data.get('username')
    password = message.text.strip()
    computer_hash = user_data.get('computer_hash')
    user_states.pop(message.chat.id, None)

    if len(password) < 6:
        bot.send_message(
            message.chat.id,
            "❌ Пароль должен быть не менее 6 символов",
            reply_markup=create_back_markup()
        )
        return handle_register(message)

    try:
        # Проверяем существование telegram_id
        cursor = db.execute_query(
            "SELECT id FROM users WHERE telegram_id = %s",
            (message.chat.id,)
        )
        if cursor.fetchone():
            bot.send_message(
                message.chat.id,
                "❌ Этот Telegram ID уже зарегистрирован!",
                reply_markup=create_back_markup()
            )
            return

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        # Создаем пользователя без telegram_id
        cursor = db.execute_query(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
            (username, hashed)
        )
        user_id = cursor.lastrowid

        # Обновляем telegram_id для существующей записи
        db.execute_query(
            "UPDATE users SET telegram_id = %s WHERE id = %s",
            (message.chat.id, user_id)
        )

        # Регистрируем компьютер
        computer_id = get_computer_id(computer_hash)

        # Привязываем компьютер к пользователю как активный
        db.execute_query(
            "INSERT INTO user_computers (user_id, computer_id, is_active) VALUES (%s, %s, TRUE)",
            (user_id, computer_id)
        )

        # Создаем сессию
        db.execute_query(
            "REPLACE INTO sessions (telegram_id, user_id, current_computer_id) VALUES (%s, %s, %s)",
            (message.chat.id, user_id, computer_id)
        )

        bot.send_message(
            message.chat.id,
            f"✅ <b>Регистрация успешна! Добро пожаловать, {username}!</b>\n"
            f"💻 Ваш компьютер успешно привязан к аккаунту.",
            reply_markup=create_main_menu_markup()
        )
    except Exception as e:
        logger.error(f"Ошибка регистрации: {e}")
        bot.send_message(
            message.chat.id,
            "⛔ <b>Ошибка регистрации</b>",
            reply_markup=create_account_menu_markup(False)
        )


def handle_logout(message):
    logger.info(f"Выход из системы для чата {message.chat.id}")

    if not is_authenticated(message.chat.id):
        return bot.send_message(
            message.chat.id,
            "❌ <b>Вы не авторизованы</b>",
            reply_markup=create_account_menu_markup(False)
        )

    try:
        db.execute_query(
            "DELETE FROM sessions WHERE telegram_id = %s",
            (message.chat.id,)
        )
        bot.send_message(
            message.chat.id,
            "✅ <b>Вы успешно вышли из системы</b>",
            reply_markup=create_main_menu_markup()
        )
    except Exception as e:
        logger.error(f"Ошибка выхода: {e}")
        bot.send_message(
            message.chat.id,
            "⛔ <b>Ошибка при выходе из системы</b>",
            reply_markup=create_main_menu_markup()
        )


def account_info(message):
    logger.info(f"Запрос информации аккаунта для чата {message.chat.id}")

    if not is_authenticated(message.chat.id):
        return bot.send_message(
            message.chat.id,
            "❌ <b>Вы не авторизованы</b>",
            reply_markup=create_account_menu_markup(False)
        )

    try:
        cursor = db.execute_query(
            "SELECT user_id, current_computer_id FROM sessions WHERE telegram_id = %s",
            (message.chat.id,)
        )
        session = cursor.fetchone()
        if not session:
            return bot.send_message(
                message.chat.id,
                "⛔ <b>Сессия не найдена</b>",
                reply_markup=create_account_menu_markup(True)
            )

        user_id = session['user_id']
        current_computer_id = session['current_computer_id']

        # Информация о пользователе
        cursor = db.execute_query(
            "SELECT username, created_at FROM users WHERE id = %s",
            (user_id,)
        )
        user = cursor.fetchone()

        if not user:
            return bot.send_message(
                message.chat.id,
                "⛔ <b>Пользователь не найден</b>",
                reply_markup=create_account_menu_markup(True)
            )

        # Информация о текущем компьютере
        computer_info = "❌ Не установлен"
        if current_computer_id:
            cursor = db.execute_query(
                "SELECT id, created_at FROM computers WHERE id = %s",
                (current_computer_id,)
            )
            computer = cursor.fetchone()
            if computer:
                computer_info = f"ID: {computer['id']}, Зарегистрирован: {computer['created_at']}"

        # Количество привязанных компьютеров
        cursor = db.execute_query(
            "SELECT COUNT(*) AS count FROM user_computers WHERE user_id = %s",
            (user_id,)
        )
        computer_count = cursor.fetchone()['count']

        info_text = (
            f"👤 <b>Информация об аккаунте</b>\n\n"
            f"▪️ Имя пользователя: {user['username']}\n"
            f"▪️ ID: {user_id}\n"
            f"▪️ Дата регистрации: {user['created_at']}\n"
            f"▪️ Привязано компьютеров: {computer_count}\n"
            f"▪️ Текущий компьютер: {computer_info}\n"
            f"▪️ Статус: Авторизован"
        )
        bot.send_message(
            message.chat.id,
            info_text,
            reply_markup=create_account_menu_markup(True)
        )
    except Exception as e:
        logger.error(f"Ошибка получения информации: {e}")
        bot.send_message(
            message.chat.id,
            "⛔ <b>Ошибка получения информации</b>",
            reply_markup=create_account_menu_markup(True)
        )


# Системные функции
def take_screenshot(message):
    logger.info(f"Запрос скриншота от {message.chat.id}")

    # Проверяем, установлен ли активный компьютер
    computer_id = get_current_computer_id(message.chat.id)
    if not computer_id:
        bot.send_message(
            message.chat.id,
            "❌ Сначала выберите активное устройство в меню 'Устройства'",
            reply_markup=create_devices_menu_markup()
        )
        return

    try:
        bot.send_chat_action(message.chat.id, 'upload_photo')
        screenshot = ImageGrab.grab()
        screenshot_path = os.path.join(os.getenv("TEMP", "/tmp"), "screenshot.jpg")
        screenshot.save(screenshot_path, "JPEG")

        with open(screenshot_path, 'rb') as photo:
            bot.send_photo(message.chat.id, photo, caption="📸 Скриншот активного устройства")

        os.remove(screenshot_path)
    except Exception as e:
        logger.error(f"Ошибка создания скриншота: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось создать скриншот")


def get_ip_address(message):
    logger.info(f"Запрос IP адреса от {message.chat.id}")
    try:
        # Внешний IP
        external_ip = requests.get('https://api.ipify.org').text

        # Внутренний IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        internal_ip = s.getsockname()[0]
        s.close()

        response = (
            f"🌐 <b>Ваши IP адреса:</b>\n\n"
            f"▪️ Внешний: <code>{external_ip}</code>\n"
            f"▪️ Внутренний: <code>{internal_ip}</code>"
        )
        bot.send_message(message.chat.id, response)
    except Exception as e:
        logger.error(f"Ошибка получения IP: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить IP адрес")


def system_info(message):
    logger.info(f"Запрос информации о системе от {message.chat.id}")

    # Проверяем, установлен ли активный компьютер
    computer_id = get_current_computer_id(message.chat.id)
    if not computer_id:
        bot.send_message(
            message.chat.id,
            "❌ Сначала выберите активное устройство в меню 'Устройства'",
            reply_markup=create_devices_menu_markup()
        )
        return

    try:
        uname = platform.uname()
        memory = psutil.virtual_memory()
        info = f"""
💻 <b>Информация о системе активного устройства:</b>

▪️ <b>ОС</b>: {uname.system} {uname.release}
▪️ <b>Версия</b>: {uname.version}
▪️ <b>Процессор</b>: {uname.processor}
▪️ <b>Архитектура</b>: {platform.architecture()[0]}
▪️ <b>Память</b>: {memory.total // (1024 ** 3)} GB
▪️ <b>Загрузка CPU</b>: {psutil.cpu_percent()}%
▪️ <b>Использование памяти</b>: {memory.percent}%
▪️ <b>Пользователь</b>: {os.getlogin()}
"""
        bot.send_message(message.chat.id, info)
    except Exception as e:
        logger.error(f"Ошибка получения информации о системе: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить информацию о системе")


def list_processes(message):
    logger.info(f"Запрос списка процессов от {message.chat.id}")

    # Проверяем, установлен ли активный компьютер
    computer_id = get_current_computer_id(message.chat.id)
    if not computer_id:
        bot.send_message(
            message.chat.id,
            "❌ Сначала выберите активное устройство в меню 'Устройства'",
            reply_markup=create_devices_menu_markup()
        )
        return

    try:
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                if proc.info['cpu_percent'] > 0 or proc.info['memory_percent'] > 0:
                    processes.append(proc.info)
            except:
                continue

        # Сортируем по использованию CPU
        processes = sorted(processes, key=lambda p: p['cpu_percent'], reverse=True)[:15]

        response = "🔄 <b>Самые ресурсоемкие процессы на активном устройстве:</b>\n\n"
        for proc in processes:
            response += f"▪️ PID: {proc['pid']} | {proc['name']}\n"
            response += f"CPU: {proc['cpu_percent']:.1f}% | MEM: {proc['memory_percent']:.1f}%\n\n"

        bot.send_message(message.chat.id, response)
    except Exception as e:
        logger.error(f"Ошибка получения списка процессов: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить список процессов")


# Файловые функции
def list_files(message):
    logger.info(f"Запрос списка файлов от {message.chat.id}")

    # Проверяем, установлен ли активный компьютер
    computer_id = get_current_computer_id(message.chat.id)
    if not computer_id:
        bot.send_message(
            message.chat.id,
            "❌ Сначала выберите активное устройство в меню 'Устройства'",
            reply_markup=create_devices_menu_markup()
        )
        return

    try:
        # Получаем текущий путь для пользователя
        current_path = current_paths.get(message.chat.id, "C:\\")

        # Нормализуем путь (заменяем слеши)
        current_path = os.path.normpath(current_path)

        # Проверяем существование пути
        if not os.path.exists(current_path):
            current_path = "C:\\"  # Возвращаемся к корню диска C:\
            current_paths[message.chat.id] = current_path

        # Получаем список элементов в директории
        try:
            items = os.listdir(current_path)
        except PermissionError:
            bot.send_message(message.chat.id, "❌ Нет доступа к папке")
            return
        except FileNotFoundError:
            bot.send_message(message.chat.id, "❌ Папка не найдена")
            return

        if not items:
            bot.send_message(message.chat.id, "📂 Папка пуста")
            return

        # Сохраняем список файлов в кэше
        file_list_cache[message.chat.id] = {
            'path': current_path,
            'items': items
        }

        # Формируем клавиатуру
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = []

        # Кнопка для возврата в родительскую папку (только если это не корень диска)
        parent_path = os.path.dirname(current_path)
        if parent_path != current_path:  # Проверяем, что это не корневая директория
            buttons.append(types.InlineKeyboardButton("📂 ..", callback_data="folder_up"))

        # Добавляем папки и файлы
        for idx, item in enumerate(items):
            full_path = os.path.join(current_path, item)

            # Пропускаем скрытые файлы/папки
            if item.startswith('.'):
                continue

            if os.path.isdir(full_path):
                # Для папок используем индекс
                buttons.append(types.InlineKeyboardButton(f"📁 {item}", callback_data=f"folder:{idx}"))
            else:
                # Для файлов используем индекс
                buttons.append(types.InlineKeyboardButton(f"📄 {item}", callback_data=f"file:{idx}"))

        # Разбиваем кнопки на строки
        for i in range(0, len(buttons), 2):
            row = buttons[i:i + 2]
            if row:  # Проверяем, что row не пустой
                markup.add(*row)

        # Добавляем кнопку обновления
        markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh"))

        bot.send_message(
            message.chat.id,
            f"📂 Содержимое папки активного устройства: <code>{current_path}</code>",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Ошибка получения списка файлов: {e}", exc_info=True)
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")


def disk_space(message):
    logger.info(f"Запрос дискового пространства от {message.chat.id}")

    # Проверяем, установлен ли активный компьютер
    computer_id = get_current_computer_id(message.chat.id)
    if not computer_id:
        bot.send_message(
            message.chat.id,
            "❌ Сначала выберите активное устройство в меню 'Устройства'",
            reply_markup=create_devices_menu_markup()
        )
        return

    try:
        disks = []
        for partition in psutil.disk_partitions():
            if 'fixed' in partition.opts or 'rw' in partition.opts:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    disks.append(
                        f"📀 <b>{partition.device}</b> ({partition.mountpoint})\n"
                        f"▪️ Всего: {usage.total // (1024 ** 3)} GB\n"
                        f"▪️ Использовано: {usage.percent}%\n"
                        f"▪️ Свободно: {usage.free // (1024 ** 3)} GB\n"
                    )
                except:
                    continue

        response = "💾 <b>Дисковое пространство активного устройства:</b>\n\n" + "\n".join(disks)
        bot.send_message(message.chat.id, response)
    except Exception as e:
        logger.error(f"Ошибка получения информации о дисках: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить информацию о дисках")


@bot.callback_query_handler(func=lambda call: call.data == "folder_up")
def handle_folder_up(call):
    chat_id = call.message.chat.id
    if chat_id not in file_list_cache:
        bot.answer_callback_query(call.id, "❌ Кэш устарел")
        return

    current_path = file_list_cache[chat_id]['path']
    parent_path = os.path.dirname(current_path)

    if parent_path == current_path:
        bot.answer_callback_query(call.id, "❌ Это корневая папка")
        return

    current_paths[chat_id] = parent_path
    list_files_from_cache(call.message)


@bot.callback_query_handler(func=lambda call: call.data.startswith('folder:'))
def handle_folder_callback(call):
    chat_id = call.message.chat.id
    if chat_id not in file_list_cache:
        bot.answer_callback_query(call.id, "❌ Кэш устарел")
        return

    try:
        folder_idx = int(call.data.split(':')[1])
        current_path = file_list_cache[chat_id]['path']
        items = file_list_cache[chat_id]['items']

        if folder_idx < 0 or folder_idx >= len(items):
            bot.answer_callback_query(call.id, "❌ Неверный индекс папки")
            return

        folder_name = items[folder_idx]
        new_path = os.path.join(current_path, folder_name)

        if not os.path.isdir(new_path):
            bot.answer_callback_query(call.id, "❌ Это не папка")
            return

        current_paths[chat_id] = new_path
        list_files_from_cache(call.message)

    except Exception as e:
        logger.error(f"Ошибка обработки папки: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Ошибка при открытии папки")


@bot.callback_query_handler(func=lambda call: call.data == 'refresh')
def handle_refresh_callback(call):
    list_files_from_cache(call.message)


def list_files_from_cache(message):
    chat_id = message.chat.id
    try:
        # Получаем текущий путь
        current_path = current_paths.get(chat_id, "C:\\")

        # Обновляем список файлов
        try:
            items = os.listdir(current_path)
        except Exception as e:
            logger.error(f"Ошибка обновления списка: {e}")
            bot.answer_callback_query(message.id, "❌ Ошибка обновления")
            return

        # Обновляем кэш
        file_list_cache[chat_id] = {
            'path': current_path,
            'items': items
        }

        # Формируем новую клавиатуру
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = []

        parent_path = os.path.dirname(current_path)
        if parent_path != current_path:
            buttons.append(types.InlineKeyboardButton("📂 ..", callback_data="folder_up"))

        for idx, item in enumerate(items):
            full_path = os.path.join(current_path, item)
            if item.startswith('.'):
                continue

            if os.path.isdir(full_path):
                buttons.append(types.InlineKeyboardButton(f"📁 {item}", callback_data=f"folder:{idx}"))
            else:
                buttons.append(types.InlineKeyboardButton(f"📄 {item}", callback_data=f"file:{idx}"))

        for i in range(0, len(buttons), 2):
            row = buttons[i:i + 2]
            if row:
                markup.add(*row)

        markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh"))

        bot.edit_message_text(
            f"📂 Содержимое папки активного устройства: <code>{current_path}</code>",
            chat_id,
            message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Ошибка обновления списка файлов: {e}", exc_info=True)
        bot.answer_callback_query(message.id, "❌ Ошибка обновления")


@bot.callback_query_handler(func=lambda call: call.data.startswith('file:'))
def handle_file_callback(call):
    chat_id = call.message.chat.id
    if chat_id not in file_list_cache:
        bot.answer_callback_query(call.id, "❌ Кэш устарел")
        return

    try:
        file_idx = int(call.data.split(':')[1])
        current_path = file_list_cache[chat_id]['path']
        items = file_list_cache[chat_id]['items']

        if file_idx < 0 or file_idx >= len(items):
            bot.answer_callback_query(call.id, "❌ Неверный индекс файла")
            return

        file_name = items[file_idx]
        file_path = os.path.join(current_path, file_name)

        if not os.path.isfile(file_path):
            bot.answer_callback_query(call.id, "❌ Файл не найден")
            return

        # Создаем меню действий для файла
        encoded_idx = base64.b64encode(str(file_idx).encode()).decode()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("⏬ Скачать", callback_data=f"download:{encoded_idx}"),
            types.InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete:{encoded_idx}")
        )
        markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_list"))

        bot.edit_message_text(
            f"📄 Файл активного устройства: <code>{file_name}</code>\nВыберите действие:",
            chat_id,
            call.message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Ошибка обработки файла: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Ошибка при выборе файла")


@bot.callback_query_handler(func=lambda call: call.data == 'back_to_list')
def handle_back_callback(call):
    list_files_from_cache(call.message)


@bot.callback_query_handler(func=lambda call: call.data.startswith('download:'))
def handle_download_callback(call):
    chat_id = call.message.chat.id
    if chat_id not in file_list_cache:
        bot.answer_callback_query(call.id, "❌ Кэш устарел")
        return

    try:
        encoded_idx = call.data.split(':', 1)[1]
        file_idx = int(base64.b64decode(encoded_idx.encode()).decode())

        current_path = file_list_cache[chat_id]['path']
        items = file_list_cache[chat_id]['items']

        if file_idx < 0 or file_idx >= len(items):
            bot.answer_callback_query(call.id, "❌ Неверный индекс файла")
            return

        file_name = items[file_idx]
        file_path = os.path.join(current_path, file_name)

        if not os.path.isfile(file_path):
            bot.answer_callback_query(call.id, "❌ Файл не найден")
            return

        bot.answer_callback_query(call.id, "⏬ Начинаем загрузку...")
        with open(file_path, 'rb') as file:
            bot.send_document(
                chat_id,
                file,
                caption=f"📥 Файл активного устройства: {file_name}"
            )
    except Exception as e:
        logger.error(f"Ошибка загрузки файла: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Ошибка при загрузке файла")


@bot.callback_query_handler(func=lambda call: call.data.startswith('delete:'))
def handle_delete_callback(call):
    chat_id = call.message.chat.id
    if chat_id not in file_list_cache:
        bot.answer_callback_query(call.id, "❌ Кэш устарел")
        return

    try:
        encoded_idx = call.data.split(':', 1)[1]
        file_idx = int(base64.b64decode(encoded_idx.encode()).decode())

        current_path = file_list_cache[chat_id]['path']
        items = file_list_cache[chat_id]['items']

        if file_idx < 0 or file_idx >= len(items):
            bot.answer_callback_query(call.id, "❌ Неверный индекс файла")
            return

        file_name = items[file_idx]
        file_path = os.path.join(current_path, file_name)

        if not os.path.isfile(file_path):
            bot.answer_callback_query(call.id, "❌ Файл не найден")
            return

        os.remove(file_path)
        bot.answer_callback_query(call.id, "✅ Файл успешно удалён")
        # Возвращаемся к списку файлов
        handle_back_callback(call)
    except Exception as e:
        logger.error(f"Ошибка удаления файла: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Ошибка при удалении файла")


# Функции устройств
def add_device_start(message):
    logger.info(f"Начало добавления устройства от {message.chat.id}")
    if not is_authenticated(message.chat.id):
        return not_logged_in(message)

    # Генерируем уникальный хеш компьютера
    computer_hash = generate_computer_hash()
    logger.info(f"Сгенерирован хеш компьютера: {computer_hash}")

    user_id = get_user_id(message.chat.id)
    if not user_id:
        bot.send_message(message.chat.id, "❌ Ошибка авторизации")
        return

    try:
        # Проверяем существование компьютера
        cursor = db.execute_query(
            "SELECT id FROM computers WHERE computer_hash = %s",
            (computer_hash,)
        )
        computer = cursor.fetchone()

        computer_id = None
        if computer:
            computer_id = computer['id']
            # Проверяем, привязан ли компьютер к ТЕКУЩЕМУ пользователю
            cursor = db.execute_query(
                "SELECT 1 FROM user_computers WHERE computer_id = %s AND user_id = %s",
                (computer_id, user_id)
            )
            if cursor.fetchone():
                bot.send_message(
                    message.chat.id,
                    "ℹ️ Это устройство уже привязано к вашему аккаунту.",
                    reply_markup=create_devices_menu_markup()
                )
                return
            else:
                # Проверяем, привязан ли компьютер к ДРУГОМУ пользователю
                cursor = db.execute_query(
                    "SELECT 1 FROM user_computers WHERE computer_id = %s AND user_id != %s",
                    (computer_id, user_id)
                )
                if cursor.fetchone():
                    bot.send_message(
                        message.chat.id,
                        "❌ Этот компьютер уже привязан к другому аккаунту!",
                        reply_markup=create_devices_menu_markup()
                    )
                    return
        else:
            # Создаем новый компьютер
            cursor = db.execute_query(
                "INSERT INTO computers (computer_hash) VALUES (%s)",
                (computer_hash,)
            )
            computer_id = cursor.lastrowid

        # Привязываем компьютер к пользователю
        db.execute_query(
            "INSERT INTO user_computers (user_id, computer_id) VALUES (%s, %s)",
            (user_id, computer_id)
        )

        # Если это первое устройство пользователя, делаем его активным
        cursor = db.execute_query(
            "SELECT COUNT(*) AS count FROM user_computers WHERE user_id = %s",
            (user_id,)
        )
        count = cursor.fetchone()['count']

        if count == 1:
            db.execute_query(
                "UPDATE user_computers SET is_active = TRUE WHERE user_id = %s AND computer_id = %s",
                (user_id, computer_id)
            )
            db.execute_query(
                "UPDATE sessions SET current_computer_id = %s WHERE telegram_id = %s",
                (computer_id, message.chat.id)
            )

        bot.send_message(
            message.chat.id,
            f"✅ Компьютер успешно привязан к вашему аккаунту!\n"
            f"🔑 Хеш устройства: <code>{computer_hash}</code>",
            reply_markup=create_devices_menu_markup()
        )
    except Exception as e:
        logger.error(f"Ошибка привязки устройства: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось привязать устройство")


def list_devices(message):
    logger.info(f"Запрос списка устройств от {message.chat.id}")
    if not is_authenticated(message.chat.id):
        return not_logged_in(message)

    user_id = get_user_id(message.chat.id)
    if not user_id:
        bot.send_message(message.chat.id, "❌ Ошибка авторизации")
        return

    try:
        cursor = db.execute_query(
            "SELECT c.id, c.computer_hash, c.created_at, uc.is_active, uc.last_active "
            "FROM user_computers uc "
            "JOIN computers c ON uc.computer_id = c.id "
            "WHERE uc.user_id = %s",
            (user_id,)
        )
        devices = cursor.fetchall()

        if not devices:
            bot.send_message(message.chat.id, "📱 У вас нет привязанных устройств")
            return

        response = "📱 <b>Ваши устройства:</b>\n\n"
        for device in devices:
            status = "✅ Активно" if device['is_active'] else "❌ Не активно"
            response += (
                f"▪️ <b>ID устройства</b>: {device['id']}\n"
                f"🔑 Хеш: <code>{device['computer_hash']}</code>\n"
                f"📅 Дата регистрации: {device['created_at']}\n"
                f"🕒 Последняя активность: {device['last_active']}\n"
                f"🔌 Статус: {status}\n\n"
            )

        bot.send_message(message.chat.id, response)
    except Exception as e:
        logger.error(f"Ошибка получения списка устройств: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить список устройств")


def switch_device_start(message):
    logger.info(f"Начало смены устройства от {message.chat.id}")
    if not is_authenticated(message.chat.id):
        return not_logged_in(message)

    user_id = get_user_id(message.chat.id)
    if not user_id:
        bot.send_message(message.chat.id, "❌ Ошибка авторизации")
        return

    try:
        cursor = db.execute_query(
            "SELECT c.id, c.computer_hash, uc.is_active "
            "FROM user_computers uc "
            "JOIN computers c ON uc.computer_id = c.id "
            "WHERE uc.user_id = %s",
            (user_id,)
        )
        devices = cursor.fetchall()

        if not devices:
            bot.send_message(message.chat.id, "📱 У вас нет привязанных устройств")
            return

        markup = types.InlineKeyboardMarkup()
        for device in devices:
            status = "✅" if device['is_active'] else "❌"
            markup.add(types.InlineKeyboardButton(
                f"{status} Устройство {device['id']}",
                callback_data=f"switch_device:{device['id']}"))

        bot.send_message(
            message.chat.id,
            "Выберите активное устройство:",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Ошибка получения списка устройств: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить список устройств")


@bot.callback_query_handler(func=lambda call: call.data.startswith('switch_device:'))
def switch_device_finish(call):
    chat_id = call.message.chat.id
    device_id = call.data.split(':')[1]

    try:
        user_id = get_user_id(chat_id)
        if not user_id:
            bot.answer_callback_query(call.id, "❌ Ошибка авторизации")
            return

        # Проверяем, принадлежит ли устройство пользователю
        cursor = db.execute_query(
            "SELECT 1 FROM user_computers WHERE user_id = %s AND computer_id = %s",
            (user_id, device_id)
        )
        if not cursor.fetchone():
            bot.answer_callback_query(call.id, "❌ Устройство не принадлежит вам")
            return

        # Снимаем активность со всех устройств пользователя
        db.execute_query(
            "UPDATE user_computers SET is_active = FALSE WHERE user_id = %s",
            (user_id,)
        )

        # Устанавливаем активность на выбранное устройство
        db.execute_query(
            "UPDATE user_computers SET is_active = TRUE WHERE user_id = %s AND computer_id = %s",
            (user_id, device_id)
        )

        # Обновляем время последней активности
        db.execute_query(
            "UPDATE user_computers SET last_active = CURRENT_TIMESTAMP WHERE user_id = %s AND computer_id = %s",
            (user_id, device_id)
        )

        # Обновляем текущее устройство в сессии
        db.execute_query(
            "UPDATE sessions SET current_computer_id = %s WHERE telegram_id = %s",
            (device_id, chat_id)
        )

        bot.answer_callback_query(call.id, "✅ Устройство активировано")
        bot.edit_message_text(
            "Активное устройство успешно изменено!",
            chat_id,
            call.message.message_id
        )
    except Exception as e:
        logger.error(f"Ошибка переключения устройства: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка переключения устройства")


def delete_device_start(message):
    logger.info(f"Начало удаления устройства от {message.chat.id}")
    if not is_authenticated(message.chat.id):
        return not_logged_in(message)

    user_id = get_user_id(message.chat.id)
    if not user_id:
        bot.send_message(message.chat.id, "❌ Ошибка авторизации")
        return

    try:
        cursor = db.execute_query(
            "SELECT c.id, c.computer_hash "
            "FROM user_computers uc "
            "JOIN computers c ON uc.computer_id = c.id "
            "WHERE uc.user_id = %s",
            (user_id,)
        )
        devices = cursor.fetchall()

        if not devices:
            bot.send_message(message.chat.id, "📱 У вас нет привязанных устройств")
            return

        markup = types.InlineKeyboardMarkup()
        for device in devices:
            markup.add(types.InlineKeyboardButton(
                f"Устройство {device['id']}",
                callback_data=f"delete_device:{device['id']}"))

        bot.send_message(
            message.chat.id,
            "Выберите устройство для удаления:",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Ошибка получения списка устройств: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось получить список устройств")


@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_device:'))
def delete_device_finish(call):
    chat_id = call.message.chat.id
    device_id = call.data.split(':')[1]

    try:
        user_id = get_user_id(chat_id)
        if not user_id:
            bot.answer_callback_query(call.id, "❌ Ошибка авторизации")
            return

        # Проверяем, принадлежит ли устройство пользователю
        cursor = db.execute_query(
            "SELECT is_active FROM user_computers WHERE user_id = %s AND computer_id = %s",
            (user_id, device_id)
        )
        device_info = cursor.fetchone()
        if not device_info:
            bot.answer_callback_query(call.id, "❌ Устройство не принадлежит вам")
            return

        # Удаляем привязку
        db.execute_query(
            "DELETE FROM user_computers WHERE user_id = %s AND computer_id = %s",
            (user_id, device_id)
        )

        # Если устройство было активным, выбираем новое активное
        if device_info['is_active']:
            cursor = db.execute_query(
                "SELECT computer_id FROM user_computers WHERE user_id = %s LIMIT 1",
                (user_id,)
            )
            new_active = cursor.fetchone()
            new_device_id = new_active['computer_id'] if new_active else None

            if new_device_id:
                db.execute_query(
                    "UPDATE user_computers SET is_active = TRUE WHERE user_id = %s AND computer_id = %s",
                    (user_id, new_device_id)
                )
                db.execute_query(
                    "UPDATE sessions SET current_computer_id = %s WHERE telegram_id = %s",
                    (new_device_id, chat_id)
                )
            else:
                db.execute_query(
                    "UPDATE sessions SET current_computer_id = NULL WHERE telegram_id = %s",
                    (chat_id,)
                )

        bot.answer_callback_query(call.id, "✅ Устройство удалено")
        bot.edit_message_text(
            "Устройство успешно удалено из вашего аккаунта!",
            chat_id,
            call.message.message_id
        )
    except Exception as e:
        logger.error(f"Ошибка удаления устройства: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка удаления устройства")


# Функции для портативного агента
def generate_agent_code(activation_key):
    server_url = os.getenv('SERVER_URL', 'http://your-server.com')

    return f'''import os
import sys
import platform
import uuid
import hashlib
import requests
import psutil
import socket
import subprocess
import json
import time
import getpass

# Конфигурация
SERVER_URL = "{server_url}"
ACTIVATION_KEY = "{activation_key}"
API_PORT = 5000

def install_dependencies():
    try:
        import pkg_resources
        required = {{'psutil', 'requests'}}
        installed = {{pkg.key for pkg in pkg_resources.working_set}}
        missing = required - installed

        if missing:
            print("Установка недостающих зависимостей...")
            python = sys.executable
            subprocess.check_call(
                [python, '-m', 'pip', 'install', *missing], 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except:
        pass

def generate_computer_hash():
    identifiers = []

    # 1. MAC-адреса
    try:
        macs = []
        for name, addrs in psutil.net_if_addrs().items():
            if name != 'lo' and not name.startswith('veth'):
                for addr in addrs:
                    if addr.family == psutil.AF_LINK:
                        mac = addr.address.replace(':', '-').upper()
                        if mac and mac != '00-00-00-00-00-00':
                            macs.append(mac)
        if macs:
            identifiers.extend(sorted(macs))
    except:
        pass

    # 2. Серийный номер системного диска
    try:
        if platform.system() == 'Windows':
            import wmi
            c = wmi.WMI()
            for disk in c.Win32_DiskDrive():
                if disk.DeviceID.startswith('PHYSICALDRIVE0'):
                    identifiers.append(disk.SerialNumber.strip())
        else:
            # Для Linux
            result = os.popen("sudo dmidecode -s system-serial-number 2>/dev/null").read().strip()
            if result and not result.startswith('O.E.M'):
                identifiers.append(result)
            else:
                result = os.popen("cat /etc/machine-id 2>/dev/null").read().strip()
                if result:
                    identifiers.append(result)
    except:
        pass

    # 3. Идентификатор процессора
    try:
        identifiers.append(platform.processor())
    except:
        pass

    # 4. Идентификатор материнской платы
    try:
        if platform.system() == 'Windows':
            import wmi
            c = wmi.WMI()
            for board in c.Win32_BaseBoard():
                identifiers.append(board.SerialNumber.strip())
        else:
            result = os.popen("sudo dmidecode -s baseboard-serial-number 2>/dev/null").read().strip()
            if result and not result.startswith('O.E.M'):
                identifiers.append(result)
    except:
        pass

    # 5. Размер оперативной памяти
    try:
        identifiers.append(str(psutil.virtual_memory().total))
    except:
        pass

    # 6. Идентификатор BIOS
    try:
        if platform.system() == 'Windows':
            import wmi
            c = wmi.WMI()
            for bios in c.Win32_BIOS():
                identifiers.append(bios.SerialNumber.strip())
        else:
            result = os.popen("sudo dmidecode -s bios-serial-number 2>/dev/null").read().strip()
            if result and not result.startswith('O.E.M'):
                identifiers.append(result)
    except:
        pass

    # Если не удалось получить идентификаторы, используем UUID
    if not identifiers:
        return str(uuid.uuid4())

    # Добавляем случайные данные
    identifiers.append(str(uuid.uuid4()))
    identifiers.append(str(os.urandom(8)))
    combined = "|".join(identifiers)
    return hashlib.sha256(combined.encode()).hexdigest()

def register_computer():
    computer_hash = generate_computer_hash()
    try:
        response = requests.post(
            f"{{SERVER_URL}}:{{API_PORT}}/register_computer",
            json={{"activation_key": ACTIVATION_KEY, "computer_hash": computer_hash}},
            timeout=30
        )
        return response.json()
    except Exception as e:
        return {{"success": False, "message": str(e)}}

def main():
    print("=== System Manager Agent ===")
    print("Установка необходимых зависимостей...")
    install_dependencies()

    print("Генерация идентификатора устройства...")
    computer_hash = generate_computer_hash()
    print(f"Хеш устройства: {{computer_hash}}")

    print("Регистрация устройства на сервере...")
    result = register_computer()

    if result.get("success"):
        print("✅ Устройство успешно зарегистрировано!")
        print(f"ID устройства: {{result.get('computer_id')}}")
        print("Теперь вы можете управлять этим устройством через Telegram бота")
    else:
        print(f"❌ Ошибка регистрации: {{result.get('message')}}")

    print("Агент завершает работу. Закройте это окно.")

if __name__ == '__main__':
    main()
'''


def download_agent(message):
    logger.info(f"Запрос скачивания агента от {message.chat.id}")

    if not is_authenticated(message.chat.id):
        return not_logged_in(message)

    user_id = get_user_id(message.chat.id)
    if not user_id:
        bot.send_message(message.chat.id, "❌ Ошибка авторизации")
        return

    try:
        # Генерируем уникальный ключ активации
        activation_key = str(uuid.uuid4())

        # Сохраняем ключ в базе
        db.execute_query(
            "INSERT INTO activation_keys (user_id, activation_key) VALUES (%s, %s)",
            (user_id, activation_key)
        )

        # Создаем временные файлы
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Основной файл агента
            agent_path = os.path.join(tmp_dir, "SystemManagerAgent.py")
            with open(agent_path, 'w', encoding='utf-8') as f:
                f.write(generate_agent_code(activation_key))

            # Пакетный файл для Windows
            bat_path = os.path.join(tmp_dir, "start_agent.bat")
            with open(bat_path, 'w', encoding='utf-8') as f:
                f.write('''@echo off
echo Запуск System Manager Agent...
echo Убедитесь, что Python установлен и добавлен в PATH
echo Если возникают ошибки, установите зависимости: pip install psutil requests

python SystemManagerAgent.py
pause
''')

            # Скрипт для Linux/Mac
            sh_path = os.path.join(tmp_dir, "start_agent.sh")
            with open(sh_path, 'w', encoding='utf-8') as f:
                f.write('''#!/bin/bash
echo "Запуск System Manager Agent..."
echo "Убедитесь, что Python 3 установлен"
echo "Если возникают ошибки, установите зависимости: pip3 install psutil requests"

python3 SystemManagerAgent.py
read -p "Нажмите Enter для выхода..."
''')

            # Файл README
            readme_path = os.path.join(tmp_dir, "README.txt")
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write('''=== System Manager Agent ===

1. Распакуйте этот архив в любую папку
2. Для запуска:
   - На Windows: дважды щелкните start_agent.bat
   - На Linux/Mac: запустите start_agent.sh

3. При первом запуске:
   - Установятся необходимые зависимости (если их нет)
   - Компьютер автоматически зарегистрируется в системе
   - Появится ID устройства

4. После регистрации:
   - Закройте окно терминала
   - Компьютер появится в вашем Telegram-боте

5. Для удаления:
   - Просто удалите папку с агентом
   - В Telegram-боте удалите устройство через меню "Устройства"

Примечания:
- Агент не требует постоянной работы, запускайте его только для регистрации
- Для повторной регистрации (например, после переустановки ОС) просто запустите снова
''')

            # Создаем ZIP-архив
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(agent_path, "SystemManagerAgent.py")
                zipf.write(bat_path, "start_agent.bat")
                zipf.write(sh_path, "start_agent.sh")
                zipf.write(readme_path, "README.txt")

            zip_buffer.seek(0)

            # Отправляем пользователю
            bot.send_document(
                message.chat.id,
                zip_buffer,
                caption="📥 Загрузите и запустите этот файл на своем компьютере\n"
                        "После запуска агент автоматически подключится к вашему аккаунту",
                visible_file_name="SystemManagerAgent.zip"
            )
            logger.info(f"Агент отправлен пользователю {message.chat.id}")

    except Exception as e:
        logger.error(f"Ошибка создания агента: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при создании агента")


# API для регистрации компьютеров
@app.route('/register_computer', methods=['POST'])
def register_computer():
    try:
        data = request.json
        activation_key = data.get('activation_key')
        computer_hash = data.get('computer_hash')

        if not activation_key or not computer_hash:
            return jsonify({"success": False, "message": "Неверный запрос"}), 400

        # Проверяем ключ активации
        cursor = db.execute_query(
            "SELECT user_id FROM activation_keys WHERE activation_key = %s",
            (activation_key,)
        )
        key_data = cursor.fetchone()

        if not key_data:
            return jsonify({"success": False, "message": "Неверный ключ активации"}), 401

        user_id = key_data['user_id']

        # Регистрируем компьютер
        computer_id = get_computer_id(computer_hash)

        # Привязываем к пользователю
        cursor = db.execute_query(
            "SELECT 1 FROM user_computers WHERE user_id = %s AND computer_id = %s",
            (user_id, computer_id)
        )
        if not cursor.fetchone():
            db.execute_query(
                "INSERT INTO user_computers (user_id, computer_id) VALUES (%s, %s)",
                (user_id, computer_id)
            )

        # Удаляем использованный ключ
        db.execute_query(
            "DELETE FROM activation_keys WHERE activation_key = %s",
            (activation_key,)
        )

        return jsonify({
            "success": True,
            "computer_id": computer_id,
            "message": "Компьютер успешно зарегистрирован"
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}")
        return jsonify({"success": False, "message": "Ошибка сервера"}), 500


def run_flask():
    app.run(host='0.0.0.0', port=API_PORT)


def bot_polling():
    logger.info("Запуск polling бота...")
    while True:
        try:
            logger.info("Бот запущен и ожидает сообщений...")
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logger.error(f"Ошибка в работе бота: {e}")
            logger.info("Перезапуск бота через 15 секунд...")
            time.sleep(15)


if __name__ == '__main__':
    # Запускаем Flask API в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Сервер API запущен на порту {API_PORT}")

    # Запускаем бота
    logger.info("=== ЗАПУСК БОТА ===")
    bot_polling()

