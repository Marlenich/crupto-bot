import telebot
import sqlite3
import requests
import time
import os
import threading

print("=== БОТ ЗАПУЩЕН НА RAILWAY ===")

# Токен бота
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

# ID администратора (ЗАМЕНИ НА СВОЙ ID)
ADMIN_ID = 5870642170  # ЗАМЕНИ НА СВОЙ TELEGRAM ID

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

# Кэш для хранения списка доступных символов
available_symbols_cache = None
cache_timestamp = 0
CACHE_DURATION = 3600  # 1 час

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
    print(f"✅ Добавлен алерт: {symbol} {alert_type} ${target_price:.6f} (сейчас: ${current_price:.6f})")

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

def get_available_symbols():
    """Получает список всех доступных символов с Bybit"""
    global available_symbols_cache, cache_timestamp
    
    # Проверяем кэш
    current_time = time.time()
    if available_symbols_cache and (current_time - cache_timestamp) < CACHE_DURATION:
        return available_symbols_cache
    
    try:
        # Получаем список всех спотовых пар
        url_spot = "https://api.bybit.com/v5/market/tickers?category=spot"
        response_spot = requests.get(url_spot, timeout=10)
        data_spot = response_spot.json()
        
        symbols = set()
        
        if data_spot.get('retCode') == 0 and 'result' in data_spot and 'list' in data_spot['result']:
            for ticker in data_spot['result']['list']:
                symbol = ticker.get('symbol', '')
                if symbol and symbol.endswith('USDT'):
                    symbols.add(symbol.replace('USDT', ''))
        
        # Получаем список фьючерсных пар
        url_linear = "https://api.bybit.com/v5/market/tickers?category=linear"
        response_linear = requests.get(url_linear, timeout=10)
        data_linear = response_linear.json()
        
        if data_linear.get('retCode') == 0 and 'result' in data_linear and 'list' in data_linear['result']:
            for ticker in data_linear['result']['list']:
                symbol = ticker.get('symbol', '')
                if symbol and symbol.endswith('USDT'):
                    symbols.add(symbol.replace('USDT', ''))
        
        available_symbols_cache = sorted(list(symbols))
        cache_timestamp = current_time
        
        print(f"✅ Получено {len(available_symbols_cache)} доступных символов")
        return available_symbols_cache
        
    except Exception as e:
        print(f"❌ Ошибка получения списка символов: {e}")
        return available_symbols_cache or []

def get_current_price(symbol):
    try:
        # Убираем USDT если уже есть в символе
        clean_symbol = symbol.upper().replace('USDT', '')
        full_symbol = f"{clean_symbol}USDT"
        
        # Пробуем сначала спотовый рынок
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={full_symbol}"
        print(f"🔍 Запрашиваю цену для: {full_symbol}")
        response = requests.get(url, timeout=10)
        data = response.json()
        
        # Если нет на споте, пробуем фьючерсы
        if data.get('retCode') != 0 or not data.get('result', {}).get('list'):
            url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={full_symbol}"
            print(f"🔍 Запрашиваю фьючерсную цену для: {full_symbol}")
            response = requests.get(url, timeout=10)
            data = response.json()
        
        # Проверяем структуру ответа
        if data.get('retCode') != 0:
            print(f"❌ Ошибка API: {data.get('retMsg')}")
            return None, clean_symbol
            
        if 'result' not in data or 'list' not in data['result']:
            print(f"❌ Неверная структура ответа API")
            return None, clean_symbol
            
        tickers = data['result']['list']
        if not tickers:
            print(f"❌ Нет данных для символа {full_symbol}")
            return None, clean_symbol
            
        # Берем первый тикер из списка
        ticker = tickers[0]
        
        # Пробуем разные поля с ценой в порядке приоритета
        price_fields = ['lastPrice', 'markPrice', 'indexPrice', 'bid1Price', 'ask1Price']
        current_price = None
        
        for field in price_fields:
            if field in ticker and ticker[field]:
                try:
                    current_price = float(ticker[field])
                    print(f"✅ Цена получена из поля {field}: {full_symbol} = ${current_price:.6f}")
                    break
                except (ValueError, TypeError):
                    continue
        
        if current_price is None:
            print(f"❌ Не найдено поле с ценой в ответе")
            return None, clean_symbol
        
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

