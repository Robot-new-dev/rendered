import telebot
import json
from datetime import datetime
import os
import uuid
import time
import hashlib
import socket
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import threading

# ========== ОБЩАЯ КОНФИГУРАЦИЯ ==========
# Telegram Bot
BOT_TOKEN = 'Токен бота'
YOUR_CHAT_ID = 'Ваш ID в телеграм'

# Web Panel
DRIVER_PATH = 'C:\\Arduino\\chromedriver-win64\\chromedriver.exe'
PANEL_URL = 'http://192.168.0.102'

# Имена файлов
LAST_MESSAGE_FILE = 'last_message.txt'      # ТОЛЬКО подтвержденные сообщения
APPROVED_FILE = 'approved_messages.txt'     # Все подтвержденные (дописываются)
REJECTED_FILE = 'rejected_messages.txt'     # Все отклоненные
LOG_FILE = 'messages_log.json'              # Полная история

# XPath для веб-панели
TAB_TEXT_XPATH = '/html/body/app-root/div/div[1]/mat-tab-group/mat-tab-header/div[2]/div/div/div[2]'
TEXTAREA_XPATH = '/html/body/app-root/div/div[1]/mat-tab-group/div/mat-tab-body[2]/div/app-tab-texts-panel/div/mat-tab-group/div/mat-tab-body[1]/div/app-tab-texts/div/div/fieldset[2]/div[2]/mat-form-field/div[1]/div[2]/div/textarea'
SUBMIT_BUTTON_XPATH = '/html/body/app-root/div/div[1]/mat-tab-group/div/mat-tab-body[2]/div/app-tab-texts-panel/div/mat-tab-group/div/mat-tab-body[1]/div/app-tab-texts/div/div/fieldset[2]/div[3]/div/div/button'

# ========== НАСТРОЙКИ ПОВТОРНЫХ ПОПЫТОК ==========
MAX_SETUP_ATTEMPTS = 10
MAX_TAB_LOAD_ATTEMPTS = 5
INITIAL_WAIT_TIME = 3
MAX_WAIT_TIME = 10
WAIT_INCREMENT = 1
RETRY_DELAY = 2
FULL_RESET_ATTEMPTS = 3

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
bot = telebot.TeleBot(BOT_TOKEN)
pending_messages = {}

# Переменные для веб-панели
driver = None
last_content_hash = None
last_file_stats = None
page_ready = False
setup_attempts = 0
last_successful_send_time = 0
CONNECTION_TIMEOUT = 30
FULL_RESET_INTERVAL = 300

# Блокировка для синхронизации доступа к файлам
file_lock = threading.Lock()

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛАМИ ==========

def save_approved_message(text):
    """Сохраняет ТОЛЬКО подтвержденное сообщение в отдельный файл (перезаписывает)"""
    with file_lock:
        try:
            # Убеждаемся, что текст не пустой
            if not text or text.strip() == "":
                print("⚠️ Предупреждение: пытаюсь сохранить пустой текст")
                return
            
            # Создаем директорию если нужно
            os.makedirs(os.path.dirname(LAST_MESSAGE_FILE) or '.', exist_ok=True)
            
            # Сохраняем в файл с принудительной записью
            with open(LAST_MESSAGE_FILE, 'w', encoding='utf-8') as f:
                f.write(text)
                f.flush()  # Принудительно записываем на диск
                os.fsync(f.fileno())  # Синхронизируем с файловой системой
            
            # Проверяем, что файл записался
            time.sleep(0.1)  # Даем время на запись
            if os.path.exists(LAST_MESSAGE_FILE):
                with open(LAST_MESSAGE_FILE, 'r', encoding='utf-8') as f:
                    saved_text = f.read()
                    if saved_text == text:
                        print(f"✓ Подтвержденное сообщение успешно сохранено в {LAST_MESSAGE_FILE}")
                        print(f"   Длина текста: {len(text)} символов")
                        print(f"   Предпросмотр: '{text[:50]}...'" if len(text) > 50 else f"   Текст: '{text}'")
                    else:
                        print(f"✗ Ошибка: сохраненный текст не совпадает с исходным!")
            else:
                print(f"✗ Ошибка: файл {LAST_MESSAGE_FILE} не был создан")
                
        except Exception as e:
            print(f"✗ КРИТИЧЕСКАЯ ОШИБКА при записи в {LAST_MESSAGE_FILE}: {e}")
            import traceback
            traceback.print_exc()

