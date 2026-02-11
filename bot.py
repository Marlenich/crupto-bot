import telebot
import sqlite3
import requests
import time
import os
import sys
import threading
import signal
import atexit
import socket
import fcntl
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

print("=== БОТ ЗАПУЩЕН НА RAILWAY ===")

# Токен бота
TELEGRAM_BOT_TOKEN = '7791402185:AAHqmitReQZjuHl7ZHV2VzPXTyFT9BUXVyU'

# ID администратора (ЗАМЕНИ НА СВОЙ ID)
ADMIN_ID = 123456789  # ЗАМЕНИ НА СВОЙ TELEGRAM ID

# Файл блокировки для предотвращения запуска нескольких экземпляров
LOCK_FILE = '/tmp/bot.lock'

def acquire_lock():
    """Приобретает блокировку файла"""
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (IOError, BlockingIOError):
        print("❌ Другой экземпляр бота уже запущен. Завершаюсь...")
        return None

def release_lock(lock_fd):
    """Освобождает блокировку файла"""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        os.remove(LOCK_FILE)
    except:
        pass

# Пытаемся получить блокировку
lock_fd = acquire_lock()
if not lock_fd:
    print("❌ Не удалось получить блокировку. Возможно, бот уже запущен.")
    sys.exit(1)

# Регистрируем освобождение блокировки при выходе
atexit.register(release_lock, lock_fd)

if not TELEGRAM_BOT_TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не найден!")
    release_lock(lock_fd)
    exit()

print(f"✅ Токен получен! Длина: {len(TELEGRAM_BOT_TOKEN)} символов")
print(f"✅ Блокировка получена. PID: {os.getpid()}")

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
        # Очищаем символ от лишних символов
        clean_symbol = symbol.upper().replace('/', '').replace('\\', '').replace('-', '').replace('_', '')
        
        # Проверяем различные варианты формата символа
        symbol_variants = []
        
        if clean_symbol.endswith('USDT'):
            symbol_variants.append(clean_symbol)
            symbol_variants.append(clean_symbol[:-4])  # Без USDT
        else:
            symbol_variants.append(f"{clean_symbol}USDT")
            symbol_variants.append(clean_symbol)
        
        # Уникальные варианты
        symbol_variants = list(set(symbol_variants))
        
        for sym in symbol_variants:
            try:
                url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}"
                response = session.get(url, timeout=5)
                
                if response.status_code != 200:
                    continue
                
                data = response.json()
                
                # Проверяем структуру ответа
                if data.get('retCode') == 0 and 'result' in data and 'list' in data['result']:
                    tickers = data['result']['list']
                    if tickers:
                        ticker = tickers[0]
                        
                        # Пробуем разные поля с ценой
                        if 'lastPrice' in ticker and ticker['lastPrice']:
                            current_price = float(ticker['lastPrice'])
                            return current_price, sym
                        elif 'markPrice' in ticker and ticker['markPrice']:
                            current_price = float(ticker['markPrice'])
                            return current_price, sym
                        elif 'indexPrice' in ticker and ticker['indexPrice']:
                            current_price = float(ticker['indexPrice'])
                            return current_price, sym
            except:
                continue
        
        # Если не нашли в споте, пробуем другие категории
        for category in ['linear', 'inverse']:
            for sym in symbol_variants:
                try:
                    url = f"https://api.bybit.com/v5/market/tickers?category={category}&symbol={sym}"
                    response = session.get(url, timeout=5)
                    
                    if response.status_code != 200:
                        continue
                    
                    data = response.json()
                    
                    if data.get('retCode') == 0 and 'result' in data and 'list' in data['result']:
                        tickers = data['result']['list']
                        if tickers:
                            ticker = tickers[0]
                            
                            if 'lastPrice' in ticker and ticker['lastPrice']:
                                current_price = float(ticker['lastPrice'])
                                return current_price, sym
                except:
                    continue
        
        # Пробуем получить все тикеры и найти нужный
        try:
            url = "https://api.bybit.com/v5/market/tickers?category=spot"
            response = session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('retCode') == 0 and 'result' in data and 'list' in data['result']:
                    tickers = data['result']['list']
                    
                    # Ищем похожий символ
                    for ticker in tickers:
                        ticker_symbol = ticker.get('symbol', '').upper()
                        
                        # Проверяем различные варианты
                        for sym in symbol_variants:
                            if sym in ticker_symbol or ticker_symbol.replace('USDT', '') == sym.replace('USDT', ''):
                                if 'lastPrice' in ticker and ticker['lastPrice']:
                                    current_price = float(ticker['lastPrice'])
                                    return current_price, ticker_symbol
        except:
            pass
        
        print(f"❌ Не удалось найти цену для {symbol}")
        return None, symbol
        
    except Exception as e:
        print(f"❌ Ошибка получения цены для {symbol}: {str(e)[:200]}")
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
Пример: BTC 50000 или MYX 0.1

