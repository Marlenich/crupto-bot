import telebot
import sqlite3
import requests
import time
import os
import sys
import threading
import signal
import atexit
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

print("=== БОТ ЗАПУЩЕН НА RAILWAY ===")

# Токен бота
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

# ID администратора (ЗАМЕНИ НА СВОЙ ID)
ADMIN_ID = 123456789  # ЗАМЕНИ НА СВОЙ TELEGRAM ID

if not TELEGRAM_BOT_TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не найден!")
    exit()

print(f"✅ Токен получен! Длина: {len(TELEGRAM_BOT_TOKEN)} символов")

# Глобальные флаги и переменные
bot_instance = None
stop_threads = False
polling_active = False

# Создаем сессию requests с повторными попытками
session = requests.Session()
retry = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504, 429],
)
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)

# Список популярных криптовалют для подсказок
POPULAR_COINS = ["BTC", "ETH", "SOL", "ADA", "BNB", "XRP", "DOGE", "DOT", "AVAX", "MATIC", "LINK", "UNI", "LTC", "ATOM"]

def create_bot():
    """Создает новый экземпляр бота"""
    global bot_instance
    try:
        bot_instance = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML', threaded=True)
        print("✅ Бот создан успешно!")
        return bot_instance
    except Exception as e:
        print(f"❌ Ошибка создания бота: {e}")
        return None

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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        triggered INTEGER DEFAULT 0
    )
    ''')
    
    # Индексы для ускорения поиска
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_user_id ON alerts(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_triggered ON alerts(triggered)')
    
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
    print(f"✅ Добавлен алерт: {symbol} {alert_type} ${target_price}")

def get_active_alerts():
    """Получаем только не сработавшие алерты"""
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, user_id, symbol, target_price, alert_type FROM alerts WHERE triggered = 0')
    all_alerts = cursor.fetchall()
    conn.close()
    return all_alerts

def mark_alert_triggered(alert_id):
    """Помечаем алерт как сработавший"""
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('UPDATE alerts SET triggered = 1 WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()

def get_user_alerts(user_id):
    conn = sqlite3.connect('alerts.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, symbol, target_price, alert_type FROM alerts WHERE user_id = ? AND triggered = 0', (user_id,))
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
        response = session.get(url, timeout=5)
        
        if response.status_code != 200:
            print(f"❌ API вернул статус {response.status_code} для {symbol}")
            return None, symbol
        
        data = response.json()
        
        # Проверяем структуру ответа
        if data.get('retCode') != 0:
            error_msg = data.get('retMsg', 'Unknown error')
            print(f"❌ Ошибка API для {symbol}: {error_msg}")
            return None, symbol
            
        if 'result' not in data or 'list' not in data['result']:
            print(f"❌ Неверная структура ответа API для {symbol}")
            return None, symbol
            
        tickers = data['result']['list']
        if not tickers:
            print(f"❌ Нет данных для символа {symbol} (пустой список)")
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
            print(f"❌ Не найдено поле с ценой для {symbol}")
            return None, symbol
        
        print(f"✅ Цена получена: {full_symbol} = ${current_price}")
        return current_price, full_symbol
        
    except requests.exceptions.Timeout:
        print(f"⏰ Таймаут при запросе цены для {symbol}")
        return None, symbol
    except requests.exceptions.ConnectionError:
        print(f"🔌 Ошибка соединения для {symbol}")
        return None, symbol
    except Exception as e:
        print(f"❌ Ошибка получения цены для {symbol}: {str(e)[:100]}")
        return None, symbol

def get_popular_coins_list():
    """Формирует список популярных монет для подсказки"""
    coins_text = ""
    for i in range(0, len(POPULAR_COINS), 4):
        chunk = POPULAR_COINS[i:i+4]
        coins_text += "• " + ", ".join(chunk) + "\n"
    return coins_text.strip()

def determine_alert_type(current_price, target_price):
    """Определяем тип алерта: UP (рост) или DOWN (падение)"""
    if target_price > current_price:
        return "UP"  # Ждем роста цены
    else:
        return "DOWN"  # Ждем падения цены

def should_trigger_alert(current_price, target_price, alert_type):
    """Определяем, должен ли сработать алерт"""
    if alert_type == "UP":
        return current_price >= target_price
    else:  # DOWN
        return current_price <= target_price

def is_admin(user_id):
    """Проверяет, является ли пользователь администратором"""
    return user_id == ADMIN_ID

# Функции-обработчики команд (будут переопределены после создания бота)
def setup_bot_handlers(bot):
    """Настраивает обработчики команд для бота"""
    
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
        
        welcome_text = """💰 Привет! Я бот для отслеживания цен криптовалют на Bybit.

📊 Просто напиши: ТИКЕР ЦЕНА
Пример: BTC 50000

Я буду следить и пришлю уведомление, когда цена достигнет указанного значения.

📈 Для роста цены (BUY) укажи цену ВЫШЕ текущей
📉 Для падения цены (SELL) укажи цену НИЖЕ текущей

✨ Популярные тикеры:
""" + get_popular_coins_list()
        
        bot.send_message(message.chat.id, welcome_text)
    
    @bot.message_handler(commands=['help'])
    def send_help(message):
        help_text = """🆘 ПОМОЩЬ ПО КОМАНДАМ:

/start - Запустить бота
/help - Эта справка
/status - Статус бота
/testprice - Проверить цену BTC
/myalerts - Мои активные алерты
/checknow - Проверить все алерты сейчас
/clear - Удалить все мои алерты

📝 Как установить алерт:
Просто напиши: ТИКЕР ЦЕНА
Пример: ETH 3500
         SOL 100
         ADA 0.5

📈 Популярные тикеры:
""" + get_popular_coins_list()
        
        bot.send_message(message.chat.id, help_text)
    
    @bot.message_handler(commands=['status'])
    def status(message):
        active_alerts = get_active_alerts()
        alerts_count = len(active_alerts)
        
        # Проверяем текущую цену BTC для демонстрации
        btc_price, btc_symbol = get_current_price("BTC")
        if btc_price:
            price_info = f"\n💰 {btc_symbol}: ${btc_price:,.2f}"
        else:
            price_info = "\n⚠️ Не удалось получить цену BTC"
        
        status_text = f"""✅ БОТ РАБОТАЕТ!

📊 Статистика:
• Активных алертов: {alerts_count}
• Проверка каждые: 5 секунд
• Источник цен: Bybit API{price_info}

⚡ Команды:
/help - справка
/testprice - проверить цену
/myalerts - мои алерты"""
        
        bot.send_message(message.chat.id, status_text)
    
    @bot.message_handler(commands=['test'])
    def test_bot(message):
        """Тестовая команда для проверки работы бота"""
        try:
            # Проверяем цену BTC
            btc_price, btc_symbol = get_current_price("BTC")
            
            if btc_price:
                test_text = f"""🧪 ТЕСТ БОТА УСПЕШЕН!

✅ Все системы работают:
• Подключение к Telegram: ✓
• Подключение к Bybit API: ✓
• База данных: ✓

📊 Текущая цена:
{btc_symbol}: ${btc_price:,.2f}

🚀 Бот готов к работе!"""
            else:
                test_text = """⚠️ ТЕСТ БОТА С ОШИБКАМИ

❌ Проблемы с подключением к Bybit API
Проверьте интернет соединение или попробуйте позже."""
            
            bot.send_message(message.chat.id, test_text)
            
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка теста: {str(e)[:100]}")

    @bot.message_handler(commands=['testalert'])
    def test_alert_command(message):
        """Создание тестового алерта"""
        user_id = message.from_user.id
        try:
            symbol = "BTC"
            current_price, full_symbol = get_current_price(symbol)
            
            if current_price:
                # Создаем тестовый алерт на $1 выше текущей цены
                test_target = current_price + 1
                
                alert_type = determine_alert_type(current_price, test_target)
                alert_icon = "📈" if alert_type == "UP" else "📉"
                
                add_alert(user_id, full_symbol, test_target, current_price, alert_type)
                
                test_text = f"""🧪 ТЕСТОВЫЙ АЛЕРТ СОЗДАН!

{full_symbol}
💰 Текущая цена: ${current_price:,.2f}
{alert_icon} Оповещение при: <b>${test_target:,.2f}</b>

Алерт будет проверяться каждые 5 секунд.
Цена должна немного вырасти для срабатывания."""
                
                bot.send_message(message.chat.id, test_text, parse_mode='HTML')
            else:
                bot.send_message(message.chat.id, "❌ Не удалось получить текущую цену BTC")
                
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)[:100]}")
    
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
        
        # Активные алерты
        cursor.execute('SELECT COUNT(*) FROM alerts WHERE triggered = 0')
        active_alerts = cursor.fetchone()[0]
        
        conn.close()
        
        stats_text = f"""📊 СТАТИСТИКА БОТА:

👥 Всего пользователей: {unique_users}
🔔 Всего алертов: {total_alerts}
🎯 Активных алертов: {active_alerts}"""

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
    
    @bot.message_handler(commands=['testprice'])
    def test_price(message):
        """Проверка текущей цены"""
        try:
            symbol = "BTC"
            current_price, full_symbol = get_current_price(symbol)
            
            if current_price:
                # Также проверяем ETH для демонстрации
                eth_price, eth_symbol = get_current_price("ETH")
                
                response = f"""🧪 ТЕКУЩИЕ ЦЕНЫ:

{full_symbol}
💰 ${current_price:,.2f}"""
                
                if eth_price:
                    response += f"\n\n{eth_symbol}\n💰 ${eth_price:,.2f}"
                
                bot.send_message(message.chat.id, response)
            else:
                bot.send_message(message.chat.id, "❌ Не удалось получить цену BTC")
                
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)[:100]}")
    
    @bot.message_handler(commands=['myalerts'])
    def list_alerts(message):
        user_id = message.from_user.id
        alerts = get_user_alerts(user_id)
        
        if not alerts:
            bot.send_message(message.chat.id, "📭 У тебя нет активных алертов.\n\nСоздай алерт командой:\nBTC 50000")
        else:
            response = "📋 ТВОИ АКТИВНЫЕ АЛЕРТЫ:\n\n"
            for alert in alerts:
                id, symbol, target_price, alert_type = alert
                icon = "📈" if alert_type == "UP" else "📉"
                response += f"• {icon} {symbol} -> ${target_price:,.2f}\n"
            bot.send_message(message.chat.id, response)
    
    @bot.message_handler(commands=['checknow'])
    def check_now(message):
        """Принудительная проверка всех алертов"""
        user_id = message.from_user.id
        try:
            alerts = get_user_alerts(user_id)
            
            if not alerts:
                bot.send_message(message.chat.id, "📭 У тебя нет активных алертов")
                return
                
            response = "🔍 ПРОВЕРКА АЛЕРТОВ:\n\n"
            triggered_count = 0
            
            for alert in alerts:
                id, symbol, target_price, alert_type = alert
                current_price_now, full_symbol = get_current_price(symbol)
                
                if current_price_now:
                    icon = "📈" if alert_type == "UP" else "📉"
                    
                    if should_trigger_alert(current_price_now, target_price, alert_type):
                        status = "✅ ГОТОВ!"
                        triggered_count += 1
                    else:
                        status = "⏳ жду"
                    
                    diff = current_price_now - target_price
                    diff_percent = (diff / target_price) * 100
                    diff_text = f"+{diff_percent:.2f}%" if diff > 0 else f"{diff_percent:.2f}%"
                    
                    response += f"• {icon} {full_symbol}: ${current_price_now:,.2f} / ${target_price:,.2f} ({diff_text}) - {status}\n"
                else:
                    response += f"• {symbol}: ❌ ошибка получения цены\n"
            
            if triggered_count > 0:
                response += f"\n🎯 Готово к отправке: {triggered_count} алертов"
            
            bot.send_message(message.chat.id, response)
            
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка проверки: {str(e)[:100]}")
    
    @bot.message_handler(commands=['clear'])
    def clear_alerts(message):
        user_id = message.from_user.id
        conn = sqlite3.connect('alerts.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM alerts WHERE user_id = ?', (user_id,))
        count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if count > 0:
            bot.send_message(message.chat.id, f"✅ Удалено {count} алертов!")
        else:
            bot.send_message(message.chat.id, "📭 У тебя не было активных алертов")
    
    # Установка алерта
    @bot.message_handler(func=lambda message: True)
    def set_alert(message):
        # Пропускаем команды
        if message.text.startswith('/'):
            return
            
        try:
            user_id = message.from_user.id
            text = message.text.strip().split()
            
            if len(text) < 2:
                bot.send_message(message.chat.id, "❌ Напиши в формате: ТИКЕР ЦЕНА\nНапример: BTC 50000")
                return

            symbol = text[0].upper().replace('$', '').replace(',', '')
            try:
                target_price = float(text[1].replace('$', '').replace(',', ''))
            except ValueError:
                bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или ETH 3500.50")
                return

            # Проверяем валидность цены
            if target_price <= 0:
                bot.send_message(message.chat.id, "❌ Цена должна быть больше нуля!")
                return

            # Получаем текущую цену
            current_price, full_symbol = get_current_price(symbol)
            
            if current_price is None:
                error_text = f"""❌ Тикер '{symbol}' не найден на Bybit.

✨ Попробуй популярные тикеры:
""" + get_popular_coins_list() + """

📌 Убедись, что вводишь правильный тикер
📌 Только криптовалюты с парами USDT"""
                
                bot.send_message(message.chat.id, error_text)
                return

            # Определяем тип алерта
            alert_type = determine_alert_type(current_price, target_price)
            alert_icon = "📈" if alert_type == "UP" else "📉"
            direction = "выше текущей" if alert_type == "UP" else "ниже текущей"

            # Добавляем алерт
            add_alert(user_id, full_symbol, target_price, current_price, alert_type)
            
            response = f"""✅ АЛЕРТ УСТАНОВЛЕН!

{full_symbol}
💰 Текущая цена: ${current_price:,.2f}
{alert_icon} Оповещение при: <b>${target_price:,.2f}</b>
🎯 Направление: цена {direction}

🔔 Бот будет проверять цену каждые 5 секунд."""

            bot.send_message(message.chat.id, response, parse_mode='HTML')
            
        except ValueError:
            bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или ETH 3500.50")
        except Exception as e:
            error_msg = str(e)[:100]
            bot.send_message(message.chat.id, f"❌ Ошибка: {error_msg}\nПопробуй еще раз")
            print(f"❌ Ошибка установки алерта: {e}")

# Фоновая проверка цен
def check_prices():
    print("🔄 Фоновая проверка цен ЗАПУЩЕНА!")
    
    # Словарь для кэширования цен
    price_cache = {}
    cache_time = {}
    CACHE_DURATION = 5  # секунды
    
    iteration = 0
    
    while not stop_threads:
        try:
            iteration += 1
            alerts = get_active_alerts()
            
            if alerts and iteration % 10 == 0:  # Логируем каждые 10 итераций
                print(f"🔍 Проверяю {len(alerts)} активных алертов (итерация {iteration})...")
            
            if alerts:
                # Группируем алерты по символам
                alerts_by_symbol = {}
                symbols_to_check = set()
                
                for alert in alerts:
                    alert_id, user_id, symbol, target_price, alert_type = alert
                    if symbol not in alerts_by_symbol:
                        alerts_by_symbol[symbol] = []
                    alerts_by_symbol[symbol].append(alert)
                    symbols_to_check.add(symbol)
                
                # Получаем цены для символов (с кэшированием)
                current_prices = {}
                for symbol in symbols_to_check:
                    # Проверяем кэш
                    if symbol in price_cache and symbol in cache_time:
                        if time.time() - cache_time[symbol] < CACHE_DURATION:
                            current_prices[symbol] = price_cache[symbol]
                            continue
                    
                    # Запрашиваем цену
                    price, _ = get_current_price(symbol)
                    if price:
                        current_prices[symbol] = price
                        price_cache[symbol] = price
                        cache_time[symbol] = time.time()
                    else:
                        # Если не удалось получить цену, удаляем из кэша
                        if symbol in price_cache:
                            del price_cache[symbol]
                        if symbol in cache_time:
                            del cache_time[symbol]
                
                # Проверяем алерты
                triggered_count = 0
                for symbol, symbol_alerts in alerts_by_symbol.items():
                    if symbol not in current_prices:
                        continue
                    
                    current_price = current_prices[symbol]
                    
                    for alert in symbol_alerts:
                        alert_id, user_id, symbol, target_price, alert_type = alert
                        
                        if should_trigger_alert(current_price, target_price, alert_type):
                            try:
                                icon = "📈" if alert_type == "UP" else "📉"
                                direction = "выросла до" if alert_type == "UP" else "упала до"
                                message_text = f"{icon} {symbol} {direction} ${target_price:,.2f}"
                                
                                # Используем глобальный экземпляр бота для отправки
                                global bot_instance
                                if bot_instance:
                                    bot_instance.send_message(user_id, message_text)
                                    print(f"✅ Уведомление отправлено: {symbol} для пользователя {user_id}")
                                
                                mark_alert_triggered(alert_id)
                                triggered_count += 1
                                
                                # Удаляем из кэша
                                if symbol in price_cache:
                                    del price_cache[symbol]
                                if symbol in cache_time:
                                    del cache_time[symbol]
                                    
                            except Exception as e:
                                print(f"❌ Ошибка отправки уведомления: {str(e)[:100]}")
                
                if triggered_count > 0:
                    print(f"🎯 Сработало {triggered_count} алертов")
            
            # Ждем 5 секунд между проверками
            time.sleep(5)
                        
        except Exception as e:
            print(f"❌ Ошибка в фоновой проверке: {str(e)[:100]}")
            time.sleep(5)

def stop_bot():
    """Останавливает бота и все потоки"""
    global stop_threads, polling_active
    print("🛑 Остановка бота...")
    stop_threads = True
    
    # Даем время потокам завершиться
    time.sleep(2)
    
    # Закрываем сессию requests
    global session
    session.close()
    
    print("✅ Бот остановлен")

def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown"""
    print(f"\n🛑 Получен сигнал {signum}. Останавливаю бота...")
    stop_bot()
    sys.exit(0)

def run_bot():
    """Основная функция запуска бота"""
    global stop_threads, polling_active, bot_instance
    
    print("🔄 Инициализация...")
    init_db()
    
    print("🔄 Запуск фоновой проверки...")
    price_thread = threading.Thread(target=check_prices)
    price_thread.daemon = True
    price_thread.start()
    
    print("✅ ВСЕ СИСТЕМЫ ЗАПУЩЕНЫ")
    
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Регистрируем функцию остановки при выходе
    atexit.register(stop_bot)
    
    # Основной цикл работы бота
    while not stop_threads:
        try:
            print("🤖 Создаю новый экземпляр бота...")
            bot_instance = create_bot()
            
            if not bot_instance:
                print("❌ Не удалось создать бота. Жду 10 секунд...")
                time.sleep(10)
                continue
            
            # Настраиваем обработчики
            print("🔄 Настраиваю обработчики команд...")
            setup_bot_handlers(bot_instance)
            
            print("🤖 Бот начинает опрос Telegram...")
            
            # Очищаем webhook на всякий случай
            try:
                bot_instance.remove_webhook()
                time.sleep(1)
            except:
                pass
            
            # Запускаем polling с коротким таймаутом
            polling_active = True
            bot_instance.polling(none_stop=True, interval=1, timeout=20, long_polling_timeout=20)
            
        except telebot.apihelper.ApiTelegramException as e:
            polling_active = False
            
            if "Conflict: terminated by other getUpdates request" in str(e):
                print("⚠️ Ошибка 409: Обнаружен другой запущенный экземпляр бота")
                print("🔄 Жду 30 секунд и создаю нового бота...")
                
                # Останавливаем текущий экземпляр
                try:
                    bot_instance.stop_polling()
                except:
                    pass
                
                # Даем время старому экземпляру завершиться
                time.sleep(30)
                
                # Создаем нового бота
                continue
                
            else:
                print(f"❌ Ошибка Telegram API: {e}")
                print("🔄 Перезапуск через 10 секунд...")
                time.sleep(10)
                
        except Exception as e:
            polling_active = False
            print(f"❌ Критическая ошибка: {e}")
            print("🔄 Перезапуск через 10 секунд...")
            time.sleep(10)
            
        finally:
            polling_active = False
            if not stop_threads:
                print("🔄 Перезапуск бота через 5 секунд...")
                time.sleep(5)

# Запуск
if __name__ == "__main__":
    run_bot()