def save_message_to_file(message_data, status="pending"):
    """Сохраняет полную информацию о сообщении в JSON-файл"""
    with file_lock:
        try:
            # Пытаемся прочитать существующие данные
            try:
                with open(LOG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = []
            
            # Добавляем новое сообщение
            data.append({
                "timestamp": datetime.now().isoformat(),
                "message_data": message_data,
                "status": status
            })
            
            # Записываем обратно
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            print(f"✓ Сообщение добавлено в {LOG_FILE} (статус: {status})")
        except Exception as e:
            print(f"✗ Ошибка при записи в {LOG_FILE}: {e}")

def update_message_status_in_log(message_id, new_status):
    """Обновляет статус сообщения в JSON-логе"""
    with file_lock:
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Ищем сообщение по ID и обновляем статус
            for item in reversed(data):
                if item['message_data'].get('message_id') == message_id:
                    item['status'] = new_status
                    item['moderated_at'] = datetime.now().isoformat()
                    break
            
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            print(f"✓ Статус сообщения {message_id} обновлен на '{new_status}'")
        except Exception as e:
            print(f"✗ Ошибка при обновлении статуса: {e}")

# ========== ФУНКЦИИ ВЕБ-ПАНЕЛИ ==========

def init_web_panel():
    """Инициализация веб-панели"""
    global driver
    print("🔄 Запуск веб-панели...")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=800,600")
    
    prefs = {
        'profile.default_content_setting_values.notifications': 2,
        'profile.managed_default_content_settings.images': 1,
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    try:
        service = Service(executable_path=DRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(CONNECTION_TIMEOUT)
        driver.implicitly_wait(5)
        print("✅ Веб-панель готова")
        return True
    except Exception as e:
        print(f"❌ Ошибка запуска веб-панели: {e}")
        return False

def check_panel_online():
    """Проверяет доступность панели по сети"""
    try:
        host = PANEL_URL.replace("http://", "").replace("https://", "").split("/")[0]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((host, 80))
        sock.close()
        return result == 0
    except:
        return False

def format_text(text):
    """Добавляет символы цвета к тексту"""
    if not text:
        return text
    return f"{{C#FFFFFF}}{{B#000000}}{text}"

def wait_for_tab_load():
    """Ожидание загрузки вкладки Тексты с увеличением времени ожидания"""
    wait_time = INITIAL_WAIT_TIME
    
    for attempt in range(MAX_TAB_LOAD_ATTEMPTS):
        try:
            print(f"⏳ Ожидание загрузки вкладки... (попытка {attempt + 1}/{MAX_TAB_LOAD_ATTEMPTS}, жду {wait_time} сек)")
            time.sleep(wait_time)
            
            # Пробуем найти поле ввода
            textarea = driver.find_element(By.XPATH, TEXTAREA_XPATH)
            
            if textarea.is_displayed() and textarea.is_enabled():
                print(f"✅ Вкладка загружена после {wait_time} секунд ожидания")
                return True
            else:
                print(f"⚠️ Поле ввода найдено, но не активно")
                
        except (NoSuchElementException, StaleElementReferenceException) as e:
            print(f"⚠️ Поле ввода еще не загрузилось: {e}")
        
        # Увеличиваем время ожидания для следующей попытки
        wait_time = min(wait_time + WAIT_INCREMENT, MAX_WAIT_TIME)
    
    return False

def setup_page():
    """Настройка страницы веб-панели с улучшенной логикой ожидания"""
    global page_ready, setup_attempts, driver
    
    if page_ready:
        return True
    
    if not driver:
        if not init_web_panel():
            return False
    
    setup_attempts += 1
    print(f"\n🔄 Попытка настройки страницы #{setup_attempts}")
    
    try:
        # Сначала проверяем доступность панели
        if not check_panel_online():
            print("⚠️ Панель недоступна по сети")
            return False
        
        print(f"📄 Загружаю страницу панели...")
        
        try:
            driver.get(PANEL_URL)
            print("✅ Страница загружена")
        except TimeoutException:
            print("⚠️ Таймаут при загрузке страницы, пробую продолжить...")
        except Exception as e:
            print(f"⚠️ Ошибка загрузки страницы: {e}")
            return False
        
        # Даем время на загрузку
        time.sleep(3)
        
        # Проверяем, загрузилась ли страница
        if "app-root" not in driver.page_source:
            print("❌ Страница не загрузилась должным образом")
            return False
        
        print("🔍 Ищу вкладку 'Тексты'...")
        try:
            # Ждем появления вкладки
            time.sleep(2)
            tab = driver.find_element(By.XPATH, TAB_TEXT_XPATH)
            print("✅ Вкладка найдена")
        except NoSuchElementException:
            print("❌ Вкладка 'Тексты' не найдена")
            return False
        
        print("🖱️ Кликаю на вкладку 'Тексты'...")
        try:
            tab.click()
        except Exception as e:
            print(f"⚠️ Ошибка при клике на вкладку: {e}")
            # Пробуем другой способ клика
            try:
                driver.execute_script("arguments[0].click();", tab)
            except:
                pass
        
        # Пытаемся дождаться загрузки вкладки несколько раз
        if not wait_for_tab_load():
            print("❌ Не удалось дождаться загрузки вкладки")
            
            # Пробуем обновить страницу и повторить
            if setup_attempts <= FULL_RESET_ATTEMPTS:
                print(f"🔄 Пробую обновить страницу (попытка {setup_attempts}/{FULL_RESET_ATTEMPTS})")
                try:
                    driver.refresh()
                    time.sleep(5)
                    
                    # Повторяем поиск и клик на вкладку
                    tab = driver.find_element(By.XPATH, TAB_TEXT_XPATH)
                    tab.click()
                    
                    if wait_for_tab_load():
                        page_ready = True
                        setup_attempts = 0
                        print("✅ Страница настроена после обновления")
                        return True
                except Exception as e:
                    print(f"⚠️ Ошибка при обновлении страницы: {e}")
            
            return False
        
        page_ready = True
        setup_attempts = 0
        print("✅ Страница настроена и готова к работе")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка настройки: {e}")
        page_ready = False
        
        # Если слишком много неудачных попыток, делаем полный сброс
        if setup_attempts >= MAX_SETUP_ATTEMPTS:
            print(f"⚠️ Достигнуто максимальное количество попыток настройки ({MAX_SETUP_ATTEMPTS})")
            return perform_full_reset()
        
        return False

def perform_full_reset():
    """Полный сброс драйвера и состояния"""
    global driver, page_ready, setup_attempts
    print("\n🔄 ВЫПОЛНЯЮ ПОЛНЫЙ СБРОС СИСТЕМЫ...")
    
    if driver:
        try:
            driver.quit()
            print("✅ Браузер закрыт")
        except Exception as e:
            print(f"⚠️ Ошибка при закрытии браузера: {e}")
    
    driver = None
    page_ready = False
    setup_attempts = 0
    
    # Ждем перед перезапуском
    time.sleep(RETRY_DELAY * 2)
    
    # Пытаемся перезапустить
    if init_web_panel():
        return setup_page()
    
    return False

def send_text_to_panel(text):
    """Отправка текста на веб-панель с улучшенной обработкой ошибок"""
    global page_ready, last_successful_send_time
    
    # Проверяем, нужен ли полный сброс по времени
    current_time = time.time()
    if current_time - last_successful_send_time > FULL_RESET_INTERVAL and last_successful_send_time > 0:
        print("🔄 Выполняю периодический сброс для поддержания стабильности...")
        perform_full_reset()
    
    # Если страница не настроена, пытаемся настроить
    if not page_ready:
        print("🔄 Страница не настроена, пытаюсь настроить...")
        if not setup_page():
            print("❌ Не удалось настроить страницу")
            return False
    
    try:
        formatted_text = format_text(text)
        print(f"📝 Отправляю текст: '{formatted_text[:50]}...'")
        
        # Находим элементы с повторными попытками
        max_retries = 3
        for attempt in range(max_retries):
            try:
                textarea = driver.find_element(By.XPATH, TEXTAREA_XPATH)
                button = driver.find_element(By.XPATH, SUBMIT_BUTTON_XPATH)
                
                # Проверяем, доступны ли элементы
                if textarea.is_displayed() and textarea.is_enabled():
                    break
                else:
                    print(f"⚠️ Элементы не доступны, попытка {attempt + 1}/{max_retries}")
                    time.sleep(1)
            except NoSuchElementException:
                if attempt < max_retries - 1:
                    print(f"⚠️ Элементы не найдены, попытка {attempt + 1}/{max_retries}")
                    time.sleep(1)
                    # Обновляем страницу
                    try:
                        driver.refresh()
                        time.sleep(3)
                    except:
                        pass
                else:
                    raise
        
        # Очищаем поле и вводим текст
        textarea.clear()
        textarea.send_keys(formatted_text)
        
        # Нажимаем кнопку
        button.click()
        
        print(f"✅ Текст успешно отправлен на панель")
        last_successful_send_time = time.time()
        
        # Короткая пауза после отправки
        time.sleep(1)
        
        return True
        
    except TimeoutException:
        print("❌ Таймаут при отправке текста")
        page_ready = False
        return False
        
    except NoSuchElementException as e:
        print(f"❌ Не найден элемент на странице: {e}")
        page_ready = False
        return False
        
    except Exception as e:
        print(f"❌ Неожиданная ошибка при отправке: {e}")
        page_ready = False
        
        # Пробуем восстановить соединение
        try:
            driver.refresh()
            time.sleep(3)
        except:
            pass
            
        return False

def check_for_new_messages():
    """Проверка новых сообщений для отправки на панель"""
    global last_content_hash, last_file_stats
    
    try:
        if not os.path.exists(LAST_MESSAGE_FILE):
            return
        
        current_stats = os.stat(LAST_MESSAGE_FILE)
        current_size = current_stats.st_size
        current_mtime = current_stats.st_mtime
        
        stats_changed = False
        if last_file_stats is None:
            stats_changed = True
        else:
            last_size, last_mtime = last_file_stats
            if current_size != last_size or current_mtime != last_mtime:
                stats_changed = True
        
        if not stats_changed:
            return
        
        # Ждем, чтобы убедиться, что файл полностью записан
        time.sleep(0.5)
        
        with file_lock:
            with open(LAST_MESSAGE_FILE, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read().strip()
        
        if not text:
            last_file_stats = (current_size, current_mtime)
            last_content_hash = None
            return
        
        current_content_hash = hashlib.md5(text.encode()).hexdigest()
        
        if current_content_hash == last_content_hash:
            last_file_stats = (current_size, current_mtime)
            return
        
        print(f"\n📝 Обнаружено новое сообщение для панели: '{text[:30]}...' ({len(text)} симв.)")
        
        # Проверяем доступность панели перед отправкой
        if not check_panel_online():
            print("⚠️ Панель недоступна по сети, пропускаю отправку")
            return
        
        # Отправляем текст с повторными попытками
        max_send_attempts = 2
        for attempt in range(max_send_attempts):
            print(f"🔄 Попытка отправки {attempt + 1}/{max_send_attempts}")
            
            if send_text_to_panel(text):
                last_content_hash = current_content_hash
                last_file_stats = (current_size, current_mtime)
                print("✅ Сообщение успешно отправлено на панель")
                break
            else:
                if attempt < max_send_attempts - 1:
                    print(f"⚠️ Не удалось отправить, следующая попытка через {RETRY_DELAY} секунд...")
                    time.sleep(RETRY_DELAY)
                else:
                    print("❌ Не удалось отправить сообщение после всех попыток")
        
    except Exception as e:
        print(f"❌ Ошибка при проверке файла: {e}")

def web_panel_loop():
    """Основной цикл работы с веб-панелью"""
    print("=" * 60)
    print("🤖 ЗАПУСКАЮ МОДУЛЬ ВЕБ-ПАНЕЛИ")
    print("=" * 60)
    print(f"📁 Отслеживаю файл: {os.path.abspath(LAST_MESSAGE_FILE)}")
    print(f"🌐 Панель: {PANEL_URL}")
    print(f"⏱️ Таймаут подключения: {CONNECTION_TIMEOUT} сек")
    print(f"🔄 Максимум попыток настройки: {MAX_SETUP_ATTEMPTS}")
    print(f"🔄 Попытки загрузки вкладки: {MAX_TAB_LOAD_ATTEMPTS}")
    print(f"🔄 Ожидание от {INITIAL_WAIT_TIME} до {MAX_WAIT_TIME} секунд")
    print("⏳ Ожидаю сообщения...")
    print("=" * 60)
    
    if not os.path.exists(LAST_MESSAGE_FILE):
        with open(LAST_MESSAGE_FILE, 'w', encoding='utf-8') as f:
            f.write("Тестовое сообщение")
        print(f"✅ Создан файл: {LAST_MESSAGE_FILE}")
    
    if not init_web_panel():
        print("❌ Не удалось инициализировать веб-панель")
        return
    
    # Пробуем настроить страницу
    print("\n🔄 Настраиваю соединение с панелью...")
    if setup_page():
        print("✅ Соединение с панелью установлено")
    else:
        print("⚠️ Не удалось настроить соединение, но продолжаю работу...")
    
    last_check_time = time.time()
    last_reset_check = time.time()
    
    try:
        while True:
            current_time = time.time()
            
            # Проверяем новые сообщения каждые 2 секунды
            if current_time - last_check_time >= 2:
                check_for_new_messages()
                last_check_time = current_time
            
            # Проверяем, не нужен ли периодический сброс
            if current_time - last_reset_check >= 60:
                if not check_panel_online():
                    print("⚠️ Панель недоступна, выполняю проверку соединения...")
                last_reset_check = current_time
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\n🛑 Остановка модуля веб-панели...")
    except Exception as e:
        print(f"\n❌ Неожиданная ошибка в модуле веб-панели: {e}")

def cleanup():
    """Очистка ресурсов при завершении"""
    global driver
    if driver:
        try:
            driver.quit()
            print("✅ Браузер закрыт")
        except:
            pass
        driver = None

# ========== TELEGRAM БОТ ==========

@bot.message_handler(content_types=['text'])
def handle_text_messages(message):
    """Обработка текстовых сообщений от пользователей"""
    try:
        msg_info = {
            "message_id": message.id,
            "from_user": {
                "id": message.from_user.id,
                "first_name": message.from_user.first_name,
                "username": message.from_user.username or "без username"
            },
            "chat_id": message.chat.id,
            "date": str(message.date),
            "text": message.text
        }
        
        callback_id = str(uuid.uuid4())[:8]
        
        markup = telebot.types.InlineKeyboardMarkup()
        btn_approve = telebot.types.InlineKeyboardButton(
            text="✅ Подтвердить", 
            callback_data=f"approve_{callback_id}"
        )
        btn_reject = telebot.types.InlineKeyboardButton(
            text="❌ Отклонить", 
            callback_data=f"reject_{callback_id}"
        )
        markup.row(btn_approve, btn_reject)
        
        forwarded_msg = bot.forward_message(YOUR_CHAT_ID, message.chat.id, message.message_id)
        
        mod_msg = bot.send_message(
            YOUR_CHAT_ID,
            f"📩 Новое сообщение для модерации:\n"
            f"От: {message.from_user.first_name} (@{message.from_user.username or 'N/A'})\n"
            f"ID: {callback_id}\n\n"
            f"Текст: {message.text[:100]}{'...' if len(message.text) > 100 else ''}",
            reply_markup=markup,
            parse_mode='HTML'
        )
        
        pending_messages[callback_id] = {
            'user_msg_info': msg_info,
            'user_chat_id': message.chat.id,
            'original_text': message.text,
            'moderator_msg_id': mod_msg.message_id,
            'original_message_id': message.id,
            'is_media': False
        }
        
        save_message_to_file(msg_info, status="pending")
        
        bot.send_message(
            message.chat.id,
            "✅ Ваше сообщение получено и отправлено на модерацию. "
            "Вы получите уведомление о результате."
        )
        
        print(f"✓ Текстовое сообщение {callback_id} ожидает модерации")
        print(f"   Текст: '{message.text[:50]}...'" if len(message.text) > 50 else f"   Текст: '{message.text}'")
        
    except Exception as e:
        print(f"✗ Ошибка в handle_text_messages: {e}")
        try:
            bot.send_message(message.chat.id, "⚠️ Произошла ошибка при обработке сообщения.")
        except:
            pass

@bot.message_handler(content_types=['photo', 'document', 'audio', 'video', 'voice'])
def handle_media_messages(message):
    """Обработка медиафайлов от пользователей"""
    try:
        if message.caption:
            text_to_save = message.caption
            display_text = f"📎 Файл с подписью: {message.caption}"
        else:
            text_to_save = f"[{message.content_type.upper()}] Без подписи"
            display_text = f"📎 Файл типа: {message.content_type}"
        
        msg_info = {
            "message_id": message.id,
            "from_user": {
                "id": message.from_user.id,
                "first_name": message.from_user.first_name,
                "username": message.from_user.username or "без username"
            },
            "chat_id": message.chat.id,
            "date": str(message.date),
            "content_type": message.content_type,
            "caption": message.caption,
            "text": text_to_save
        }
        
        callback_id = str(uuid.uuid4())[:8]
        
        markup = telebot.types.InlineKeyboardMarkup()
        btn_approve = telebot.types.InlineKeyboardButton(
            text="✅ Подтвердить", 
            callback_data=f"approve_{callback_id}"
        )
        btn_reject = telebot.types.InlineKeyboardButton(
            text="❌ Отклонить", 
            callback_data=f"reject_{callback_id}"
        )
        markup.row(btn_approve, btn_reject)
        
        forwarded_msg = bot.forward_message(YOUR_CHAT_ID, message.chat.id, message.message_id)
        
        mod_msg = bot.send_message(
            YOUR_CHAT_ID,
            f"📎 Медиафайл для модерации:\n"
            f"От: {message.from_user.first_name} (@{message.from_user.username or 'N/A'})\n"
            f"Тип: {message.content_type}\n"
            f"ID: {callback_id}\n"
            f"Подпись: {message.caption or 'Нет подписи'}",
            reply_markup=markup,
            parse_mode='HTML'
        )
        
        pending_messages[callback_id] = {
            'user_msg_info': msg_info,
            'user_chat_id': message.chat.id,
            'original_text': text_to_save,
            'moderator_msg_id': mod_msg.message_id,
            'original_message_id': message.id,
            'is_media': True,
            'content_type': message.content_type
        }
        
        save_message_to_file(msg_info, status="pending")
        
        bot.send_message(
            message.chat.id,
            f"✅ Ваш файл ({message.content_type}) получен и отправлен на модерацию."
        )
        
        print(f"✓ Медиафайл {callback_id} ожидает модерации")
        
    except Exception as e:
        print(f"✗ Ошибка в handle_media_messages: {e}")

@bot.callback_query_handler(func=lambda call: True)
def handle_moderation_buttons(call):
    """Обработка нажатий на кнопки Подтвердить/Отклонить с подробным логированием"""
    try:
        print(f"\n🔘 Получен callback запрос: {call.data}")
        print(f"   ID сообщения: {call.message.message_id}")
        print(f"   ID чата: {call.message.chat.id}")
        
        # Разбираем callback_data
        parts = call.data.split('_')
        if len(parts) != 2:
            print(f"❌ Неверный формат callback_data: {call.data}")
            bot.answer_callback_query(call.id, text="Ошибка в данных кнопки")
            return
            
        action, callback_id = parts
        
        print(f"   Действие: {action}")
        print(f"   Callback ID: {callback_id}")
        
        # Проверяем, существует ли такое сообщение
        if callback_id not in pending_messages:
            print(f"❌ Callback ID {callback_id} не найден в pending_messages")
            print(f"   Доступные ID: {list(pending_messages.keys())}")
            bot.answer_callback_query(call.id, text="Сообщение уже обработано или устарело")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"⚠️ Это сообщение уже было обработано."
            )
            return
        
        data = pending_messages[callback_id]
        user_msg_info = data['user_msg_info']
        user_chat_id = data['user_chat_id']
        original_text = data['original_text']
        original_message_id = data['original_message_id']
        
        print(f"✅ Найдено сообщение в очереди:")
        print(f"   От пользователя: {user_msg_info['from_user']['username']}")
        print(f"   Текст: '{original_text[:50]}...'")
        
        # Убираем кнопки (редактируем сообщение)
        try:
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None
            )
            print("✅ Кнопки удалены")
        except Exception as e:
            print(f"⚠️ Не удалось удалить кнопки: {e}")
        
        # Обрабатываем действие
        if action == "approve":
            print("🔄 Обрабатываю подтверждение...")
            
            # Даем обратную связь пользователю
            bot.answer_callback_query(call.id, text="Сообщение подтверждено!")
            
            # Обновляем статус в логе
            update_message_status_in_log(original_message_id, "approved")
            
            # ВАЖНО: Сохраняем подтвержденное сообщение в last_message.txt
            print(f"💾 Сохраняю текст в файл {LAST_MESSAGE_FILE}:")
            print(f"   Текст для сохранения: '{original_text[:50]}...'")
            
            save_approved_message(original_text)
            
            # Записываем в файл подтвержденных сообщений (дописываем)
            try:
                with open(APPROVED_FILE, 'a', encoding='utf-8') as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    username = user_msg_info['from_user']['username'] or "без_username"
                    f.write(f"[{timestamp}] @{username}: {original_text}\n")
                print(f"✅ Сообщение добавлено в {APPROVED_FILE}")
            except Exception as e:
                print(f"✗ Ошибка записи в {APPROVED_FILE}: {e}")
            
            # Уведомляем пользователя
            try:
                bot.send_message(
                    user_chat_id,
                    "✅ Ваше сообщение было одобрено модератором и опубликовано."
                )
                print("✅ Пользователь уведомлен")
            except Exception as e:
                print(f"⚠️ Не удалось уведомить пользователя: {e}")
            
            # Обновляем сообщение модератора
            try:
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"✅ <b>ПОДТВЕРЖДЕНО</b>\n\n"
                         f"Сообщение от @{user_msg_info['from_user']['username']} было подтверждено.\n"
                         f"Текст: {original_text[:150]}{'...' if len(original_text) > 150 else ''}",
                    parse_mode='HTML'
                )
                print("✅ Сообщение модератора обновлено")
            except Exception as e:
                print(f"⚠️ Не удалось обновить сообщение модератора: {e}")
            
            print(f"✓ Сообщение {callback_id} успешно подтверждено")
            
        elif action == "reject":
            print("🔄 Обрабатываю отклонение...")
            
            bot.answer_callback_query(call.id, text="Сообщение отклонено")
            
            # Обновляем статус в логе
            update_message_status_in_log(original_message_id, "rejected")
            
            # Записываем в файл отклоненных сообщений
            try:
                with open(REJECTED_FILE, 'a', encoding='utf-8') as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    username = user_msg_info['from_user']['username'] or "без_username"
                    f.write(f"[{timestamp}] @{username}: {original_text}\n")
                print(f"✅ Сообщение добавлено в {REJECTED_FILE}")
            except Exception as e:
                print(f"✗ Ошибка записи в {REJECTED_FILE}: {e}")
            
            # Уведомляем пользователя
            try:
                bot.send_message(
                    user_chat_id,
                    "❌ Ваше сообщение было отклонено модератором."
                )
                print("✅ Пользователь уведомлен")
            except Exception as e:
                print(f"⚠️ Не удалось уведомить пользователя: {e}")
            
            # Обновляем сообщение модератора
            try:
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"❌ <b>ОТКЛОНЕНО</b>\n\n"
                         f"Сообщение от @{user_msg_info['from_user']['username']} было отклонено.\n"
                         f"Текст: {original_text[:150]}{'...' if len(original_text) > 150 else ''}",
                    parse_mode='HTML'
                )
                print("✅ Сообщение модератора обновлено")
            except Exception as e:
                print(f"⚠️ Не удалось обновить сообщение модератора: {e}")
            
            print(f"✓ Сообщение {callback_id} отклонено")
        else:
            print(f"❌ Неизвестное действие: {action}")
            bot.answer_callback_query(call.id, text="Неизвестное действие")
            return
        
        # Удаляем из словаря ожидающих
        del pending_messages[callback_id]
        print(f"✅ Сообщение удалено из очереди. Осталось в очереди: {len(pending_messages)}")
        
    except Exception as e:
        print(f"✗ КРИТИЧЕСКАЯ ОШИБКА в handle_moderation_buttons: {e}")
        import traceback
        traceback.print_exc()
        try:
            bot.answer_callback_query(call.id, text="Произошла ошибка при обработке")
        except:
            pass

