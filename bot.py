import telebot
import sqlite3
import requests
import time
import os
import threading

print("=== БОТ ЗАПУЩЕН НА RAILWAY ===")

# Токен бота
TELEGRAM_BOT_TOKEN = os.environ.get('7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU')
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

print("✅ Бот инициализирован")

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
    print(f"✅ Добавлен алерт: {symbol} -> ${target_price}")

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
    print(f"✅ Удален алерт ID: {alert_id}")

def get_current_price(symbol):
    try:
        # Если символ короткий (BTC, ETH) - добавляем USDT
        if len(symbol) <= 5:
            full_symbol = f"{symbol.upper()}USDT"
        else:
            full_symbol = symbol.upper()
            
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={full_symbol}"
        response = requests.get(url, timeout=10)
        data = response.json()
        current_price = float(data['result']['list'][0]['lastPrice'])
        return current_price, full_symbol
    except Exception as e:
        print(f"❌ Ошибка получения цены для {symbol}: {e}")
        return None, symbol

# Команды бота
@bot.message_handler(commands=['start'])
def send_welcome(message):
    print(f"👤 Пользователь {message.from_user.id} запустил бота")
    bot.reply_to(message, "💰 Привет! Я бот для отслеживания цен крипты.\n\nПросто напиши: BTC 50000\nИ я сообщу когда Bitcoin достигнет $50000")

@bot.message_handler(commands=['status'])
def status(message):
    alerts_count = len(get_all_alerts())
    bot.reply_to(message, f"✅ Бот работает на Railway!\nАктивных запросов: {alerts_count}\n\nПиши: BTC 50000")

@bot.message_handler(commands=['myalerts'])
def list_alerts(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, symbol, target_price FROM alerts WHERE user_id = ?', (user_id,))
    alerts = cursor.fetchall()
    conn.close()
    
    if not alerts:
        bot.send_message(message.chat.id, "У тебя нет активных запросов.")
    else:
        response = "📋 Твои запросы:\n\n"
        for alert in alerts:
            id, symbol, price = alert
            response += f"• {symbol} -> ${price}\n"
        bot.send_message(message.chat.id, response)

@bot.message_handler(commands=['clear'])
def clear_alerts(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    bot.reply_to(message, "✅ Все твои запросы удалены!")

# Установка алерта
@bot.message_handler(func=lambda message: True)
def set_alert(message):
    try:
        user_id = message.from_user.id
        text = message.text.split()
        
        if len(text) < 2:
            bot.reply_to(message, "❌ Напиши в формате: ТИКЕР ЦЕНА\nНапример: BTC 50000")
            return

        symbol = text[0].upper()
        target_price = float(text[1])

        print(f"🔄 Запрос от {user_id}: {symbol} ${target_price}")

        current_price, full_symbol = get_current_price(symbol)
        
        if current_price is None:
            bot.reply_to(message, f"❌ Тикер '{symbol}' не найден. Попробуй: BTC, ETH, SOL, ADA")
            return

        add_alert(user_id, full_symbol, target_price)
        
        response = f"""✅ Алерт установлен!

💠 Монета: {full_symbol}
💰 Текущая цена: ${current_price:,.2f}
🎯 Оповещение при: ${target_price:,.2f}

Бот проверяет цены каждые 30 секунд."""
        
        bot.reply_to(message, response)
        
    except ValueError:
        bot.reply_to(message, "❌ Цена должна быть числом!\nПример: BTC 50000 или ETH 3500.50")
    except Exception as e:
        bot.reply_to(message, "❌ Ошибка, попробуй еще раз")
        print(f"❌ Ошибка: {e}")

# Фоновая проверка цен
def check_prices():
    print("🔄 Запущена фоновая проверка цен...")
    while True:
        try:
            all_alerts = get_all_alerts()
            if all_alerts:
                print(f"🔍 Проверяю {len(all_alerts)} алертов...")
            
            for alert in all_alerts:
                alert_id, user_id, symbol, target_price = alert
                current_price, _ = get_current_price(symbol)

                if current_price and current_price >= target_price:
                    try:
                        message_text = f"🚀 АЛЕРТ! 🚀\n\n{symbol} достиг цели!\n🎯 Цель: ${target_price:,.2f}\n💰 Текущая цена: ${current_price:,.2f}"
                        bot.send_message(user_id, message_text)
                        delete_alert(alert_id)
                        print(f"✅ Отправлен алерт пользователю {user_id} для {symbol}")
                    except Exception as e:
                        print(f"❌ Ошибка отправки: {e}")
                        
        except Exception as e:
            print(f"❌ Ошибка проверки: {e}")
        
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
    print("🤖 Бот начинает опрос Telegram...")
    
    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")