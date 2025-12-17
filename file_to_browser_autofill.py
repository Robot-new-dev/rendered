import time
import os
import hashlib
import socket
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# ========== КОНФИГУРАЦИЯ ==========
DRIVER_PATH = 'C:\\Arduino\\chromedriver-win64\\chromedriver.exe'
PANEL_URL = 'http://192.168.0.102'
TEXT_FILE = 'last_message.txt'

# Точные XPath
TAB_TEXT_XPATH = '/html/body/app-root/div/div[1]/mat-tab-group/mat-tab-header/div[2]/div/div/div[2]'
TEXTAREA_XPATH = '/html/body/app-root/div/div[1]/mat-tab-group/div/mat-tab-body[2]/div/app-tab-texts-panel/div/mat-tab-group/div/mat-tab-body[1]/div/app-tab-texts/div/div/fieldset[2]/div[2]/mat-form-field/div[1]/div[2]/div/textarea'
SUBMIT_BUTTON_XPATH = '/html/body/app-root/div/div[1]/mat-tab-group/div/mat-tab-body[2]/div/app-tab-texts-panel/div/mat-tab-group/div/mat-tab-body[1]/div/app-tab-texts/div/div/fieldset[2]/div[3]/div/div/button'

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
driver = None
last_content_hash = None
last_file_stats = None
page_ready = False
setup_attempts = 0
MAX_SETUP_ATTEMPTS = 3

# ========== ИНИЦИАЛИЗАЦИЯ ==========
def init_system():
    global driver
    print("🔄 Запуск системы...")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Headless режим
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=800,600")
    
    # Минимальные настройки
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
        print("✅ Система готова")
        return True
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        return False

# ========== ПИНГ-ПРОВЕРКА ПАНЕЛИ ==========
def check_panel_online():
    """Проверяет доступность панели по сети"""
    try:
        # Извлекаем хост из URL
        host = PANEL_URL.replace("http://", "").replace("https://", "").split("/")[0]
        
        # Пробуем подключиться к порту 80 (HTTP)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)  # Таймаут 2 секунды
        result = sock.connect_ex((host, 80))
        sock.close()
        
        return result == 0
    except:
        return False

# ========== ФОРМАТИРОВАНИЕ ТЕКСТА ==========
def format_text(text):
    """Добавляет символы цвета к тексту (белый текст на черном фоне)"""
    if not text:
        return text
    
    # Добавляем теги цвета: {C#FFFFFF} - белый цвет текста, {B#000000} - черный фон
    formatted = f"{{C#FFFFFF}}{{B#000000}}{text}"
    
    return formatted

# ========== НАСТРОЙКА СТРАНИЦЫ ==========
def setup_page():
    global driver, page_ready, setup_attempts
    
    if page_ready:
        return True
    
    setup_attempts += 1
    if setup_attempts > MAX_SETUP_ATTEMPTS:
        print("❌ Превышено количество попыток настройки")
        return False
    
    try:
        # Сначала проверяем доступность панели
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

# ========== БЫСТРАЯ ОТПРАВКА ТЕКСТА ==========
def send_text_fast(text):
    global driver, page_ready
    
    if not page_ready:
        print("🔄 Страница не настроена, пытаюсь настроить...")
        if not setup_page():
            print("❌ Не удалось настроить страницу")
            return False
    
    try:
        # Форматируем текст (добавляем символы цвета)
        formatted_text = format_text(text)
        print(f"📝 Форматированный текст: '{formatted_text[:50]}...'")
        
        # Находим элементы
        textarea = driver.find_element(By.XPATH, TEXTAREA_XPATH)
        button = driver.find_element(By.XPATH, SUBMIT_BUTTON_XPATH)
        
        # Очищаем поле и вводим текст
        textarea.clear()
        textarea.send_keys(formatted_text)
        
        # Нажимаем кнопку
        button.click()
        
        print(f"✅ Текст отправлен")
        
        time.sleep(1)
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        page_ready = False
        
        try:
            driver.refresh()
            time.sleep(3)
        except:
            pass
            
        return False

# ========== ПРОВЕРКА ФАЙЛА ==========
def check_for_new_messages():
    global last_content_hash, last_file_stats
    
    try:
        if not os.path.exists(TEXT_FILE):
            return
        
        # Получаем текущую статистику файла
        current_stats = os.stat(TEXT_FILE)
        current_size = current_stats.st_size
        current_mtime = current_stats.st_mtime
        
        # Проверяем, изменилась ли статистика файла
        stats_changed = False
        if last_file_stats is None:
            stats_changed = True
        else:
            last_size, last_mtime = last_file_stats
            if current_size != last_size or current_mtime != last_mtime:
                stats_changed = True
        
        if not stats_changed:
            return
        
        # Ждем немного, чтобы убедиться, что файл полностью записан
        time.sleep(0.5)
        
        # Читаем содержимое файла
        with open(TEXT_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read().strip()
        
        if not text:
            last_file_stats = (current_size, current_mtime)
            last_content_hash = None
            return
        
        # Вычисляем хеш содержимого
        current_content_hash = hashlib.md5(text.encode()).hexdigest()
        
        # Проверяем, изменилось ли содержимое
        if current_content_hash == last_content_hash:
            last_file_stats = (current_size, current_mtime)
            return
        
        print(f"\n📝 Новое сообщение: '{text[:30]}...' ({len(text)} симв.)")
        
        # Проверяем доступность панели перед отправкой
        if not check_panel_online():
            print("⚠️ Панель недоступна, пропускаю отправку")
            return
        
        # Отправляем текст
        if send_text_fast(text):
            last_content_hash = current_content_hash
            last_file_stats = (current_size, current_mtime)
        else:
            print("❌ Не удалось отправить сообщение")
        
    except Exception as e:
        print(f"❌ Ошибка проверки файла: {e}")

# ========== ОСНОВНОЙ ЦИКЛ ==========
def main():
    global driver
    
    print("=" * 60)
    print("🤖 ESP8266 ПАНЕЛЬ - С ЦВЕТНЫМ ФОРМАТИРОВАНИЕМ")
    print("=" * 60)
    print(f"📁 Отслеживаю файл: {os.path.abspath(TEXT_FILE)}")
    print(f"🌐 Панель: {PANEL_URL}")
    print(f"🎨 К тексту добавляется: {{C#FFFFFF}}{{B#000000}}")
    print("\n⏳ Ожидаю сообщения... (Ctrl+C для выхода)")
    print("=" * 60)
    
    # Создаем файл если нет
    if not os.path.exists(TEXT_FILE):
        with open(TEXT_FILE, 'w', encoding='utf-8') as f:
            f.write("Тестовое сообщение")
        print(f"✅ Создан файл: {TEXT_FILE}")
    
    # Запускаем систему
    if not init_system():
        print("❌ Не удалось инициализировать систему")
        return
    
    # Настраиваем страницу
    print("\n🔄 Настраиваю соединение с панелью...")
    if not setup_page():
        print("⚠️ Проблема с настройкой, но продолжаю...")
    
    last_check_time = time.time()
    
    try:
        while True:
            current_time = time.time()
            
            # Проверяем каждые 2 секунды
            if current_time - last_check_time >= 2:
                check_for_new_messages()
                last_check_time = current_time
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\n🛑 Остановка по запросу пользователя...")
    except Exception as e:
        print(f"\n❌ Неожиданная ошибка: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                print("✅ Браузер закрыт")
            except:
                pass
    
    print("\n👋 Программа завершена")

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    main()