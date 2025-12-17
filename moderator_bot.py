import telebot
import json
from datetime import datetime
import os
import uuid

# ========== КОНФИГУРАЦИЯ ==========
# ЗАМЕНИТЕ ЭТИ ЗНАЧЕНИЯ НА СВОИ!
BOT_TOKEN = 'Ваш токен бота'
YOUR_CHAT_ID = 'Ваш ID в телеграмме'

# Имена файлов
LAST_MESSAGE_FILE = 'last_message.txt'      # ТОЛЬКО подтвержденные сообщения
APPROVED_FILE = 'approved_messages.txt'     # Все подтвержденные (дописываются)
REJECTED_FILE = 'rejected_messages.txt'     # Все отклоненные
LOG_FILE = 'messages_log.json'              # Полная история

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = telebot.TeleBot(BOT_TOKEN)

# Словарь для хранения сообщений, ожидающих модерации
pending_messages = {}

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛАМИ ==========

def save_approved_message(text):
    """Сохраняет ТОЛЬКО подтвержденное сообщение в отдельный файл (перезаписывает)"""
    try:
        with open(LAST_MESSAGE_FILE, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"✓ Подтвержденное сообщение сохранено в {LAST_MESSAGE_FILE}")
    except Exception as e:
        print(f"✗ Ошибка при записи в {LAST_MESSAGE_FILE}: {e}")

def save_message_to_file(message_data, status="pending"):
    """Сохраняет полную информацию о сообщении в JSON-файл"""
    try:
        # Пытаемся прочитать существующие данные
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    
    # Добавляем новое сообщение
    data.append({
        "timestamp": datetime.now().isoformat(),
        "message_data": message_data,
        "status": status  # Статус: pending, approved, rejected
    })
    
    # Записываем обратно
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"✓ Сообщение добавлено в {LOG_FILE} (статус: {status})")

def update_message_status_in_log(message_id, new_status):
    """Обновляет статус сообщения в JSON-логе"""
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Ищем сообщение по ID и обновляем статус
        for item in reversed(data):  # Ищем с конца (последние сообщения)
            if item['message_data'].get('message_id') == message_id:
                item['status'] = new_status
                item['moderated_at'] = datetime.now().isoformat()
                break
        
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"✓ Статус сообщения {message_id} обновлен на '{new_status}'")
    except Exception as e:
        print(f"✗ Ошибка при обновлении статуса: {e}")

# ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========

@bot.message_handler(content_types=['text'])
def handle_text_messages(message):
    """Обработка текстовых сообщений от пользователей"""
    try:
        # Подготавливаем информацию о сообщении
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
        
        # Генерируем уникальный ID для этого сообщения модерации
        callback_id = str(uuid.uuid4())[:8]
        
        # Создаем клавиатуру с кнопками модерации
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
        
        # Пересылаем сообщение модератору (вам)
        forwarded_msg = bot.forward_message(YOUR_CHAT_ID, message.chat.id, message.message_id)
        
        # Отправляем кнопки модерации отдельным сообщением
        mod_msg = bot.send_message(
            YOUR_CHAT_ID,
            f"📩 Новое сообщение для модерации:\n"
            f"От: {message.from_user.first_name} (@{message.from_user.username or 'N/A'})\n"
            f"ID: {callback_id}\n\n"
            f"Текст: {message.text[:100]}{'...' if len(message.text) > 100 else ''}",
            reply_markup=markup,
            parse_mode='HTML'
        )
        
        # Сохраняем в словарь ожидающих модерации
        pending_messages[callback_id] = {
            'user_msg_info': msg_info,
            'user_chat_id': message.chat.id,
            'original_text': message.text,
            'moderator_msg_id': mod_msg.message_id,
            'original_message_id': message.id,
            'is_media': False
        }
        
        # Сохраняем в лог (НО НЕ В last_message.txt!)
        save_message_to_file(msg_info, status="pending")
        
        # Отправляем подтверждение пользователю
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
        # Определяем текст для сохранения (подпись или информация о типе файла)
        if message.caption:
            text_to_save = message.caption
            display_text = f"📎 Файл с подписью: {message.caption}"
        else:
            text_to_save = f"[{message.content_type.upper()}] Без подписи"
            display_text = f"📎 Файл типа: {message.content_type}"
        
        # Информация о сообщении
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
        
        # Генерируем уникальный ID
        callback_id = str(uuid.uuid4())[:8]
        
        # Создаем клавиатуру
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
        
        # Пересылаем файл модератору
        forwarded_msg = bot.forward_message(YOUR_CHAT_ID, message.chat.id, message.message_id)
        
        # Отправляем кнопки модерации
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
        
        # Сохраняем в словарь
        pending_messages[callback_id] = {
            'user_msg_info': msg_info,
            'user_chat_id': message.chat.id,
            'original_text': text_to_save,
            'moderator_msg_id': mod_msg.message_id,
            'original_message_id': message.id,
            'is_media': True,
            'content_type': message.content_type
        }
        
        # Сохраняем в лог (НО НЕ В last_message.txt!)
        save_message_to_file(msg_info, status="pending")
        
        # Подтверждение пользователю
        bot.send_message(
            message.chat.id,
            f"✅ Ваш файл ({message.content_type}) получен и отправлен на модерацию."
        )
        
        print(f"✓ Медиафайл {callback_id} ожидает модерации")
        
    except Exception as e:
        print(f"✗ Ошибка в handle_media_messages: {e}")

