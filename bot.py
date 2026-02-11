import telebot
import psycopg2
import psycopg2.extras
import requests
import time
import os
import sys
import threading
import signal
import atexit
import socket
import fcntl
import logging
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# === ПОЛНОСТЬЮ ОТКЛЮЧАЕМ ЛОГИ TELEBOT ===
logging.getLogger('telebot').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.WARNING)

print("=== БОТ ЗАПУЩЕН НА RAILWAY ===")

# Токен бота
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

# ID администратора (ЗАМЕНИ НА СВОЙ ID)
ADMIN_ID = 5870642170  # ← ТВОЙ TELEGRAM ID

# === ПОДКЛЮЧЕНИЕ К POSTGRESQL ===
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("❌ ОШИБКА: Переменная DATABASE_URL не найдена! PostgreSQL не подключён.")
    sys.exit(1)

print(f"✅ Подключение к PostgreSQL: {DATABASE_URL.split('@')[1].split('/')[0]}")

def get_db_connection():
    """Создаёт и возвращает соединение с PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    conn.autocommit = False
    return conn

# === ИНИЦИАЛИЗАЦИЯ ТАБЛИЦ (ЕСЛИ ЕЩЁ НЕ СОЗДАНЫ) ===
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Таблица пользователей (users)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            last_activity TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    # Таблица алертов (alerts)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            symbol TEXT NOT NULL,
            target_price NUMERIC NOT NULL,
            current_price NUMERIC NOT NULL,
            alert_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            triggered INTEGER DEFAULT 0
        )
    ''')
    
    # Индексы для ускорения
    cur.execute('CREATE INDEX IF NOT EXISTS idx_alerts_user_id ON alerts(user_id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_alerts_triggered ON alerts(triggered)')
    
    conn.commit()
    cur.close()
    conn.close()
    print("✅ PostgreSQL: таблицы проверены/созданы")

# === ФАЙЛОВАЯ БЛОКИРОВКА (ОДИН ЭКЗЕМПЛЯР) ===
LOCK_FILE = '/tmp/bot.lock'

def acquire_lock():
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (IOError, BlockingIOError):
        print("❌ Другой экземпляр бота уже запущен. Завершаюсь...")
        return None

def release_lock(lock_fd):
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        os.remove(LOCK_FILE)
    except:
        pass

lock_fd = acquire_lock()
if not lock_fd:
    print("❌ Не удалось получить блокировку. Возможно, бот уже запущен.")
    sys.exit(1)

atexit.register(release_lock, lock_fd)

# === ТЕЛЕГРАМ БОТ ===
bot_instance = None
stop_threads = False
polling_active = False

# Сессия requests с повторными попытками
session = requests.Session()
retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504, 429])
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)

def format_price(price):
    """Динамическое форматирование цены"""
    if price >= 1:
        return f"${price:,.2f}"
    else:
        return f"${price:,.8f}"

def create_bot():
    global bot_instance
    try:
        bot_instance = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML', threaded=True)
        print("✅ Бот создан успешно!")
        return bot_instance
    except Exception as e:
        print(f"❌ Ошибка создания бота: {e}")
        return None

# === РАБОТА С POSTGRESQL (ФУНКЦИИ БИЗНЕС-ЛОГИКИ) ===
def add_alert(user_id, symbol, target_price, current_price, alert_type):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Обновляем/добавляем пользователя
        cur.execute('''
            INSERT INTO users (user_id, last_activity) 
            VALUES (%s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET last_activity = NOW()
        ''', (user_id,))
        
        # Добавляем алерт
        cur.execute('''
            INSERT INTO alerts (user_id, symbol, target_price, current_price, alert_type) 
            VALUES (%s, %s, %s, %s, %s)
        ''', (user_id, symbol.upper(), target_price, current_price, alert_type))
        
        conn.commit()
        print(f"✅ Добавлен алерт: {symbol} {alert_type} ${target_price}")
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка добавления алерта: {e}")
        raise
    finally:
        cur.close()
        conn.close()

def get_active_alerts():
    """Получаем не сработавшие алерты (triggered = 0)"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('''
        SELECT id, user_id, symbol, target_price, alert_type 
        FROM alerts 
        WHERE triggered = 0
    ''')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [(row['id'], row['user_id'], row['symbol'], float(row['target_price']), row['alert_type']) for row in rows]

def mark_alert_triggered(alert_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE alerts SET triggered = 1 WHERE id = %s', (alert_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_user_alerts(user_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('''
        SELECT id, symbol, target_price, alert_type 
        FROM alerts 
        WHERE user_id = %s AND triggered = 0
        ORDER BY created_at DESC
    ''', (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [(row['id'], row['symbol'], float(row['target_price']), row['alert_type']) for row in rows]

def get_all_alerts():
    """Для статистики — все алерты"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('SELECT id, user_id, symbol, target_price, current_price, alert_type FROM alerts')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# === BYBIT API ===
def get_current_price(symbol):
    try:
        clean_symbol = symbol.upper().replace('/', '').replace('\\', '').replace('-', '').replace('_', '')
        symbol_variants = []
        if clean_symbol.endswith('USDT'):
            symbol_variants.append(clean_symbol)
            symbol_variants.append(clean_symbol[:-4])
        else:
            symbol_variants.append(f"{clean_symbol}USDT")
            symbol_variants.append(clean_symbol)
        symbol_variants = list(set(symbol_variants))
        for sym in symbol_variants:
            try:
                url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}"
                response = session.get(url, timeout=5)
                if response.status_code != 200:
                    continue
                data = response.json()
                if data.get('retCode') == 0 and 'result' in data and 'list' in data['result']:
                    tickers = data['result']['list']
                    if tickers:
                        ticker = tickers[0]
                        if 'lastPrice' in ticker and ticker['lastPrice']:
                            return float(ticker['lastPrice']), sym
                        elif 'markPrice' in ticker and ticker['markPrice']:
                            return float(ticker['markPrice']), sym
                        elif 'indexPrice' in ticker and ticker['indexPrice']:
                            return float(ticker['indexPrice']), sym
            except:
                continue
        # Поиск по всем тикерам, если точный не найден
        try:
            url = "https://api.bybit.com/v5/market/tickers?category=spot"
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('retCode') == 0 and 'result' in data and 'list' in data['result']:
                    tickers = data['result']['list']
                    for ticker in tickers:
                        ticker_symbol = ticker.get('symbol', '').upper()
                        for sym in symbol_variants:
                            if sym in ticker_symbol or ticker_symbol.replace('USDT', '') == sym.replace('USDT', ''):
                                if 'lastPrice' in ticker and ticker['lastPrice']:
                                    return float(ticker['lastPrice']), ticker_symbol
        except:
            pass
        print(f"❌ Не удалось найти цену для {symbol}")
        return None, symbol
    except Exception as e:
        print(f"❌ Ошибка получения цены для {symbol}: {str(e)[:200]}")
        return None, symbol

def determine_alert_type(current_price, target_price):
    return "UP" if target_price > current_price else "DOWN"

def should_trigger_alert(current_price, target_price, alert_type):
    return current_price >= target_price if alert_type == "UP" else current_price <= target_price

def is_admin(user_id):
    return user_id == ADMIN_ID

# === ОБРАБОТЧИКИ КОМАНД TELEGRAM ===
def setup_bot_handlers(bot):
    
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO users (user_id, username, first_name, last_name, created_at, last_activity) 
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (user_id) DO UPDATE SET 
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_activity = NOW()
        ''', (user_id, username, first_name, last_name))
        conn.commit()
        cur.close()
        conn.close()
        
        welcome_text = """💰 Привет! Я бот для отслеживания цен криптовалют на Bybit.

📊 Просто напиши: ТИКЕР ЦЕНА
Пример: BTC 50000 или MYX 0.1

Я буду следить и пришлю уведомление, когда цена достигнет указанного значения.

📈 Для роста цены укажи цену ВЫШЕ текущей
📉 Для падения цены укажи цену НИЖЕ текущей

✨ Популярные тикеры: BTC, ETH, SOL, ADA, BNB, XRP, DOGE, DOT, AVAX, MATIC, LINK, UNI, LTC, ATOM"""
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
/search SYMBOL - Поиск монеты на Bybit