Я буду следить и пришлю уведомление, когда цена достигнет указанного значения.

📈 Для роста цены (BUY) укажи цену ВЫШЕ текущей
📉 Для падения цены (SELL) укажи цену НИЖЕ текущей

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
• Можно вводить тикеры с USDT или без (BTC или BTCUSDT)
• Цена должна быть числом (можно с точкой)
• Бот поддерживает ВСЕ монеты, доступные на Bybit"""
        
        bot.send_message(message.chat.id, help_text)
    
    @bot.message_handler(commands=['status'])
    def status(message):
        active_alerts = get_active_alerts()
        alerts_count = len(active_alerts)
        
        # Проверяем текущую цену BTC для демонстрации
        btc_price, btc_symbol = get_current_price("BTC")
        if btc_price:
            price_info = f"\n💰 {btc_symbol}: ${btc_price:,.6f}"
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
/myalerts - мои алерты
/search - поиск монеты"""
        
        bot.send_message(message.chat.id, status_text)
    
    @bot.message_handler(commands=['search'])
    def search_coin(message):
        """Поиск монеты на Bybit"""
        try:
            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(message.chat.id, "❌ Используй: /search ТИКЕР\nПример: /search MYX")
                return
            
            symbol = parts[1].upper()
            price, found_symbol = get_current_price(symbol)
            
            if price:
                bot.send_message(message.chat.id, f"✅ Монета найдена!\n\n📈 Символ: {found_symbol}\n💰 Цена: ${price:,.6f}\n\nТеперь можешь установить алерт:\n{symbol} {price * 1.1:.6f}")
            else:
                bot.send_message(message.chat.id, f"❌ Монета '{symbol}' не найдена на Bybit.\n\nПопробуй:\n• Проверить правильность написания\n• Убедиться, что монета торгуется на Bybit\n• Попробовать другой тикер")
                
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка поиска: {str(e)[:100]}")
    
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
💰 ${current_price:,.6f}"""
                
                if eth_price:
                    response += f"\n\n{eth_symbol}\n💰 ${eth_price:,.6f}"
                
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
                response += f"• {icon} {symbol} -> ${target_price:,.6f}\n"
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
                    
                    response += f"• {icon} {full_symbol}: ${current_price_now:,.6f} / ${target_price:,.6f} ({diff_text}) - {status}\n"
                else:
                    response += f"• {symbol}: ❌ ошибка получения цены\n"
            
            if triggered_count > 0:
                response += f"\n🎯 Готово к отправку: {triggered_count} алертов"
            
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
                bot.send_message(message.chat.id, "❌ Напиши в формате: ТИКЕР ЦЕНА\nНапример: BTC 50000 или MYX 0.1")
                return

            symbol = text[0].upper().replace('$', '').replace(',', '')
            try:
                target_price = float(text[1].replace('$', '').replace(',', ''))
            except ValueError:
                bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или MYX 0.123456")
                return

            # Проверяем валидность цены
            if target_price <= 0:
                bot.send_message(message.chat.id, "❌ Цена должна быть больше нуля!")
                return

            # Получаем текущую цену
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

            # Определяем тип алерта
            alert_type = determine_alert_type(current_price, target_price)
            alert_icon = "📈" if alert_type == "UP" else "📉"
            direction = "выше текущей" if alert_type == "UP" else "ниже текущей"

            # Добавляем алерт
            add_alert(user_id, full_symbol, target_price, current_price, alert_type)
            
            response = f"""✅ АЛЕРТ УСТАНОВЛЕН!

{full_symbol}
💰 Текущая цена: ${current_price:,.6f}
{alert_icon} Оповещение при: <b>${target_price:,.6f}</b>
🎯 Направление: цена {direction}"""

            bot.send_message(message.chat.id, response, parse_mode='HTML')
            
        except ValueError:
            bot.send_message(message.chat.id, "❌ Цена должна быть числом!\nПример: BTC 50000 или MYX 0.123456")
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
                    price, found_symbol = get_current_price(symbol)
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
                                
                                message_text = f"{icon} {symbol} {direction} ${target_price:,.6f}"
                                
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
    
    # Освобождаем блокировку
    release_lock(lock_fd)
    
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
            
            # Используем только один long polling запрос
            bot_instance.polling(
                none_stop=True,
                interval=0,  # Немедленно после получения обновлений
                timeout=30,  # Таймаут для long polling
                long_polling_timeout=30,
                allowed_updates=None,
                restart_on_change=False
            )
            
        except telebot.apihelper.ApiTelegramException as e:
            polling_active = False
            
            if "Conflict: terminated by other getUpdates request" in str(e):
                print("⚠️ Критическая ошибка 409: Обнаружен другой запущенный экземпляр бота")
                print("🛑 Завершаю работу, так как у нас есть файловая блокировка")
                print("ℹ️ Это может означать, что на Railway запущено несколько реплик")
                
                # Выходим из программы
                stop_bot()
                sys.exit(1)
                
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