@bot.message_handler(commands=['status'])
def handle_status_command(message):
    """Команда для проверки статуса бота"""
    if str(message.chat.id) != YOUR_CHAT_ID:
        bot.send_message(message.chat.id, "⛔ У вас нет прав для этой команды.")
        return
    
    pending_count = len(pending_messages)
    
    files_info = []
    for filename in [LOG_FILE, LAST_MESSAGE_FILE, APPROVED_FILE, REJECTED_FILE]:
        if os.path.exists(filename):
            size = os.path.getsize(filename)
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read(100)
                preview = content[:50] + "..." if len(content) > 50 else content
            
            if filename == LAST_MESSAGE_FILE:
                files_info.append(f"  • {filename}: {size} байт\n    Текст: '{preview}'")
            else:
                files_info.append(f"  • {filename}: {size} байт")
        else:
            files_info.append(f"  • {filename}: не создан")
    
    panel_status = "✅ Онлайн" if check_panel_online() else "❌ Офлайн"
    
    status_text = (
        f"🤖 <b>Статус системы</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"  • Сообщений в очереди: {pending_count}\n"
        f"  • Ваш Chat ID: {YOUR_CHAT_ID}\n"
        f"  • Веб-панель: {panel_status}\n\n"
        f"📁 <b>Файлы:</b>\n" + "\n".join(files_info) + "\n\n"
        f"<i>Система работает в объединенном режиме</i>"
    )
    
    bot.send_message(message.chat.id, status_text, parse_mode='HTML')

