import telebot
import sqlite3
import requests
import threading
import time
import os

# Получаем токен бота
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

# Создаем бота
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Создаем базу данных для хранения запросов
def init_db():
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        target_price REAL NOT NULL
    )
    ''')
    conn.commit()
    conn.close()

# Добавляем новый запрос в базу
def add_alert(user_id, symbol, target_price):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO alerts (user_id, symbol, target_price) VALUES (?, ?, ?)',
                   (user_id, symbol.upper(), target_price))
    conn.commit()
    conn.close()

# Получаем все запросы пользователя
def get_user_alerts(user_id):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, symbol, target_price FROM alerts WHERE user_id = ?', (user_id,))
    alerts = cursor.fetchall()
    conn.close()
    return alerts

# Получаем ВСЕ запросы для проверки
def get_all_alerts():
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, user_id, symbol, target_price FROM alerts')
    all_alerts = cursor.fetchall()
    conn.close()
    return all_alerts

# Удаляем запрос
def delete_alert(alert_id):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

# Получаем текущую цену с Bybit
def get_current_price(symbol):
    try:
        full_symbol = f"{symbol.upper()}USDT"
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={full_symbol}"
        response = requests.get(url).json()
        current_price = float(response['result']['list'][0]['lastPrice'])
        return current_price, full_symbol
    except:
        return None, symbol

# Фоновая проверка цен каждые 30 секунд
def check_prices_background():
    while True:
        try:
            all_alerts = get_all_alerts()
            for alert in all_alerts:
                alert_id, user_id, symbol, target_price = alert
                current_price, _ = get_current_price(symbol)

                if current_price and current_price >= target_price:
                    bot.send_message(user_id, f'🚀 АЛЕРТ! 🚀\n{symbol} достиг цены ${target_price}!\nТекущая цена: ${current_price}')
                    delete_alert(alert_id)
        except:
            pass
        time.sleep(30)

# Команда /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я бот для отслеживания цен криптовалют. Просто напиши мне тикер и цену, например: BTC 50000")

# Команда /myalerts - показать мои запросы
@bot.message_handler(commands=['myalerts'])
def list_alerts(message):
    user_id = message.from_user.id
    alerts = get_user_alerts(user_id)

    if not alerts:
        bot.send_message(message.chat.id, "У тебя нет активных запросов.")
    else:
        response = "Твои запросы:\n"
        for alert in alerts:
            id, symbol, price = alert
            response += f"{symbol} -> ${price}\n"
        bot.send_message(message.chat.id, response)

# Обработка сообщений с тикером и ценой
@bot.message_handler(func=lambda message: True)
def set_alert(message):
    try:
        user_id = message.from_user.id
        parts = message.text.split()
        
        if len(parts) < 2:
            bot.reply_to(message, "Напиши в формате: ТИКЕР ЦЕНА\nНапример: BTC 50000")
            return

        symbol = parts[0]
        target_price = float(parts[1])

        current_price, full_symbol = get_current_price(symbol)
        if current_price is None:
            bot.reply_to(message, f"Ошибка! Тикер '{symbol}' не найден.")
            return

        add_alert(user_id, full_symbol, target_price)
        bot.reply_to(message, f"✅ Готово! Слежу за {full_symbol}\nСейчас: ${current_price}\nОповещу при: ${target_price}")

    except ValueError:
        bot.reply_to(message, "Цена должна быть числом. Пример: BTC 50000")

# Запускаем все
init_db()
thread = threading.Thread(target=check_prices_background)
thread.daemon = True
thread.start()

print("Бот запущен!")
bot.infinity_polling()