def format_price(price):
    """Форматирует цену с 6 знаками после запятой"""
    return f"{price:.6f}".rstrip('0').rstrip('.') if '.' in f"{price:.6f}" else f"{price:.6f}"

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
    
    welcome_text = """💰 Привет! Я бот для отслеживания цен крипто монет на Bybit.

📊 Поддерживаются ВСЕ монеты с Bybit (спотовые и фьючерсы)
💎 Точность: 6 знаков после запятой

📝 Просто напиши: ТИКЕР ЦЕНА
Примеры:
• BTC 50000
• ETH 3500.50
• SHIB 0.000045
• PEPE 0.00000123

🔔 Я пришлю уведомление когда цена достигнет указанного значения!"""
    
    bot.send_message(message.chat.id, welcome_text)

@bot.message_handler(commands=['status'])
def status(message):
    alerts_count = len(get_all_alerts())
    
    # Проверяем текущую цену BTC для демонстрации
    btc_price, _ = get_current_price("BTC")
    price_info = f"\n💰 BTC сейчас: ${format_price(btc_price)}" if btc_price else ""
    
    bot.send_message(message.chat.id, f"✅ Бот работает!\nАктивных запросов: {alerts_count}{price_info}\n\nИспользуй:\n/testprice - проверить цену\n/checknow - мои алерты\n/myalerts - список алертов\n/search - поиск монет")

@bot.message_handler(commands=['search'])
def search_coins(message):
    """Поиск доступных монет"""
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "❌ Использование: /search НАЗВАНИЕ\nПример: /search BTC\n/search DOGE")
            return
            
        search_term = parts[1].upper()
        available_symbols = get_available_symbols()
        
        if not available_symbols:
            bot.send_message(message.chat.id, "❌ Не удалось получить список монет. Попробуйте позже.")
            return
        
        # Ищем совпадения
        matches = [symbol for symbol in available_symbols if search_term in symbol]
        
        if not matches:
            bot.send_message(message.chat.id, f"❌ Монеты содержащие '{search_term}' не найдены.\nПопробуйте другой запрос.")
            return
        
        # Показываем первые 20 результатов
        matches = matches[:20]
        response = f"🔍 Найдено монет: {len(matches)}\n\n"
        
        for i, symbol in enumerate(matches, 1):
            # Получаем текущую цену для отображения
            current_price, full_symbol = get_current_price(symbol)
            price_display = f"${format_price(current_price)}" if current_price else "❌ ошибка"
            response += f"{i}. {symbol}: {price_display}\n"
            
            # Если сообщение становится слишком длинным, отправляем и начинаем новое
            if len(response) > 3500 and i < len(matches):
                bot.send_message(message.chat.id, response)
                response = f"🔍 Продолжение ({i+1}-{min(i+10, len(matches))}):\n\n"
        
        if len(matches) == 20:
            response += f"\n⚠️ Показаны первые 20 результатов. Уточните запрос для более точного поиска."
        
        bot.send_message(message.chat.id, response)
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка поиска: {e}")

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