@bot.message_handler(commands=['last_approved'])
def handle_last_approved_command(message):
    """Показывает последнее подтвержденное сообщение"""
    if str(message.chat.id) != YOUR_CHAT_ID:
        bot.send_message(message.chat.id, "⛔ У вас нет прав для этой команды.")
        return
    
    if os.path.exists(LAST_MESSAGE_FILE):
        with open(LAST_MESSAGE_FILE, 'r', encoding='utf-8') as f:
            last_message = f.read()
        
        if last_message:
            response = f"📝 <b>Последнее подтвержденное сообщение:</b>\n\n{last_message}"
        else:
            response = "📭 Файл last_message.txt пуст."
    else:
        response = "📭 Файл last_message.txt не существует."
    
    bot.send_message(message.chat.id, response, parse_mode='HTML')

@bot.message_handler(commands=['clear_pending'])
def handle_clear_command(message):
    """Очистка очереди модерации"""
    if str(message.chat.id) != YOUR_CHAT_ID:
        bot.send_message(message.chat.id, "⛔ У вас нет прав для этой команды.")
        return
    
    global pending_messages
    count = len(pending_messages)
    pending_messages = {}
    
    bot.send_message(
        message.chat.id,
        f"✅ Очередь модерации очищена. Удалено {count} сообщений."
    )