📝 Как установить алерт:
Просто напиши: ТИКЕР ЦЕНА
Пример: ETH 3500
         SOL 100
         ADA 0.5
         MYX 0.1

💡 Подсказки:
• Можно вводить тикеры с USDT или без
• Цена должна быть числом (можно с точкой)
• Бот поддерживает ВСЕ монеты, доступные на Bybit"""
        bot.send_message(message.chat.id, help_text)
    
    @bot.message_handler(commands=['status'])
    def status(message):
        active_alerts = get_active_alerts()
        alerts_count = len(active_alerts)
        btc_price, btc_symbol = get_current_price("BTC")
        price_info = f"\n💰 {btc_symbol}: {format_price(btc_price)}" if btc_price else "\n⚠️ Не удалось получить цену BTC"
        status_text = f"""✅ БОТ РАБОТАЕТ!

📊 Статистика:
• Активных алертов: {alerts_count}
• Проверка каждые: 5 секунд
• Источник цен: Bybit API{price_info}

⚡ Команды:
/help - справка
/testprice - проверить цену
/myalerts - мои алерты
/search - поиск монеты"""
        bot.send_message(message.chat.id, status_text)
    
    @bot.message_handler(commands=['search'])
    def search_coin(message):
        try:
            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(message.chat.id, "❌ Используй: /search ТИКЕР\nПример: /search MYX")
                return
            symbol = parts[1].upper()
            price, found_symbol = get_current_price(symbol)
            if price:
                bot.send_message(message.chat.id, 
                    f"✅ Монета найдена!\n\n📈 Символ: {found_symbol}\n💰 Цена: {format_price(price)}\n\nТеперь можешь установить алерт:\n{symbol} {(price * 1.1):.8f}")
            else:
                bot.send_message(message.chat.id, 
                    f"❌ Монета '{symbol}' не найдена на Bybit.\n\nПопробуй:\n• Проверить правильность написания\n• Убедиться, что монета торгуется на Bybit\n• Попробовать другой тикер")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка поиска: {str(e)[:100]}")
    
    @bot.message_handler(commands=['testprice'])
    def test_price(message):
        try:
            btc_price, btc_symbol = get_current_price("BTC")
            if btc_price:
                eth_price, eth_symbol = get_current_price("ETH")
                response = f"""🧪 ТЕКУЩИЕ ЦЕНЫ:

{btc_symbol}
💰 {format_price(btc_price)}"""
                if eth_price:
                    response += f"\n\n{eth_symbol}\n💰 {format_price(eth_price)}"
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
                response += f"• {icon} {symbol} -> {format_price(target_price)}\n"
            bot.send_message(message.chat.id, response)
    
    @bot.message_handler(commands=['checknow'])
    def check_now(message):
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
                    response += f"• {icon} {full_symbol}: {format_price(current_price_now)} / {format_price(target_price)} ({diff_text}) - {status}\n"
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
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('DELETE FROM alerts WHERE user_id = %s', (user_id,))
        count = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if count > 0:
            bot.send_message(message.chat.id, f"✅ Удалено {count} алертов!")
        else:
            bot.send_message(message.chat.id, "📭 У тебя не было активных алертов")
    
    # === АДМИНИСТРАТОРСКИЕ КОМАНДЫ ===
    @bot.message_handler(commands=['stats'])
    def show_stats(message):
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
            return
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(DISTINCT user_id) FROM alerts')
        unique_users = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM alerts')
        total_alerts = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM alerts WHERE triggered = 0')
        active_alerts = cur.fetchone()[0]
        cur.close()
        conn.close()
        stats_text = f"""📊 СТАТИСТИКА БОТА:

👥 Всего пользователей: {unique_users}
🔔 Всего алертов: {total_alerts}
🎯 Активных алертов: {active_alerts}"""
        bot.send_message(message.chat.id, stats_text)
    
    @bot.message_handler(commands=['detailed_stats'])
    def detailed_stats(message):
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
            return
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(DISTINCT user_id) FROM users')
        total_users = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM alerts')
        total_alerts = cur.fetchone()[0]
        cur.execute('SELECT COUNT(DISTINCT user_id) FROM users WHERE last_activity > NOW() - INTERVAL \'1 day\'')
        active_1d = cur.fetchone()[0]
        cur.execute('SELECT COUNT(DISTINCT user_id) FROM users WHERE last_activity > NOW() - INTERVAL \'7 days\'')
        active_7d = cur.fetchone()[0]
        cur.execute('SELECT COUNT(DISTINCT user_id) FROM users WHERE last_activity > NOW() - INTERVAL \'30 days\'')
        active_30d = cur.fetchone()[0]
        cur.execute('''
            SELECT symbol, COUNT(*) as count 
            FROM alerts 
            GROUP BY symbol 
            ORDER BY count DESC 
            LIMIT 5
        ''')
        popular_coins = cur.fetchall()
        cur.close()
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
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
            return
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute('''
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.created_at, u.last_activity, 
                   COUNT(a.id) as alert_count
            FROM users u 
            LEFT JOIN alerts a ON u.user_id = a.user_id 
            GROUP BY u.user_id 
            ORDER BY u.created_at DESC
        ''')
        users = cur.fetchall()
        cur.close()
        conn.close()
        if not users:
            bot.send_message(message.chat.id, "📭 В базе нет пользователей")
            return
        user_count = len(users)
        response = f"👥 ВСЕ ПОЛЬЗОВАТЕЛИ: {user_count}\n\n"
        for i, user in enumerate(users, 1):
            user_id = user['user_id']
            username = user['username']
            first_name = user['first_name']
            last_name = user['last_name']
            created_at = user['created_at'].strftime('%Y-%m-%d %H:%M') if user['created_at'] else 'неизвестно'
            last_activity = user['last_activity'].strftime('%Y-%m-%d %H:%M') if user['last_activity'] else 'неизвестно'
            alert_count = user['alert_count']
            user_info = f"#{i} 👤 ID: {user_id}\n"
            if username:
                user_info += f"   @{username}\n"
            if first_name:
                user_info += f"   Имя: {first_name}"
                if last_name:
                    user_info += f" {last_name}"
                user_info += "\n"
            user_info += f"   📅 Регистрация: {created_at}\n"
            user_info += f"   ⏰ Последняя активность: {last_activity}\n"
            user_info += f"   🔔 Алертов: {alert_count}\n"
            user_info += "   ───────────────────\n"
            if len(response + user_info) > 4000:
                bot.send_message(message.chat.id, response)
                response = "👥 ПРОДОЛЖЕНИЕ:\n\n" + user_info
            else:
                response += user_info
        bot.send_message(message.chat.id, response)
    
    @bot.message_handler(commands=['userinfo'])
    def user_info(message):
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
            return
        try:
            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(message.chat.id, "❌ Использование: /userinfo USER_ID\nПример: /userinfo 123456789")
                return
            target_user_id = int(parts[1])
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute('SELECT * FROM users WHERE user_id = %s', (target_user_id,))
            user_data = cur.fetchone()
            if not user_data:
                bot.send_message(message.chat.id, f"❌ Пользователь с ID {target_user_id} не найден")
                cur.close()
                conn.close()
                return
            cur.execute('''
                SELECT symbol, target_price, alert_type, created_at 
                FROM alerts 
                WHERE user_id = %s 
                ORDER BY created_at DESC
            ''', (target_user_id,))
            user_alerts = cur.fetchall()
            cur.close()
            conn.close()
            created_at = user_data['created_at'].strftime('%Y-%m-%d %H:%M:%S') if user_data['created_at'] else 'неизвестно'
            last_activity = user_data['last_activity'].strftime('%Y-%m-%d %H:%M:%S') if user_data['last_activity'] else 'неизвестно'
            response = f"👤 ДЕТАЛЬНАЯ ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:\n\n"
            response += f"🆔 ID: {user_data['user_id']}\n"
            response += f"👤 Username: @{user_data['username'] if user_data['username'] else 'не указан'}\n"
            response += f"📛 Имя: {user_data['first_name'] if user_data['first_name'] else 'не указано'}\n"
            response += f"📛 Фамилия: {user_data['last_name'] if user_data['last_name'] else 'не указана'}\n"
            response += f"📅 Дата регистрации: {created_at}\n"
            response += f"⏰ Последняя активность: {last_activity}\n"
            response += f"🔔 Всего алертов: {len(user_alerts)}\n\n"
            if user_alerts:
                response += "📋 ПОСЛЕДНИЕ АЛЕРТЫ:\n"
                for i, alert in enumerate(user_alerts[:10], 1):
                    symbol, target_price, alert_type, created_at = alert
                    icon = "📈" if alert_type == "UP" else "📉"
                    time_str = created_at.strftime('%Y-%m-%d %H:%M') if created_at else ''
                    response += f"{i}. {icon} {symbol} -> {format_price(target_price)} ({time_str})\n"
                if len(user_alerts) > 10:
                    response += f"\n... и еще {len(user_alerts) - 10} алертов"
            else:
                response += "📭 У пользователя нет алертов"
            bot.send_message(message.chat.id, response)
        except ValueError:
            bot.send_message(message.chat.id, "❌ USER_ID должен быть числом!")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)[:100]}")
    
    @bot.message_handler(commands=['recent_users'])
    def recent_users(message):
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
            return
        try:
            parts = message.text.split()
            days = 7
            if len(parts) >= 2:
                days = int(parts[1])
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute('''
                SELECT user_id, username, first_name, last_name, created_at, 
                       (SELECT COUNT(*) FROM alerts WHERE user_id = users.user_id) as alert_count
                FROM users 
                WHERE created_at > NOW() - INTERVAL %s
                ORDER BY created_at DESC
            ''', (f'{days} days',))
            recent_users = cur.fetchall()
            cur.close()
            conn.close()
            if not recent_users:
                bot.send_message(message.chat.id, f"📭 Нет новых пользователей за последние {days} дней")
                return
            response = f"🆕 ПОЛЬЗОВАТЕЛИ ЗА ПОСЛЕДНИЕ {days} ДНЕЙ: {len(recent_users)}\n\n"
            for user in recent_users:
                user_id = user['user_id']
                username = user['username']
                first_name = user['first_name']
                last_name = user['last_name']
                created_at = user['created_at'].strftime('%Y-%m-%d %H:%M') if user['created_at'] else 'неизвестно'
                alert_count = user['alert_count']
                user_info = f"👤 ID: {user_id}\n"
                if username:
                    user_info += f"   @{username}\n"
                if first_name:
                    user_info += f"   {first_name}"
                    if last_name:
                        user_info += f" {last_name}"
                    user_info += "\n"
                user_info += f"   📅 {created_at}\n"
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
            bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)[:100]}")
    
    @bot.message_handler(commands=['dbinfo'])
    def db_info(message):
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "❌ У вас нет доступа к этой команде")
            return
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM alerts")
        alerts_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM alerts WHERE triggered = 0")
        active_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        info = f"""📁 **ИНФОРМАЦИЯ О БАЗЕ ДАННЫХ**