# ========== ОБРАБОТЧИК КНОПОК МОДЕРАЦИИ ==========

@bot.callback_query_handler(func=lambda call: True)
def handle_moderation_buttons(call):
    """Обработка нажатий на кнопки Подтвердить/Отклонить"""
    try:
        # Разбираем callback_data
        parts = call.data.split('_')
        if len(parts) != 2:
            bot.answer_callback_query(call.id, text="Ошибка в данных кнопки")
            return
            
        action, callback_id = parts
        
        # Проверяем, существует ли такое сообщение
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
        
        # Убираем кнопки (редактируем сообщение)
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )
        
        # Обрабатываем действие
        if action == "approve":
            # Действия при подтверждении
            bot.answer_callback_query(call.id, text="Сообщение подтверждено!")
            
            # Обновляем статус в логе
            update_message_status_in_log(original_message_id, "approved")
            
            # ЗАПИСЫВАЕМ ПОДТВЕРЖДЕННОЕ СООБЩЕНИЕ В last_message.txt
            save_approved_message(original_text)
            
            # Записываем в файл подтвержденных сообщений (дописываем)
            with open(APPROVED_FILE, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                username = user_msg_info['from_user']['username'] or "без_username"
                f.write(f"[{timestamp}] @{username}: {original_text}\n")
            
            # Уведомляем пользователя
            try:
                bot.send_message(
                    user_chat_id,
                    "✅ Ваше сообщение было одобрено модератором и опубликовано."
                )
            except:
                pass
            
            # Обновляем сообщение модератора
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
            # Действия при отклонении
            bot.answer_callback_query(call.id, text="Сообщение отклонено")
            
            # Обновляем статус в логе
            update_message_status_in_log(original_message_id, "rejected")
            
            # Записываем в файл отклоненных сообщений
            with open(REJECTED_FILE, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                username = user_msg_info['from_user']['username'] or "без_username"
                f.write(f"[{timestamp}] @{username}: {original_text}\n")
            
            # Уведомляем пользователя
            try:
                bot.send_message(
                    user_chat_id,
                    "❌ Ваше сообщение было отклонено модератором."
                )
            except:
                pass
            
            # Обновляем сообщение модератора
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ <b>ОТКЛОНЕНО</b>\n\n"
                     f"Сообщение от @{user_msg_info['from_user']['username']} было отклонено.\n"
                     f"Текст: {original_text[:150]}{'...' if len(original_text) > 150 else ''}",
                parse_mode='HTML'
            )
            
            print(f"✓ Сообщение {callback_id} отклонено")
        
        # Удаляем из словаря ожидающих
        del pending_messages[callback_id]
        
    except Exception as e:
        print(f"✗ Ошибка в handle_moderation_buttons: {e}")
        try:
            bot.answer_callback_query(call.id, text="Произошла ошибка")
        except:
            pass

# ========== КОМАНДА ДЛЯ ПРОВЕРКИ СТАТУСА ==========

@bot.message_handler(commands=['status'])
def handle_status_command(message):
    """Команда для проверки статуса бота (только для модератора)"""
    if str(message.chat.id) != YOUR_CHAT_ID:
        bot.send_message(message.chat.id, "⛔ У вас нет прав для этой команды.")
        return
    
    # Статистика
    pending_count = len(pending_messages)
    
    # Проверяем существование файлов
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
    
    status_text = (
        f"🤖 <b>Статус бота-модератора</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"  • Сообщений в очереди: {pending_count}\n"
        f"  • Ваш Chat ID: {YOUR_CHAT_ID}\n\n"
        f"📁 <b>Файлы:</b>\n" + "\n".join(files_info) + "\n\n"
        f"<i>last_message.txt теперь содержит только последнее подтвержденное сообщение</i>"
    )
    
    bot.send_message(message.chat.id, status_text, parse_mode='HTML')

# ========== КОМАНДА ДЛЯ ПРОСМОТРА ПОСЛЕДНЕГО ПОДТВЕРЖДЕННОГО ==========

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
            response = "📭 Файл last_message.txt пуст. Еще не было подтвержденных сообщений."
    else:
        response = "📭 Файл last_message.txt не существует. Еще не было подтвержденных сообщений."
    
    bot.send_message(message.chat.id, response, parse_mode='HTML')

# ========== КОМАНДА ДЛЯ ОЧИСТКИ ОЧЕРЕДИ ==========

@bot.message_handler(commands=['clear_pending'])
def handle_clear_command(message):
    """Очистка очереди модерации (только для модератора)"""
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

# ========== ЗАПУСК БОТА ==========

if __name__ == '__main__':
    print("=" * 50)
    print("🤖 Бот-модератор запускается...")
    print(f"📁 Файлы будут сохраняться в: {os.getcwd()}")
    print(f"📝 Логи: {LOG_FILE}")
    print(f"💬 Последнее ПОДТВЕРЖДЕННОЕ сообщение: {LAST_MESSAGE_FILE}")
    print(f"✅ Все подтвержденные: {APPROVED_FILE}")
    print(f"❌ Все отклоненные: {REJECTED_FILE}")
    print("=" * 50)
    print("⏳ Бот запущен и ожидает сообщений...")
    print("Команды для модератора:")
    print("  /status - показать статус бота")
    print("  /last_approved - показать последнее подтвержденное сообщение")
    print("  /clear_pending - очистить очередь модерации")
    print("=" * 50)
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"✗ Критическая ошибка: {e}")
        print("Проверьте токен бота и подключение к интернету")