@bot.message_handler(commands=['userlist'])
def user_list(message):
    """Список всех пользователей (только для администратора)"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
        return
        
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Получаем всех пользователей с количеством их алертов
    cursor.execute('''
    SELECT u.user_id, u.username, u.first_name, u.last_name, u.created_at, u.last_activity, 
           COUNT(a.id) as alert_count
    FROM users u 
    LEFT JOIN alerts a ON u.user_id = a.user_id 
    GROUP BY u.user_id 
    ORDER BY u.created_at DESC
    ''')
    users = cursor.fetchall()
    
    conn.close()
    
    if not users:
        bot.send_message(message.chat.id, "📭 В базе нет пользователей")
        return
    
    # Разбиваем на части, если пользователей много
    user_count = len(users)
    response = f"👥 ВСЕ ПОЛЬЗОВАТЕЛИ: {user_count}\n\n"
    
    for i, user in enumerate(users, 1):
        user_id, username, first_name, last_name, created_at, last_activity, alert_count = user
        
        # Форматируем даты
        created = created_at[:16] if created_at else "неизвестно"
        last_active = last_activity[:16] if last_activity else "неизвестно"
        
        user_info = f"#{i} 👤 ID: {user_id}\n"
        if username:
            user_info += f"   @{username}\n"
        if first_name:
            user_info += f"   Имя: {first_name}"
            if last_name:
                user_info += f" {last_name}"
            user_info += "\n"
        user_info += f"   📅 Регистрация: {created}\n"
        user_info += f"   ⏰ Последняя активность: {last_active}\n"
        user_info += f"   🔔 Алертов: {alert_count}\n"
        user_info += "   ───────────────────\n"
        
        # Если сообщение становится слишком длинным, отправляем и начинаем новое
        if len(response + user_info) > 4000:
            bot.send_message(message.chat.id, response)
            response = "👥 ПРОДОЛЖЕНИЕ:\n\n" + user_info
        else:
            response += user_info
    
    bot.send_message(message.chat.id, response)

@bot.message_handler(commands=['userinfo'])
def user_info(message):
    """Информация о конкретном пользователе (только для администратора)"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
        return
        
    try:
        # Парсим команду: /userinfo 123456789
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "❌ Использование: /userinfo USER_ID\nПример: /userinfo 123456789")
            return
            
        target_user_id = int(parts[1])
        
        conn = sqlite3.connect('alerts.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Информация о пользователе
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (target_user_id,))
        user_data = cursor.fetchone()
        
        if not user_data:
            bot.send_message(message.chat.id, f"❌ Пользователь с ID {target_user_id} не найден")
            conn.close()
            return
        
        # Алерты пользователя
        cursor.execute('SELECT symbol, target_price, alert_type, created_at FROM alerts WHERE user_id = ? ORDER BY created_at DESC', (target_user_id,))
        user_alerts = cursor.fetchall()
        
        conn.close()
        
        user_id, username, first_name, last_name, created_at, last_activity = user_data
        
        response = f"👤 ДЕТАЛЬНАЯ ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:\n\n"
        response += f"🆔 ID: {user_id}\n"
        response += f"👤 Username: @{username if username else 'не указан'}\n"
        response += f"📛 Имя: {first_name if first_name else 'не указано'}\n"
        response += f"📛 Фамилия: {last_name if last_name else 'не указана'}\n"
        response += f"📅 Дата регистрации: {created_at}\n"
        response += f"⏰ Последняя активность: {last_activity}\n"
        response += f"🔔 Всего алертов: {len(user_alerts)}\n\n"
        
        if user_alerts:
            response += "📋 ПОСЛЕДНИЕ АЛЕРТЫ:\n"
            for i, alert in enumerate(user_alerts[:10], 1):  # Показываем последние 10
                symbol, target_price, alert_type, created_at = alert
                icon = "📈" if alert_type == "UP" else "📉"
                response += f"{i}. {icon} {symbol} -> ${format_price(target_price)} ({created_at[:16]})\n"
            if len(user_alerts) > 10:
                response += f"\n... и еще {len(user_alerts) - 10} алертов"
        else:
            response += "📭 У пользователя нет активных алертов"
        
        bot.send_message(message.chat.id, response)
        
    except ValueError:
        bot.send_message(message.chat.id, "❌ USER_ID должен быть числом!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['recent_users'])
def recent_users(message):
    """Недавно зарегистрированные пользователи (только для администратора)"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
        return
        
    try:
        # Парсим количество дней: /recent_users 7
        parts = message.text.split()
        days = 7  # по умолчанию за 7 дней
        if len(parts) >= 2:
            days = int(parts[1])
        
        conn = sqlite3.connect('alerts.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT user_id, username, first_name, last_name, created_at, 
               (SELECT COUNT(*) FROM alerts WHERE user_id = users.user_id) as alert_count
        FROM users 
        WHERE created_at > datetime("now", "-? days") 
        ORDER BY created_at DESC
        ''', (days,))
        
        recent_users = cursor.fetchall()
        conn.close()
        
        if not recent_users:
            bot.send_message(message.chat.id, f"📭 Нет новых пользователей за последние {days} дней")
            return
        
        response = f"🆕 ПОЛЬЗОВАТЕЛИ ЗА ПОСЛЕДНИЕ {days} ДНЕЙ: {len(recent_users)}\n\n"
        
        for user in recent_users:
            user_id, username, first_name, last_name, created_at, alert_count = user
            
            user_info = f"👤 ID: {user_id}\n"
            if username:
                user_info += f"   @{username}\n"
            if first_name:
                user_info += f"   {first_name}"
                if last_name:
                    user_info += f" {last_name}"
                user_info += "\n"
            user_info += f"   📅 {created_at[:16]}\n"
            user_info += f"   🔔 Алертов: {alert_count}\n"
            user_info += "   ───────────────────\n"
            
            if len(response + user_info) > 4000:
                bot.send_message(message.chat.id, response)
                response = "🆕 ПРОДОЛЖЕНИЕ:\n\n" + user_info
            else:
                response += user_info
        
        bot.send_message(message.chat.id, response)
        
    except ValueError:
        bot.send_message(message.chat.id, "❌ Количество дней должно быть числом!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['testprice'])
def test_price(message):
    """Проверка текущей цены"""
    try:
        symbol = "BTC"
        current_price, full_symbol = get_current_price(symbol)
        
        if current_price:
            response = f"🧪 ТЕКУЩАЯ ЦЕНА:\n\n{full_symbol}\n💰 ${format_price(current_price)}"
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
            response += f"• {icon} {symbol} -> ${format_price(target_price)} ({alert_type})\n"
        bot.send_message(message.chat.id, response)

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
                diff_percent = (diff / target_price) * 100
                
                response += f"• {icon} {symbol}: ${format_price(current_price_now)} / ${format_price(target_price)} ({diff_percent:+.2f}%) - {status}\n"
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
        text = message.text.strip().split()
        
        # Проверяем, не является ли сообщение командой (начинается с /)
        if text[0].startswith('/'):
            # Это команда, которую мы не обработали - игнорируем
            return
        
        if len(text) < 2:
            bot.send_message(message.chat.id, "❌ Напиши в формате: ТИКЕР ЦЕНА\nНапример: BTC 50000 или SHIB 0.000045")
            return

        symbol = text[0].upper()
        try:
            target_price = float(text[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или SHIB 0.000045")
            return

        print(f"🔄 Запрос от {user_id}: {symbol} ${format_price(target_price)}")

        # Проверяем доступность символа
        available_symbols = get_available_symbols()
        if available_symbols and symbol not in available_symbols:
            # Предлагаем похожие символы
            similar = [s for s in available_symbols if symbol in s][:5]
            error_msg = f"❌ Тикер '{symbol}' не найден."
            if similar:
                error_msg += f"\n\nВозможно вы имели в виду:\n" + "\n".join(similar[:5])
            else:
                error_msg += f"\n\nИспользуйте /search {symbol} для поиска доступных монет."
            bot.send_message(message.chat.id, error_msg)
            return

        current_price, full_symbol = get_current_price(symbol)
        
        if current_price is None:
            bot.send_message(message.chat.id, f"❌ Не удалось получить цену для '{symbol}'. Попробуйте позже или используйте /search для поиска монет.")
            return

        # Определяем тип алерта
        alert_type = determine_alert_type(current_price, target_price)
        alert_icon = "📈" if alert_type == "UP" else "📉"

        add_alert(user_id, full_symbol, target_price, current_price, alert_type)
        
        response = f"""{full_symbol}
💰 Текущая цена: <b>${format_price(current_price)}</b>
{alert_icon} Оповещение при: <b>${format_price(target_price)}</b>
📊 Разница: {((target_price - current_price) / current_price * 100):+.2f}%"""

        bot.send_message(message.chat.id, response, parse_mode='HTML')
        
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
                    print(f"💰 {symbol}: ${format_price(current_price)} / ${format_price(target_price)} ({alert_type})")
                    
                    if should_trigger_alert(current_price, target_price, alert_type):
                        print(f"🚨 АЛЕРТ СРАБОТАЛ! {symbol} {alert_type} ${format_price(target_price)}")
                        try:
                            icon = "📈" if alert_type == "UP" else "📉"
                            direction = "выросла до" if alert_type == "UP" else "упала до"
                            message_text = f"{icon} {symbol} {direction} ${format_price(target_price)}"
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
    
    # Предварительная загрузка списка символов
    print("🔄 Загрузка списка доступных символов...")
    get_available_symbols()
    
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