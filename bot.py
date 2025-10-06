import telebot
import sqlite3
import requests
import time
import os
import threading

print("=== ПРОВЕРКА RAILWAY ===")

# Проверяем все переменные окружения
print("🔍 Доступные переменные окружения:")
for key, value in os.environ.items():
    if 'BOT' in key or 'TOKEN' in key:
        print(f"   {key}: {value}")

# Получаем токен
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

print(f"📋 TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN}")

if not TELEGRAM_BOT_TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не найден!")
    print("ℹ️  Создай переменную в Railway:")
    print("   Name: TELEGRAM_BOT_TOKEN")
    print("   Value: токен_от_BotFather")
    exit()

print(f"✅ Токен получен! Длина: {len(TELEGRAM_BOT_TOKEN)} символов")

# Создаем бота
try:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    print("✅ Бот создан успешно!")
except Exception as e:
    print(f"❌ Ошибка создания бота: {e}")
    exit()

# База данных
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
    print("✅ База данных готова")

def add_alert(user_id, symbol, target_price):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO alerts (user_id, symbol, target_price) VALUES (?, ?, ?)',
                   (user_id, symbol.upper(), target_price))
    conn.commit()
    conn.close()

def get_all_alerts():
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, user_id, symbol, target_price FROM alerts')
    all_alerts = cursor.fetchall()
    conn.close()
    return all_alerts

def delete_alert(alert_id):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

def get_current_price(symbol):
    try:
        full_symbol = f"{symbol.upper()}USDT"
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={full_symbol}"
        response = requests.get(url, timeout=10)
        data = response.json()
        current_price = float(data['result']['list'][0]['lastPrice'])
        return current_price, full_symbol
    except:
        return None, symbol

# Команды бота
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "✅ Бот работает! Напиши: BTC 50000")

@bot.message_handler(commands=['test'])
def test(message):
    bot.reply_to(message, "🟢 Тест пройден! Бот активен")

@bot.message_handler(commands=['myalerts'])
def list_alerts(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT symbol, target_price FROM alerts WHERE user_id = ?', (user_id,))
    alerts = cursor.fetchall()
    conn.close()
    
    if not alerts:
        bot.send_message(message.chat.id, "Нет активных запросов.")
    else:
        response = "Твои запросы:\n"
        for alert in alerts:
            symbol, price = alert
            response += f"• {symbol} -> ${price}\n"
        bot.send_message(message.chat.id, response)

# Установка алерта
@bot.message_handler(func=lambda message: True)
def set_alert(message):
    try:
        user_id = message.from_user.id
        text = message.text.split()
        
        if len(text) < 2:
            bot.reply_to(message, "Напиши: ТИКЕР ЦЕНА\nПример: BTC 50000")
            return

        symbol = text[0]
        target_price = float(text[1])

        current_price, full_symbol = get_current_price(symbol)
        
        if current_price is None:
            bot.reply_to(message, f"Тикер '{symbol}' не найден")
            return

        add_alert(user_id, full_symbol, target_price)
        
        response = f"""✅ Алерт установлен!

{full_symbol}
Сейчас: ${current_price:,.2f}
Оповещение: ${target_price:,.2f}"""
        
        bot.reply_to(message, response)
        
    except ValueError:
        bot.reply_to(message, "Цена должна быть числом!")

# Фоновая проверка цен
def check_prices():
    while True:
        try:
            all_alerts = get_all_alerts()
            for alert in all_alerts:
                alert_id, user_id, symbol, target_price = alert
                current_price, _ = get_current_price(symbol)

                if current_price and current_price >= target_price:
                    message_text = f"🚀 АЛЕРТ! {symbol} - ${current_price:,.2f}"
                    bot.send_message(user_id, message_text)
                    delete_alert(alert_id)
                        
        except Exception as e:
            print(f"Ошибка: {e}")
        
        time.sleep(30)

# Запуск
if __name__ == "__main__":
    print("🔄 Инициализация...")
    init_db()
    
    print("🔄 Запуск фоновой проверки...")
    price_thread = threading.Thread(target=check_prices)
    price_thread.daemon = True
    price_thread.start()
    
    print("✅ ВСЕ СИСТЕМЫ ЗАПУЩЕНЫ")
    print("🤖 Запускаю бота...")
    
    bot.infinity_polling()