@bot.message_handler(commands=['panel_status'])
def handle_panel_status_command(message):
    """Проверка статуса веб-панели"""
    if str(message.chat.id) != YOUR_CHAT_ID:
        bot.send_message(message.chat.id, "⛔ У вас нет прав для этой команды.")
        return
    
    online = check_panel_online()
    status = "✅ Онлайн" if online else "❌ Офлайн"
    
    if driver and page_ready:
        driver_status = "✅ Настроен"
    elif driver:
        driver_status = "⚠️ Драйвер запущен, но страница не настроена"
    else:
        driver_status = "❌ Не запущен"
    
    response = (
        f"🌐 <b>Статус веб-панели</b>\n\n"
        f"• Доступность: {status}\n"
        f"• URL: {PANEL_URL}\n"
        f"• Драйвер: {driver_status}\n"
        f"• Попыток настройки: {setup_attempts}/{MAX_SETUP_ATTEMPTS}"
    )
    
    bot.send_message(message.chat.id, response, parse_mode='HTML')

# ========== ЗАПУСК СИСТЕМЫ ==========

def run_telegram_bot():
    """Запуск Telegram бота в отдельном потоке"""
    print("=" * 50)
    print("🤖 ЗАПУСКАЮ TELEGRAM БОТА")
    print("=" * 50)
    print(f"📝 Логи: {LOG_FILE}")
    print(f"💬 Последнее ПОДТВЕРЖДЕННОЕ сообщение: {LAST_MESSAGE_FILE}")
    print(f"✅ Все подтвержденные: {APPROVED_FILE}")
    print(f"❌ Все отклоненные: {REJECTED_FILE}")
    print("⏳ Бот запущен и ожидает сообщений...")
    print("Команды для модератора:")
    print("  /status - показать статус системы")
    print("  /last_approved - показать последнее подтвержденное сообщение")
    print("  /panel_status - статус веб-панели")
    print("  /clear_pending - очистить очередь модерации")
    print("=" * 50)
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
    except Exception as e:
        print(f"✗ Критическая ошибка бота: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Основная функция запуска системы"""
    print("=" * 60)
    print("🚀 ЗАПУСК ОБЪЕДИНЕННОЙ СИСТЕМЫ")
    print("=" * 60)
    print("Система объединяет:")
    print("  1. Telegram бота для модерации сообщений")
    print("  2. Модуль отправки на веб-панель")
    print("=" * 60)
    
    # Создаем необходимые файлы если их нет
    for filename in [LOG_FILE, APPROVED_FILE, REJECTED_FILE]:
        if not os.path.exists(filename):
            if filename == LOG_FILE:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump([], f)
            else:
                with open(filename, 'w', encoding='utf-8') as f:
                    pass
            print(f"✅ Создан файл: {filename}")
    
    # Запускаем Telegram бота в отдельном потоке
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()
    
    # В основном потоке запускаем модуль веб-панели
    try:
        web_panel_loop()
    except KeyboardInterrupt:
        print("\n🛑 Остановка системы...")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()
        print("\n👋 Система завершена")

if __name__ == '__main__':
    main()