Подключение: PostgreSQL
Пользователей в таблице users: {users_count}
Всего алертов: {alerts_count}
Активных алертов: {active_count}"""
        bot.send_message(message.chat.id, info, parse_mode='Markdown')
    
    # === УСТАНОВКА АЛЕРТА (ОСНОВНАЯ ФУНКЦИЯ) ===
    @bot.message_handler(func=lambda message: True)
    def set_alert(message):
        if message.text.startswith('/'):
            return
        try:
            user_id = message.from_user.id
            text = message.text.strip().split()
            if len(text) < 2:
                bot.send_message(message.chat.id, "❌ Напиши в формате: ТИКЕР ЦЕНА\nНапример: BTC 50000 или MYX 0.1")
                return
            symbol = text[0].upper().replace('$', '').replace(',', '')
            try:
                target_price = float(text[1].replace('$', '').replace(',', ''))
            except ValueError:
                bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или MYX 0.12345678")
                return
            if target_price <= 0:
                bot.send_message(message.chat.id, "❌ Цена должна быть больше нуля!")
                return
            current_price, full_symbol = get_current_price(symbol)
            if current_price is None:
                error_text = f"""❌ Не удалось найти '{symbol}' на Bybit.

💡 Возможные причины:
• Тикер написан с ошибкой
• Монета не торгуется на Bybit
• Проблемы с API Bybit

✨ Попробуй:
• Проверить правильность тикера
• Использовать команду /search {symbol}
• Попробовать популярные тикеры: BTC, ETH, SOL, ADA"""
                bot.send_message(message.chat.id, error_text)
                return
            alert_type = determine_alert_type(current_price, target_price)
            alert_icon = "📈" if alert_type == "UP" else "📉"
            direction = "выше текущей" if alert_type == "UP" else "ниже текущей"
            add_alert(user_id, full_symbol, target_price, current_price, alert_type)
            response = f"""✅ АЛЕРТ УСТАНОВЛЕН!

