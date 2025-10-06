import telebot
import sqlite3
import requests
import time
import os
import threading

print("=== БОТ ЗАПУЩЕН НА RAILWAY ===")

# Токен бота
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

if not TELEGRAM_BOT_TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не найден!")
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
        target_price REAL NOT NULL,
        current_price REAL NOT NULL,
        alert_type TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()
    print("✅ База данных готова")

def add_alert(user_id, symbol, target_price, current_price, alert_type):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO alerts (user_id, symbol, target_price, current_price, alert_type) VALUES (?, ?, ?, ?, ?)',
                   (user_id, symbol.upper(), target_price, current_price, alert_type))
    conn.commit()
    conn.close()
    print(f"✅ Добавлен алерт: {symbol} {alert_type} ${target_price} (сейчас: ${current_price})")

def get_all_alerts():
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, user_id, symbol, target_price, current_price, alert_type FROM alerts')
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

def get_user_alerts(user_id):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, symbol, target_price, alert_type FROM alerts WHERE user_id = ?', (user_id,))
    alerts = cursor.fetchall()
    conn.close()
    return alerts

def get_current_price(symbol):
    try:
        # Убираем USDT если уже есть в символе
        if symbol.endswith('USDT'):
            full_symbol = symbol
        else:
            full_symbol = f"{symbol.upper()}USDT"
        
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={full_symbol}"
        print(f"🔍 Запрашиваю цену для: {full_symbol}")
        response = requests.get(url, timeout=10)
        data = response.json()
        
        # Отладочная информация
        print(f"📊 Ответ API: {data}")
        
        # Проверяем структуру ответа
        if data.get('retCode') != 0:
            print(f"❌ Ошибка API: {data.get('retMsg')}")
            return None, symbol
            
        if 'result' not in data or 'list' not in data['result']:
            print(f"❌ Неверная структура ответа API")
            return None, symbol
            
        tickers = data['result']['list']
        if not tickers:
            print(f"❌ Нет данных для символа {full_symbol}")
            return None, symbol
            
        # Берем первый тикер из списка
        ticker = tickers[0]
        
        # Пробуем разные поля с ценой
        if 'lastPrice' in ticker and ticker['lastPrice']:
            current_price = float(ticker['lastPrice'])
        elif 'markPrice' in ticker and ticker['markPrice']:
            current_price = float(ticker['markPrice'])
        elif 'indexPrice' in ticker and ticker['indexPrice']:
            current_price = float(ticker['indexPrice'])
        else:
            print(f"❌ Не найдено поле с ценой в ответе")
            return None, symbol
        
        print(f"✅ Цена получена: {full_symbol} = ${current_price}")
        return current_price, full_symbol
        
    except Exception as e:
        print(f"❌ Ошибка получения цены для {symbol}: {e}")
        return None, symbol

def determine_alert_type(current_price, target_price):
    """Определяем тип алерта: UP (рост) или DOWN (падение)"""
    if target_price > current_price:
        return "UP"  # Ждем роста цены
    else:
        return "DOWN"  # Ждем падения цены

def should_trigger_alert(current_price, target_price, alert_type):
    """Определяем, должен ли сработать алерт"""
    if alert_type == "UP":
        return current_price >= target_price  # Срабатывает когда цена ВЫРОСЛА до цели
    else:  # DOWN
        return current_price <= target_price  # Срабатывает когда цена УПАЛА до цели

# Команды бота
@bot.message_handler(commands=['start'])
def send_welcome(message):
    print(f"👤 Пользователь {message.from_user.id} запустил бота")
    bot.reply_to(message, "💰 Привет! Я бот для отслеживания цен крипты.\n\nПросто напиши: BTC 50000\n\nЯ сам пойму, ждать роста или падения цены! 📈📉")

@bot.message_handler(commands=['status'])
def status(message):
    alerts_count = len(get_all_alerts())
    
    # Проверяем текущую цену BTC для демонстрации
    btc_price, _ = get_current_price("BTC")
    price_info = f"\n💰 BTC сейчас: ${btc_price:,.2f}" if btc_price else ""
    
    bot.reply_to(message, f"✅ Бот работает!\nАктивных запросов: {alerts_count}{price_info}\n\nИспользуй:\n/testprice - проверить цену\n/checknow - мои алерты\n/myalerts - список алертов")

@bot.message_handler(commands=['testprice'])
def test_price(message):
    """Проверка текущей цены"""
    try:
        symbol = "BTC"
        current_price, full_symbol = get_current_price(symbol)
        
        if current_price:
            response = f"🧪 ТЕКУЩАЯ ЦЕНА:\n\n{full_symbol}\n💰 ${current_price:,.2f}"
            bot.reply_to(message, response)
        else:
            bot.reply_to(message, "❌ Не удалось получить цену BTC")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['myalerts'])
def list_alerts(message):
    user_id = message.from_user.id
    alerts = get_user_alerts(user_id)
    
    if not alerts:
        bot.send_message(message.chat.id, "У тебя нет активных запросов.")
    else:
        response = "📋 Твои запросы:\n\n"
        for alert in alerts:
            id, symbol, target_price, alert_type = alert
            icon = "📈" if alert_type == "UP" else "📉"
            response += f"• {icon} {symbol} -> ${target_price:,.2f} ({alert_type})\n"
        bot.send_message(message.chat.id, response)

