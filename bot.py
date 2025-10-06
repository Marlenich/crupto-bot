import telebot
import sqlite3
import requests
import time
import os
import threading

print("=== БОТ ЗАПУЩЕН НА RAILWAY ===")

# Токен бота
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

# ID администратора (твой ID)
ADMIN_ID = 5870642170  # ЗАМЕНИ НА СВОЙ ID

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
    
    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Таблица алертов
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
    
    # Обновляем активность пользователя
    cursor.execute('''
    INSERT OR REPLACE INTO users (user_id, last_activity) 
    VALUES (?, CURRENT_TIMESTAMP)
    ''', (user_id,))
    
    # Добавляем алерт
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

def is_admin(user_id):
    """Проверяет, является ли пользователь администратором"""
    return user_id == ADMIN_ID

# Команды бота
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    
    # Логируем пользователя (в фоне, пользователь не видит)
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at, last_activity) 
    VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM users WHERE user_id = ?), CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
    ''', (user_id, username, first_name, last_name, user_id))
    conn.commit()
    conn.close()
    
    print(f"👤 Пользователь {user_id} запустил бота")
    bot.send_message(message.chat.id, "💰 Привет! Я бот для отслеживания цен крипто монет на Bybit.\n\nПросто напиши: BTC 50000 (пример)\n\nЯ пришлю уведомление когда цена достигент указанных значений")

@bot.message_handler(commands=['status'])
def status(message):
    alerts_count = len(get_all_alerts())
    
    # Проверяем текущую цену BTC для демонстрации
    btc_price, _ = get_current_price("BTC")
    price_info = f"\n💰 BTC сейчас: ${btc_price:,.2f}" if btc_price else ""
    
    bot.send_message(message.chat.id, f"✅ Бот работает!\nАктивных запросов: {alerts_count}{price_info}\n\nИспользуй:\n/testprice - проверить цену\n/checknow - мои алерты\n/myalerts - список алертов")

@bot.message_handler(commands=['stats'])
def show_stats(message):
    """Статистика (только для администратора)"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
        return
        
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Количество уникальных пользователей
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM alerts')
    unique_users = cursor.fetchone()[0]
    
    # Общее количество алертов
    cursor.execute('SELECT COUNT(*) FROM alerts')
    total_alerts = cursor.fetchone()[0]
    
    # Активные пользователи (за последние 7 дней)
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM alerts WHERE created_at > datetime("now", "-7 days")')
    active_users = cursor.fetchone()[0]
    
    conn.close()
    
    stats_text = f"""📊 СТАТИСТИКА БОТА:

👥 Всего пользователей: {unique_users}
🔔 Всего алертов: {total_alerts}
🎯 Активных пользователей: {active_users}"""

    bot.send_message(message.chat.id, stats_text)

@bot.message_handler(commands=['detailed_stats'])
def detailed_stats(message):
    """Детальная статистика (только для администратора)"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
        return
        
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Основная статистика
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM alerts')
    total_alerts = cursor.fetchone()[0]
    
    # Активные пользователи за разные периоды
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM users WHERE last_activity > datetime("now", "-1 day")')
    active_1d = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM users WHERE last_activity > datetime("now", "-7 days")')
    active_7d = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM users WHERE last_activity > datetime("now", "-30 days")')
    active_30d = cursor.fetchone()[0]
    
    # Популярные монеты
    cursor.execute('SELECT symbol, COUNT(*) as count FROM alerts GROUP BY symbol ORDER BY count DESC LIMIT 5')
    popular_coins = cursor.fetchall()
    
    conn.close()
    
    stats_text = f"""📊 ДЕТАЛЬНАЯ СТАТИСТИКА:

👥 Всего пользователей: {total_users}
🔔 Всего алертов: {total_alerts}

🎯 Активность:
• За 24 часа: {active_1d} пользователей
• За 7 дней: {active_7d} пользователей  
• За 30 дней: {active_30d} пользователей

🏆 Популярные монеты:
"""
    
    for coin, count in popular_coins:
        stats_text += f"• {coin}: {count} алертов\n"
    
    bot.send_message(message.chat.id, stats_text)

@bot.message_handler(commands=['testprice'])
def test_price(message):
    """Проверка текущей цены"""
    try:
        symbol = "BTC"
        current_price, full_symbol = get_current_price(symbol)
        
        if current_price:
            response = f"🧪 ТЕКУЩАЯ ЦЕНА:\n\n{full_symbol}\n💰 ${current_price:,.2f}"
            bot.send_message(message.chat.id, response)
        else:
            bot.send_message(message.chat.id, "❌ Не удалось получить цену BTC")
            
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

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
            bot.send_message(message.chat.id, response)
        else:
            bot.send_message(message.chat.id, "❌ Не удалось получить текущую цену BTC")
            
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['checknow'])
def check_now(message):
    """Принудительная проверка всех алертов"""
    user_id = message.from_user.id
    try:
        all_alerts = get_all_alerts()
        user_alerts = [alert for alert in all_alerts if alert[1] == user_id]
        
        if not user_alerts:
            bot.send_message(message.chat.id, "У тебя нет активных алертов")
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
        
        bot.send_message(message.chat.id, response)
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка проверки: {e}")

@bot.message_handler(commands=['clear'])
def clear_alerts(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alerts WHERE user_id = ?', (user_id,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"✅ Удалено {count} алертов!")

# Установка алерта
@bot.message_handler(func=lambda message: True)
def set_alert(message):
    try:
        user_id = message.from_user.id
        text = message.text.split()
        
        if len(text) < 2:
            bot.send_message(message.chat.id, "❌ Напиши в формате: ТИКЕР ЦЕНА\nНапример: BTC 50000")
            return

        symbol = text[0].upper()
        target_price = float(text[1])

        print(f"🔄 Запрос от {user_id}: {symbol} ${target_price}")

        current_price, full_symbol = get_current_price(symbol)
        
        if current_price is None:
            bot.send_message(message.chat.id, f"❌ Тикер '{symbol}' не найден. Попробуй: BTC, ETH, SOL, ADA")
            return

        # Определяем тип алерта
        alert_type = determine_alert_type(current_price, target_price)
        alert_icon = "📈" if alert_type == "UP" else "📉"

        add_alert(user_id, full_symbol, target_price, current_price, alert_type)
        
        response = f"""{full_symbol}
💰 Текущая цена: ${current_price:,.2f}
{alert_icon} Оповещение при: <b>${target_price:,.2f}</b>"""

        bot.send_message(message.chat.id, response, parse_mode='HTML')
        
    except ValueError:
        bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или ETH 3500.50")
    except Exception as e:
        bot.send_message(message.chat.id, "❌ Ошибка, попробуй еще раз")
        print(f"❌ Ошибка: {e}")

# Фоновая проверка цен
def check_prices():
    print("🔄 Фоновая проверка цен ЗАПУЩЕНА! (интервал: 5 секунд)")
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
                            direction = "выросла до" if alert_type == "UP" else "упала до"
                            message_text = f"{icon} {symbol} {direction} ${target_price:,.2f}"
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
        
        print(f"⏰ Жду 5 секунд...")
        time.sleep(5)

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