{full_symbol}
💰 Текущая цена: {format_price(current_price)}
{alert_icon} Оповещение при: <b>{format_price(target_price)}</b>
🎯 Направление: цена {direction}"""
            bot.send_message(message.chat.id, response, parse_mode='HTML')
        except ValueError:
            bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или MYX 0.12345678")
        except Exception as e:
            error_msg = str(e)[:100]
            bot.send_message(message.chat.id, f"❌ Ошибка: {error_msg}\nПопробуй еще раз")
            print(f"❌ Ошибка установки алерта: {e}")

# === ФОНОВАЯ ПРОВЕРКА ЦЕН ===
def check_prices():
    print("🔄 Фоновая проверка цен ЗАПУЩЕНА!")
    price_cache = {}
    cache_time = {}
    CACHE_DURATION = 5
    iteration = 0
    while not stop_threads:
        try:
            iteration += 1
            alerts = get_active_alerts()
            if alerts and iteration % 10 == 0:
                print(f"🔍 Проверяю {len(alerts)} активных алертов (итерация {iteration})...")
            if alerts:
                alerts_by_symbol = {}
                symbols_to_check = set()
                for alert in alerts:
                    alert_id, user_id, symbol, target_price, alert_type = alert
                    if symbol not in alerts_by_symbol:
                        alerts_by_symbol[symbol] = []
                    alerts_by_symbol[symbol].append(alert)
                    symbols_to_check.add(symbol)
                current_prices = {}
                for symbol in symbols_to_check:
                    if symbol in price_cache and symbol in cache_time:
                        if time.time() - cache_time[symbol] < CACHE_DURATION:
                            current_prices[symbol] = price_cache[symbol]
                            continue
                    price, found_symbol = get_current_price(symbol)
                    if price:
                        current_prices[symbol] = price
                        price_cache[symbol] = price
                        cache_time[symbol] = time.time()
                    else:
                        if symbol in price_cache:
                            del price_cache[symbol]
                        if symbol in cache_time:
                            del cache_time[symbol]
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
                                message_text = f"{icon} {symbol} {direction} {format_price(target_price)}"
                                global bot_instance
                                if bot_instance:
                                    bot_instance.send_message(user_id, message_text)
                                    print(f"✅ Уведомление отправлено: {symbol} для пользователя {user_id}")
                                mark_alert_triggered(alert_id)
                                triggered_count += 1
                                if symbol in price_cache:
                                    del price_cache[symbol]
                                if symbol in cache_time:
                                    del cache_time[symbol]
                            except Exception as e:
                                print(f"❌ Ошибка отправки уведомления: {str(e)[:100]}")
                if triggered_count > 0:
                    print(f"🎯 Сработало {triggered_count} алертов")
            time.sleep(5)
        except Exception as e:
            print(f"❌ Ошибка в фоновой проверке: {str(e)[:100]}")
            time.sleep(5)

def stop_bot():
    global stop_threads, polling_active
    print("🛑 Остановка бота...")
    stop_threads = True
    time.sleep(2)
    session.close()
    release_lock(lock_fd)
    print("✅ Бот остановлен")

def signal_handler(signum, frame):
    print(f"\n🛑 Получен сигнал {signum}. Останавливаю бота...")
    stop_bot()
    sys.exit(0)

def run_bot():
    global stop_threads, polling_active, bot_instance
    print("🔄 Инициализация...")
    init_db()
    print("🔄 Запуск фоновой проверки...")
    price_thread = threading.Thread(target=check_prices)
    price_thread.daemon = True
    price_thread.start()
    print("✅ ВСЕ СИСТЕМЫ ЗАПУЩЕНЫ")
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(stop_bot)
    
    while not stop_threads:
        try:
            print("🤖 Создаю новый экземпляр бота...")
            bot_instance = create_bot()
            if not bot_instance:
                print("❌ Не удалось создать бота. Жду 10 секунд...")
                time.sleep(10)
                continue
            print("🔄 Настраиваю обработчики команд...")
            setup_bot_handlers(bot_instance)
            print("🤖 Бот начинает опрос Telegram...")
            bot_instance.remove_webhook()
            time.sleep(1)
            time.sleep(2)  # Задержка для избежания 409
            polling_active = True
            bot_instance.polling(
                none_stop=True,
                interval=0,
                timeout=30,
                long_polling_timeout=30,
                allowed_updates=None,
                restart_on_change=False
            )
        except telebot.apihelper.ApiTelegramException as e:
            polling_active = False
            if "Conflict: terminated by other getUpdates request" in str(e):
                # Полностью игнорируем, без лога
                time.sleep(5)
                continue
            else:
                print(f"❌ Ошибка Telegram API: {e}")
                time.sleep(10)
        except Exception as e:
            polling_active = False
            print(f"❌ Критическая ошибка: {e}")
            time.sleep(10)
        finally:
            polling_active = False
            if not stop_threads:
                print("🔄 Перезапуск бота через 5 секунд...")
                time.sleep(5)

if __name__ == "__main__":
    run_bot()