@bot.message_handler(commands=['testalert'])
def test_alert(message):
    """Тестовая команда для проверки работы алертов"""
    user_id = message.from_user.id
    try:
        symbol = "BTC"
        current_price, full_symbol = get_current_price(symbol)
        
        if current_price:
            # Создаем два тестовых алерта: один на рост, один на падение
            test_target_up = current_price + 100  # На $100 выше
            test_target_down = current_price - 100  # На $100 ниже
            
            add_alert(user_id, full_symbol, test_target_up, current_price, "UP")
            add_alert(user_id, full_symbol, test_target_down, current_price, "DOWN")
            
            response = f"""🧪 ТЕСТОВЫЕ АЛЕРТЫ!

{full_symbol}
Сейчас: ${current_price:,.2f}

📈 Алерт на РОСТ: ${test_target_up:,.2f}
📉 Алерт на ПАДЕНИЕ: ${test_target_down:,.2f}

Слежу за ценой!"""
            bot.reply_to(message, response)
        else:
            bot.reply_to(message, "❌ Не удалось получить текущую цену BTC")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['checknow'])
def check_now(message):
    """Принудительная проверка всех алертов"""
    user_id = message.from_user.id
    try:
        all_alerts = get_all_alerts()
        user_alerts = [alert for alert in all_alerts if alert[1] == user_id]
        
        if not user_alerts:
            bot.reply_to(message, "У тебя нет активных алертов")
            return
            
        response = "🔍 Твои алерты:\n\n"
        for alert in user_alerts:
            alert_id, user_id, symbol, target_price, initial_price, alert_type = alert
            current_price_now, _ = get_current_price(symbol)
            
            if current_price_now:
                icon = "📈" if alert_type == "UP" else "📉"
                status = "✅ ГОТОВ!" if should_trigger_alert(current_price_now, target_price, alert_type) else "⏳ жду"
                diff = current_price_now - target_price
                diff_text = f"+${diff:,.2f}" if diff > 0 else f"-${abs(diff):,.2f}"
                
                response += f"• {icon} {symbol}: ${current_price_now:,.2f} / ${target_price:,.2f} ({diff_text}) - {status}\n"
            else:
                response += f"• {symbol}: ошибка получения цены\n"
        
        bot.reply_to(message, response)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка проверки: {e}")

@bot.message_handler(commands=['clear'])
def clear_alerts(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE user_id = ?', (user_id,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    bot.reply_to(message, f"✅ Удалено {count} алертов!")

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

        # Определяем тип алерта
        alert_type = determine_alert_type(current_price, target_price)
        alert_icon = "📈" if alert_type == "UP" else "📉"
        alert_text = "роста" if alert_type == "UP" else "падения"

        add_alert(user_id, full_symbol, target_price, current_price, alert_type)
        
        response = f"""✅ Алерт установлен!

💠 Монета: {full_symbol}
💰 Текущая цена: ${current_price:,.2f}
{alert_icon} Оповещение при: ${target_price:,.2f}
🎯 Тип: жду {alert_text} цены

Бот следит за ценой каждые 30 секунд!"""
        
        bot.reply_to(message, response)
        
    except ValueError:
        bot.reply_to(message, "❌ Цена должна быть числом!\nПример: BTC 50000 или ETH 3500.50")
    except Exception as e:
        bot.reply_to(message, "❌ Ошибка, попробуй еще раз")
        print(f"❌ Ошибка: {e}")

# Фоновая проверка цен
def check_prices():
    print("🔄 Фоновая проверка цен ЗАПУЩЕНА! (интервал: 30 секунд)")
    while True:
        try:
            all_alerts = get_all_alerts()
            if all_alerts:
                print(f"🔍 Проверяю {len(all_alerts)} алертов...")
            else:
                print("🔍 Нет алертов для проверки")
            
            for alert in all_alerts:
                alert_id, user_id, symbol, target_price, initial_price, alert_type = alert
                current_price, _ = get_current_price(symbol)

                if current_price:
                    print(f"💰 {symbol}: ${current_price} / ${target_price} ({alert_type})")
                    
                    if should_trigger_alert(current_price, target_price, alert_type):
                        print(f"🚨 АЛЕРТ СРАБОТАЛ! {symbol} {alert_type} ${target_price}")
                        try:
                            icon = "📈" if alert_type == "UP" else "📉"
                            direction = "выросла" if alert_type == "UP" else "упала"
                            message_text = f"""🚀 АЛЕРТ! 🚀

{icon} {symbol} {direction} до цели!

🎯 Цель: ${target_price:,.2f}
💰 Текущая цена: ${current_price:,.2f}

Алерт выполнен! ✅"""
                            bot.send_message(user_id, message_text)
                            delete_alert(alert_id)
                            print(f"✅ Уведомление отправлено пользователю {user_id}")
                        except Exception as e:
                            print(f"❌ Ошибка отправки: {e}")
                    else:
                        print(f"⏳ {symbol}: еще не достиг цели ({alert_type})")
                else:
                    print(f"❌ Не удалось получить цену для {symbol}")
                        
        except Exception as e:
            print(f"❌ Ошибка проверки: {e}")
        
        print(f"⏰ Жду 30 секунд...")
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