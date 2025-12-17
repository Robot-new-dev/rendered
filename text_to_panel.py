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
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import threading

# ========== ОБЩАЯ КОНФИГУРАЦИЯ ==========
# Telegram Bot
BOT_TOKEN = '7997058547:AAF1R2Vf3Ge2SjJxtLeRfw5MIyU32hdNYu8'
YOUR_CHAT_ID = '191786185'

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

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
bot = telebot.TeleBot(BOT_TOKEN)
pending_messages = {}

# Переменные для веб-панели
driver = None
last_content_hash = None
last_file_stats = None
page_ready = False
setup_attempts = 0
MAX_SETUP_ATTEMPTS = 3

# Блокировка для синхронизации доступа к файлам
file_lock = threading.Lock()

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛАМИ ==========

def save_approved_message(text):
    """Сохраняет ТОЛЬКО подтвержденное сообщение в отдельный файл (перезаписывает)"""
    with file_lock:
        try:
            with open(LAST_MESSAGE_FILE, 'w', encoding='utf-8') as f:
                f.write(text)
            print(f"✓ Подтвержденное сообщение сохранено в {LAST_MESSAGE_FILE}")
        except Exception as e:
            print(f"✗ Ошибка при записи в {LAST_MESSAGE_FILE}: {e}")

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
        driver.set_page_load_timeout(15)
        driver.implicitly_wait(3)
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
        sock.settimeout(2)
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

def setup_page():
    """Настройка страницы веб-панели"""
    global page_ready, setup_attempts
    
    if page_ready:
        return True
    
    setup_attempts += 1
    if setup_attempts > MAX_SETUP_ATTEMPTS:
        print("❌ Превышено количество попыток настройки")
        return False
    
    try:
        if not check_panel_online():
            print("⚠️ Панель недоступна по сети")
            return False
        
        print(f"📄 Загружаю страницу панели (попытка {setup_attempts})...")
        driver.get(PANEL_URL)
        time.sleep(5)
        
        if "app-root" not in driver.page_source:
            print("❌ Страница не загрузилась")
            page_ready = False
            return False
        
        print("🔍 Ищу вкладку 'Тексты'...")
        try:
            tab = driver.find_element(By.XPATH, TAB_TEXT_XPATH)
            print("✅ Вкладка найдена")
        except:
            print("❌ Вкладка 'Тексты' не найдена")
            page_ready = False
            return False
        
        print("🖱️ Кликаю на вкладку 'Тексты'...")
        tab.click()
        time.sleep(3)
        
        try:
            textarea = driver.find_element(By.XPATH, TEXTAREA_XPATH)
            if textarea.is_displayed():
                print("✅ Поле ввода доступно")
            else:
                print("⚠️ Поле ввода найдено, но не отображается")
        except:
            print("❌ Поле ввода не найдено после клика на вкладку")
            page_ready = False
            return False
        
        page_ready = True
        setup_attempts = 0
        print("✅ Страница настроена")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка настройки: {e}")
        page_ready = False
        return False

def send_text_to_panel(text):
    """Отправка текста на веб-панель"""
    global page_ready
    
    if not page_ready:
        print("🔄 Страница не настроена, пытаюсь настроить...")
        if not setup_page():
            print("❌ Не удалось настроить страницу")
            return False
    
    try:
        formatted_text = format_text(text)
        print(f"📝 Форматированный текст: '{formatted_text[:50]}...'")
        
        textarea = driver.find_element(By.XPATH, TEXTAREA_XPATH)
        button = driver.find_element(By.XPATH, SUBMIT_BUTTON_XPATH)
        
        textarea.clear()
        textarea.send_keys(formatted_text)
        button.click()
        
        print(f"✅ Текст отправлен на панель")
        time.sleep(1)
        return True
        
    except Exception as e:
        print(f"❌ Ошибка отправки на панель: {e}")
        page_ready = False
        
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
        
        print(f"\n📝 Новое сообщение для панели: '{text[:30]}...' ({len(text)} симв.)")
        
        if not check_panel_online():
            print("⚠️ Панель недоступна, пропускаю отправку")
            return
        
        if send_text_to_panel(text):
            last_content_hash = current_content_hash
            last_file_stats = (current_size, current_mtime)
        else:
            print("❌ Не удалось отправить сообщение на панель")
        
    except Exception as e:
        print(f"❌ Ошибка проверки файла: {e}")

def web_panel_loop():
    """Основной цикл работы с веб-панелью"""
    print("=" * 60)
    print("🤖 ЗАПУСКАЮ МОДУЛЬ ВЕБ-ПАНЕЛИ")
    print("=" * 60)
    print(f"📁 Отслеживаю файл: {os.path.abspath(LAST_MESSAGE_FILE)}")
    print(f"🌐 Панель: {PANEL_URL}")
    print("⏳ Ожидаю сообщения...")
    print("=" * 60)
    
    if not os.path.exists(LAST_MESSAGE_FILE):
        with open(LAST_MESSAGE_FILE, 'w', encoding='utf-8') as f:
            f.write("Тестовое сообщение")
        print(f"✅ Создан файл: {LAST_MESSAGE_FILE}")
    
    if not init_web_panel():
        print("❌ Не удалось инициализировать веб-панель")
        return
    
    if not setup_page():
        print("⚠️ Проблема с настройкой, но продолжаю...")
    
    last_check_time = time.time()
    
    try:
        while True:
            current_time = time.time()
            if current_time - last_check_time >= 2:
                check_for_new_messages()
                last_check_time = current_time
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка модуля веб-панели...")
    except Exception as e:
        print(f"\n❌ Неожиданная ошибка в модуле веб-панели: {e}")

# ========== ОБРАБОТЧИКИ TELEGRAM ==========

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
    """Обработка нажатий на кнопки Подтвердить/Отклонить"""
    try:
        parts = call.data.split('_')
        if len(parts) != 2:
            bot.answer_callback_query(call.id, text="Ошибка в данных кнопки")
            return
            
        action, callback_id = parts
        
        if callback_id not in pending_messages:
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
        
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )
        
        if action == "approve":
            bot.answer_callback_query(call.id, text="Сообщение подтверждено!")
            
            update_message_status_in_log(original_message_id, "approved")
            
            save_approved_message(original_text)
            
            with open(APPROVED_FILE, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                username = user_msg_info['from_user']['username'] or "без_username"
                f.write(f"[{timestamp}] @{username}: {original_text}\n")
            
            try:
                bot.send_message(
                    user_chat_id,
                    "✅ Ваше сообщение было одобрено модератором и опубликовано."
                )
            except:
                pass
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"✅ <b>ПОДТВЕРЖДЕНО</b>\n\n"
                     f"Сообщение от @{user_msg_info['from_user']['username']} было подтверждено.\n"
                     f"Текст: {original_text[:150]}{'...' if len(original_text) > 150 else ''}",
                parse_mode='HTML'
            )
            
            print(f"✓ Сообщение {callback_id} подтверждено и сохранено в last_message.txt")
            
        elif action == "reject":
            bot.answer_callback_query(call.id, text="Сообщение отклонено")
            
            update_message_status_in_log(original_message_id, "rejected")
            
            with open(REJECTED_FILE, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                username = user_msg_info['from_user']['username'] or "без_username"
                f.write(f"[{timestamp}] @{username}: {original_text}\n")
            
            try:
                bot.send_message(
                    user_chat_id,
                    "❌ Ваше сообщение было отклонено модератором."
                )
            except:
                pass
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ <b>ОТКЛОНЕНО</b>\n\n"
                     f"Сообщение от @{user_msg_info['from_user']['username']} было отклонено.\n"
                     f"Текст: {original_text[:150]}{'...' if len(original_text) > 150 else ''}",
                parse_mode='HTML'
            )
            
            print(f"✓ Сообщение {callback_id} отклонено")
        
        del pending_messages[callback_id]
        
    except Exception as e:
        print(f"✗ Ошибка в handle_moderation_buttons: {e}")
        try:
            bot.answer_callback_query(call.id, text="Произошла ошибка")
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
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"✗ Критическая ошибка бота: {e}")

def cleanup():
    """Очистка ресурсов при завершении"""
    if driver:
        try:
            driver.quit()
            print("✅ Браузер закрыт")
        except:
            pass

def main():
    """Основная функция запуска системы"""
    print("=" * 60)
    print("🚀 ЗАПУСК ОБЪЕДИНЕННОЙ СИСТЕМЫ")
    print("=" * 60)
    print("Система объединяет:")
    print("  1. Telegram бота для модерации сообщений")
    print("  2. Модуль отправки на веб-панель")
    print("=" * 60)
    
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
    finally:
        cleanup()
        print("\n👋 Система завершена")

if __name__ == '__main__':
    main()