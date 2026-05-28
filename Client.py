import customtkinter as ctk
import socket
import threading
import queue
import json
import base64
import re
import os
import webbrowser
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, filedialog
from PIL import Image
import io
from Google import GoogleAuthManager

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SESSION_FILE = Path.home() / ".messenger_session.json"
SETTINGS_FILE = Path.home() / ".messenger_settings.json"

def get_session_path():
    return Path.home() / ".messenger_session.json"

class MessengerClient:
    def __init__(self, host='127.0.0.1', port=8888):
        self.host = host
        self.port = port
        self.sock = None
        self.running = False
        self.recv_queue = queue.Queue()
        self.username = None
        self.current_chat = None
        self.current_chat_type = None

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)
            self.running = True
            threading.Thread(target=self._receive_thread, daemon=True).start()
            return True
        except Exception as e:
            print(f"Ошибка подключения: {e}")
            return False

    def _receive_thread(self):
        while self.running:
            try:
                data = self.sock.recv(65536).decode('utf-8')
                if not data:
                    break
                for line in data.split('\n'):
                    if line.strip():
                        self.recv_queue.put(line.strip())
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"Ошибка приёма: {e}")
                break
        self.running = False

    def send_command(self, cmd):
        if self.sock and self.running:
            try:
                self.sock.sendall((cmd + '\n').encode('utf-8'))
            except Exception as e:
                print(f"Ошибка отправки: {e}")
                self.running = False

    def close(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.sock = None

# ---------- Виджет пузырька ----------
class MessageWidget(ctk.CTkFrame):
    def __init__(self, parent, app, username, text, is_own, timestamp=None, msg_id=None):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.original_text = text
        self.pack(fill="x", padx=10, pady=(4,4))

        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        if is_own:
            main_frame.pack(anchor="e")
        else:
            main_frame.pack(anchor="w")

        if timestamp:
            time_label = ctk.CTkLabel(main_frame, text=timestamp, font=ctk.CTkFont(size=10), text_color="gray")
            if is_own:
                time_label.pack(anchor="e", padx=5, pady=(0,2))
            else:
                time_label.pack(anchor="w", padx=5, pady=(0,2))

        bubble = ctk.CTkFrame(main_frame, corner_radius=18)
        if is_own:
            bubble.pack(anchor="e", padx=5, pady=2)
            bubble.configure(fg_color="#2b5278")
        else:
            bubble.pack(anchor="w", padx=5, pady=2)
            bubble.configure(fg_color="#3a3a3a")

        if app.current_chat_type == "group" and not is_own:
            name_label = ctk.CTkLabel(bubble, text=username, font=ctk.CTkFont(size=11, weight="bold"), text_color="#4a9eff")
            name_label.pack(anchor="w", padx=12, pady=(8,0))

        # Разбираем текст на части: обычный текст и ссылки
        self.create_clickable_content(bubble, text)

        if msg_id:
            id_label = ctk.CTkLabel(bubble, text=f"id:{msg_id}", font=ctk.CTkFont(size=9), text_color="gray")
            id_label.pack(anchor="e", padx=8, pady=(0,4))

        # Контекстное меню
        self.context_menu = tk.Menu(self.app, tearoff=0)
        self.context_menu.add_command(label="📋 Копировать сообщение", command=self.copy_message)

        bubble.bind("<Button-3>", self.show_context_menu)
        # Привязываем меню также к дочерним виджетам (чтобы работало везде)
        for child in bubble.winfo_children():
            child.bind("<Button-3>", self.show_context_menu)

    def create_clickable_content(self, parent, text):
        """Разбивает текст на фрагменты, делая ссылки кликабельными"""
        import re
        # Регулярное выражение для поиска URL
        url_pattern = r'(https?://[^\s]+|www\.[^\s]+)'
        parts = re.split(url_pattern, text)
        
        for part in parts:
            if not part:
                continue
            # Проверяем, является ли часть ссылкой
            if re.match(url_pattern, part):
                url = part if part.startswith('http') else 'http://' + part
                link = ctk.CTkLabel(parent, text=part, font=ctk.CTkFont(size=13), 
                                    text_color="#4a9eff", cursor="hand2")
                link.pack(anchor="w", padx=12, pady=0)
                link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
            else:
                label = ctk.CTkLabel(parent, text=part, font=ctk.CTkFont(size=13), wraplength=500, justify="left")
                label.pack(anchor="w", padx=12, pady=0)

    def show_context_menu(self, event):
        self.context_menu.post(event.x_root, event.y_root)

    def copy_message(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.original_text)
        self.app.add_system_message_to_chat(f"📋 Сообщение скопировано")

# ---------- Основное окно ----------
class MessengerGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("GoodVibe")
        self.geometry("950x650")
        self.minsize(800, 550)
        self.iconbitmap("C:\\Users\\Денис\\Desktop\\GoodVibe\\Logo.ico")

        self.settings = self.load_settings()
        self.apply_settings()

        self.client = MessengerClient()
        self.connected = False
        self.logged_in = False
        self.contacts = []
        self.groups = []
        self.avatars_cache = {}
        self.group_avatars_cache = {}
        self.receiving_avatar = None
        self.avatar_chunks = {}
        self.expected_avatar_chunks = 0
        self.receiving_group_avatar = None
        self.group_avatar_chunks = {}
        self.expected_group_avatar_chunks = 0
        self.password_visible = False
        self.auto_login_attempted = False
        self._status_update_id = None
        self.saved_device_name = self.settings.get("device_name", "")
        self.system_messages_queue = []
        self.messages_cache = {}
        self.current_chat = None
        self.current_chat_type = None
        self.messages_frame = None
        self.messages_cache_dir = Path.home() / ".messenger_cache"
        self.messages_cache_dir.mkdir(exist_ok=True)
        self.google_bound = self.settings.get("google_bound", False)

        self.create_login_screen()
        self.after(500, self.attempt_auto_login)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ---------- Настройки ----------
    def load_settings(self):
        default = {"theme": "dark", "color_theme": "blue", "font_size": 13, "device_name": ""}
        if not SETTINGS_FILE.exists():
            return default
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                return {**default, **data}
        except:
            return default

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f)
        except Exception as e:
            print(f"Не удалось сохранить настройки: {e}")

    def apply_settings(self):
        ctk.set_appearance_mode(self.settings["theme"])
        ctk.set_default_color_theme(self.settings["color_theme"])
        self.font_size = self.settings["font_size"]

    def rebuild_ui(self):
        if self.logged_in:
            self.create_main_ui()
            self.load_contacts_and_groups()
            self.client.send_command(f"/get_avatar {self.client.username}")

    def open_settings_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Настройки")
        dialog.geometry("470x360")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.transient(self)
        ctk.CTkLabel(dialog, text="Тема:").pack(pady=(20,5))
        theme_var = ctk.StringVar(value=self.settings["theme"])
        ctk.CTkOptionMenu(dialog, values=["dark","light"], variable=theme_var).pack(pady=(0,15))
        ctk.CTkLabel(dialog, text="Цветовая схема:").pack(pady=(10,5))
        color_var = ctk.StringVar(value=self.settings["color_theme"])
        ctk.CTkOptionMenu(dialog, values=["blue","dark-blue","green"], variable=color_var).pack(pady=(0,15))
        ctk.CTkLabel(dialog, text="Размер шрифта:").pack(pady=(10,5))
        font_slider = ctk.CTkSlider(dialog, from_=10, to=20, number_of_steps=10)
        font_slider.set(self.settings["font_size"])
        font_slider.pack(pady=(0,5), padx=20, fill="x")
        font_value = ctk.CTkLabel(dialog, text=f"{int(self.settings['font_size'])}")
        font_value.pack(pady=(0,15))
        def update_font_label(val):
            font_value.configure(text=str(int(val)))
        font_slider.configure(command=update_font_label)
        def save_and_apply():
            new_theme = theme_var.get()
            new_color = color_var.get()
            new_font_size = int(font_slider.get())
            if (new_theme != self.settings["theme"] or 
                new_color != self.settings["color_theme"] or 
                new_font_size != self.settings["font_size"]):
                self.settings.update({"theme":new_theme, "color_theme":new_color, "font_size":new_font_size})
                self.save_settings()
                self.apply_settings()
                self.rebuild_ui()
                messagebox.showinfo("Настройки", "Настройки применены")
            dialog.destroy()
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=20)
        ctk.CTkButton(btn_frame, text="Сохранить", command=save_and_apply, width=120).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Отмена", command=dialog.destroy, width=120, fg_color="gray").pack(side="left", padx=10)

        self.google_bind_btn = ctk.CTkButton(btn_frame, text="Привязать аккаунт Google" + (" (привязан)" if self.google_bound else ""), command=self.start_google_binding)
        self.google_bind_btn.pack(pady=5)

    # ---------- Сессия ----------
    def save_session(self, username, password):
        try:
            # Кодируем пароль в base64 и удаляем лишние символы
            encoded_password = base64.b64encode(password.encode()).decode().strip()
            data = {"username": username, "password": encoded_password}
            with open(get_session_path(), "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Не удалось сохранить сессию: {e}")
    
    def link_google_account(self):
        if not self.logged_in:
            messagebox.showinfo("Внимание", "Сначала войдите в аккаунт")
            return
        # Запускаем OAuth-поток (как при входе, но с другим callback)
        self.google_manager = GoogleAuthManager(
            client_id="724125973432-0rmc8vtupsrv63ao7dbrip8jgqpbomld.apps.googleusercontent.com",
            client_secret=os.getenv("Google_Client_Secret", "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"),
            callback=self.on_google_link_success
        )
        self.google_manager.start_login()
        self.add_system_message_to_chat("Открывается окно Google для привязки...")
        
    
    def on_google_link_success(self, user_info):
        if user_info is None:
            self.add_system_message_to_chat("Ошибка привязки Google: не удалось получить данные")
            return
        email = user_info.get('email')
        if email:
            self.add_system_message_to_chat(f"Аккаунт Google {email} привязан")
            self.client.send_command(f"/bind_google {email}")
        else:
            self.add_system_message_to_chat("Ошибка: email не получен от Google")

    def load_session(self):
        path = get_session_path()
        if not path.exists():
            return None, None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            username = data.get("username", "").strip()
            password_enc = data.get("password", "").strip()
            if username and password_enc:
                # Декодируем base64, игнорируя возможные ошибки padding
                missing_padding = len(password_enc) % 4
                if missing_padding:
                    password_enc += "=" * (4 - missing_padding)
                password = base64.b64decode(password_enc).decode()
                return username, password
        except Exception as e:
            print(f"Не удалось загрузить сессию: {e}")
        return None, None

    def clear_session(self):
        try:
            if SESSION_FILE.exists():
                SESSION_FILE.unlink()
        except:
            pass
    
    def clear_system_messages(self):
        """Очищает файл с системными сообщениями и очередь в памяти"""
        if not self.client.username:
            return
        self.system_messages_queue = []
        path = Path.home() / f".messenger_system_{self.client.username}.json"
        try:
            if path.exists():
                path.unlink()
            self.add_system_message_to_chat("Системные сообщения очищены")
        except Exception as e:
            self.add_system_message_to_chat(f"Ошибка очистки: {e}")
        if self.current_chat_type == "system":
            self.show_system_messages_in_chat()

    def attempt_auto_login(self):
        if self.auto_login_attempted:
            return
        self.auto_login_attempted = True
        username, password = self.load_session()
        if not username or not password:
            print("Нет сохранённой сессии")
            return
        print(f"Автовход для {username}")
        if not self.connected:
            if not self.client.connect():
                self.status_label.configure(text="Автовход не удался: нет соединения с сервером", text_color="red")
                return
            self.connected = True
            self.after(100, self.process_incoming)
        # Заполняем поля (для отображения)
        self.username_entry.delete(0, "end")
        self.username_entry.insert(0, username)
        self.password_entry.delete(0, "end")
        self.password_entry.insert(0, password)
        # Отправляем команду логина
        self.client.send_command(f"/login {username} {password} {self.get_device_name()}")
        self.client.username = username
        self.status_label.configure(text="Автоматический вход...", text_color="gray")

    # ---------- Экран входа ----------
    def create_login_screen(self):
        self.google_manager = GoogleAuthManager(
            client_id="724125973432-0rmc8vtupsrv63ao7dbrip8jgqpbomld.apps.googleusercontent.com",
            client_secret=os.getenv("Google_Client_Secret", "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"),
            callback=self.on_google_login_success
        )
        for widget in self.winfo_children():
            widget.destroy()
        self.login_frame = ctk.CTkFrame(self)
        self.login_frame.pack(expand=True, fill="both", padx=40, pady=40)
        ctk.CTkLabel(self.login_frame, text="GoodVibe", font=ctk.CTkFont(size=32, weight="bold")).pack(pady=(20,30))
        self.username_entry = ctk.CTkEntry(self.login_frame, placeholder_text="Имя пользователя", width=300, height=45)
        self.username_entry.pack(pady=10)
        password_frame = ctk.CTkFrame(self.login_frame, fg_color="transparent")
        password_frame.pack(pady=10)
        self.password_entry = ctk.CTkEntry(password_frame, placeholder_text="Пароль", show="*", width=260, height=45)
        self.password_entry.pack(side="left", padx=(0,5))
        self.toggle_password_btn = ctk.CTkButton(password_frame, text="👁️", width=35, height=35,
                                                 command=self.toggle_password_visibility, fg_color="transparent")
        self.toggle_password_btn.pack(side="left")
        button_frame = ctk.CTkFrame(self.login_frame, fg_color="transparent")
        button_frame.pack(pady=20)
        self.login_btn = ctk.CTkButton(button_frame, text="Вход", width=140, height=40, command=self.do_login)
        self.login_btn.pack(side="left", padx=10)
        self.register_btn = ctk.CTkButton(button_frame, text="Регистрация", width=140, height=40,
                                          fg_color="transparent", border_width=2, command=self.do_register)
        self.register_btn.pack(side="left", padx=10)
        self.status_label = ctk.CTkLabel(self.login_frame, text="", font=ctk.CTkFont(size=12), text_color="gray")
        self.status_label.pack(pady=10)

        self.google_btn = ctk.CTkButton(
            self.login_frame,  # ваш контейнер с полями ввода
            text="Войти с помощью Google",
            command=self.start_google_login
        )
        self.google_btn.pack(pady=10)

    def toggle_password_visibility(self):
        self.password_visible = not self.password_visible
        if self.password_visible:
            self.password_entry.configure(show="")
            self.toggle_password_btn.configure(text="🙈")
        else:
            self.password_entry.configure(show="*")
            self.toggle_password_btn.configure(text="👁️")

    def get_computer_name(self):
        try:
            return socket.gethostname()
        except Exception:
            return None
    
    def start_google_login(self):
        self.status_label.configure(text="Перенаправление в Google...", text_color="blue")
        # Запускаем OAuth-поток
        self.google_manager.start_login()

    def on_google_login_success(self, user_info):
        """Вызывается после успешного получения данных от Google."""
        print("Получены данные пользователя:", user_info)
        # Извлекаем email пользователя
        email = user_info.get('email')
        if email:
            # Показываем сообщение об успешной аутентификации
            self.status_label.configure(text=f"Привет, {email}! Завершаем вход...", text_color="green")
            # ВАЖНО: ТЕПЕРЬ НУЖНО ВЫПОЛНИТЬ РЕГИСТРАЦИЮ/ВХОД НА ВАШЕМ СЕРВЕРЕ!
            # У вас нет пароля от Google, но вы можете использовать email как логин,
            # а в качестве пароля сгенерировать случайную строку.
            # Это потребует доработки серверной части.
            # Отправляем команду на ваш сервер:
            # self.client.send_command(f"/google_login {email}")
        else:
            self.status_label.configure(text="Не удалось получить email от Google", text_color="red")

    def get_device_name(self):
        if self.saved_device_name:
            return self.saved_device_name
        device_name = simpledialog.askstring("Устройство", "Введите название устройства (например, Ноутбук, Телефон):", parent=self)
        if not device_name:
            device_name = self.get_computer_name() or "Неизвестное устройство"
        self.saved_device_name = device_name
        self.settings["device_name"] = device_name
        self.save_settings()
        return device_name

    def do_login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        if not username or not password:
            self.status_label.configure(text="Заполните оба поля", text_color="red")
            return
        device_name = self.get_device_name()
        if self.connected:
            self.client.close()
            self.connected = False
        if not self.client.connect():
            self.status_label.configure(text="Не удалось подключиться к серверу", text_color="red")
            return
        self.connected = True
        self.after(100, self.process_incoming)
        self.client.send_command(f"/login {username} {password} {device_name}")
        self.client.username = username
        self.status_label.configure(text="Выполняется вход...", text_color="gray")

    def do_register(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        if not username or not password:
            self.status_label.configure(text="Заполните оба поля", text_color="red")
            return
        if self.connected:
            self.client.close()
            self.connected = False
        if not self.client.connect():
            self.status_label.configure(text="Не удалось подключиться к серверу", text_color="red")
            return
        self.connected = True
        self.after(100, self.process_incoming)
        self.client.send_command(f"/register {username} {password}")
        self.status_label.configure(text="Регистрация...", text_color="gray")

    # ---------- Кэш сообщений ----------
    def save_cache_for_chat(self, chat_name):
        if chat_name not in self.messages_cache:
            return
        path = self.messages_cache_dir / f"{self.client.username}_{chat_name}.json"
        try:
            data = []
            for msg in self.messages_cache[chat_name]:
                data.append({
                    "sender": msg[0],
                    "text": msg[1],
                    "is_own": msg[2],
                    "timestamp": msg[3],
                    "msg_id": msg[4]
                })
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения кэша {chat_name}: {e}")

    def load_cache_for_chat(self, chat_name):
        path = self.messages_cache_dir / f"{self.client.username}_{chat_name}.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.messages_cache[chat_name] = []
                for item in data:
                    self.messages_cache[chat_name].append(
                        (item["sender"], item["text"], item["is_own"], item["timestamp"], item["msg_id"])
                    )
            self.display_cached_messages(chat_name)
        except Exception as e:
            print(f"Ошибка загрузки кэша {chat_name}: {e}")

    def add_message_to_cache(self, chat_name, sender, text, is_own, timestamp, msg_id):
        if chat_name not in self.messages_cache:
            self.messages_cache[chat_name] = []
        self.messages_cache[chat_name].append((sender, text, is_own, timestamp, msg_id))
        # Ограничим размер кэша (не более 200 сообщений)
        if len(self.messages_cache[chat_name]) > 200:
            self.messages_cache[chat_name].pop(0)
        self.save_cache_for_chat(chat_name)

    def display_cached_messages(self, chat_name):
        self.clear_messages()
        for (sender, text, is_own, timestamp, msg_id) in self.messages_cache.get(chat_name, []):
            MessageWidget(self.messages_frame, self, sender, text, is_own, timestamp, msg_id)
        self.messages_frame._parent_canvas.yview_moveto(1.0)

    def clear_messages(self):
        if self.messages_frame:
            for widget in self.messages_frame.winfo_children():
                widget.destroy()

    # ---------- Отображение сообщений ----------
    def add_chat_message(self, message):
        if not self.messages_frame:
            return
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.+?): (.+) \(id:(\d+)\)', message)
        if match:
            timestamp, sender, text, msg_id = match.groups()
            is_own = (sender == self.client.username)
            MessageWidget(self.messages_frame, self, sender, text, is_own, timestamp, msg_id)
        else:
            # fallback
            frame = ctk.CTkFrame(self.messages_frame, fg_color="transparent")
            frame.pack(fill="x", padx=10, pady=2)
            label = ctk.CTkLabel(frame, text=message, font=ctk.CTkFont(size=13), wraplength=500, justify="left")
            label.pack(pady=5)
        self.messages_frame._parent_canvas.yview_moveto(1.0)

    def send_own_message(self, text):
        if not self.messages_frame or not self.current_chat:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        MessageWidget(self.messages_frame, self, self.client.username, text, True, timestamp, None)
        self.add_message_to_cache(self.current_chat, self.client.username, text, True, timestamp, None)

    # ---------- Обработка входящих сообщений (реалтайм) ----------
    def process_incoming(self):
        try:
            while True:
                msg = self.client.recv_queue.get_nowait()
                self.after(0, lambda m=msg: self.handle_server_message(m))
        except queue.Empty:
            pass
        finally:
            if self.connected:
                self.after(100, self.process_incoming)

    def handle_server_message(self, msg):
        print(f"Пришло сообщение: {msg}")
        print(f"DEBUG: {msg}")
        if msg.startswith("[CMD]"):
            cmd_response = msg[5:].strip()
            if cmd_response.startswith("Добро пожаловать") and not self.logged_in:
                self.logged_in = True
                self.client.send_command("/check_google_binding")
                username = self.username_entry.get().strip()
                password = self.password_entry.get().strip()
                if username and password:
                    self.save_session(username, password)
                self.create_main_ui()
                self.load_system_messages()
                self.load_contacts_and_groups()
                self.client.send_command(f"/get_avatar {self.client.username}")
                return

            if cmd_response.startswith("Контакты:"):
                contacts_part = cmd_response.replace("Контакты:", "").strip()
                if contacts_part and contacts_part != "У вас нет контактов":
                    self.contacts = []
                    for item in contacts_part.split(";"):
                        if "|" in item:
                            username, status = item.split("|", 1)
                            self.contacts.append((username, status == "online"))
                        else:
                            self.contacts.append((item, False))
                else:
                    self.contacts = []
                self.refresh_contacts_list()
                for contact, _ in self.contacts:
                    if contact not in self.avatars_cache:
                        self.client.send_command(f"/get_avatar {contact}")
                return

            if cmd_response.startswith("Группы:"):
                groups_str = cmd_response.replace("Группы:", "").strip()
                self.groups = [g.strip() for g in groups_str.split(",")] if groups_str and groups_str != "Вы не состоите ни в одной группе" else []
                self.refresh_groups_list()
                for group in self.groups:
                    if group not in self.group_avatars_cache:
                        self.client.send_command(f"/get_group_avatar {group}")
                return

            if cmd_response.startswith("STATUS"):
                parts = cmd_response.split(maxsplit=1)
                if len(parts) == 2:
                    data = parts[1]
                    if "|" in data:
                        username, status = data.split("|")
                        if username == self.current_chat and self.current_chat_type == "user":
                            self.update_chat_title_status(status == "online")
                return

            # Аватар пользователя
            if cmd_response.startswith("AVATAR_START"):
                parts = cmd_response.split()
                if len(parts) == 3:
                    self.receiving_avatar = parts[1]
                    self.expected_avatar_chunks = int(parts[2])
                    self.avatar_chunks = {}
                return
            if cmd_response.startswith("AVATAR_CHUNK"):
                if self.receiving_avatar:
                    parts = cmd_response.split(maxsplit=2)
                    if len(parts) == 3:
                        idx = int(parts[1])
                        chunk = parts[2]
                        self.avatar_chunks[idx] = chunk
                return
            if cmd_response.startswith("AVATAR_END"):
                if self.receiving_avatar and len(self.avatar_chunks) == self.expected_avatar_chunks:
                    full_b64 = "".join(self.avatar_chunks[i] for i in range(self.expected_avatar_chunks))
                    full_b64 = full_b64.strip().replace("\n", "").replace("\r", "").replace(" ", "")
                    missing_padding = len(full_b64) % 4
                    if missing_padding:
                        full_b64 += "=" * (4 - missing_padding)
                    try:
                        img_data = base64.b64decode(full_b64)
                        img = Image.open(io.BytesIO(img_data))
                        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(40, 40))
                        self.avatars_cache[self.receiving_avatar] = ctk_img
                        if self.receiving_avatar == self.client.username and hasattr(self, 'user_avatar_label'):
                            self.user_avatar_label.configure(image=ctk_img, text="")
                        self.refresh_contacts_list()
                        if (self.current_chat_type == "user" and self.current_chat == self.receiving_avatar):
                            self.chat_avatar_label.configure(image=ctk_img, text="")
                    except Exception as e:
                        print(f"Ошибка загрузки аватара {self.receiving_avatar}: {e}")
                    self.receiving_avatar = None
                    self.avatar_chunks = {}
                return
            if cmd_response.startswith("AVATAR_DATA None"):
                return

            # Аватар группы
            if cmd_response.startswith("GROUP_AVATAR_START"):
                parts = cmd_response.split()
                if len(parts) == 3:
                    self.receiving_group_avatar = parts[1]
                    self.expected_group_avatar_chunks = int(parts[2])
                    self.group_avatar_chunks = {}
                return
            if cmd_response.startswith("GROUP_AVATAR_CHUNK"):
                if self.receiving_group_avatar:
                    parts = cmd_response.split(maxsplit=2)
                    if len(parts) == 3:
                        idx = int(parts[1])
                        chunk = parts[2]
                        self.group_avatar_chunks[idx] = chunk
                return
            if cmd_response.startswith("GROUP_AVATAR_END"):
                if self.receiving_group_avatar and len(self.group_avatar_chunks) == self.expected_group_avatar_chunks:
                    full_b64 = "".join(self.group_avatar_chunks[i] for i in range(self.expected_group_avatar_chunks))
                    full_b64 = full_b64.strip().replace("\n", "").replace("\r", "").replace(" ", "")
                    missing_padding = len(full_b64) % 4
                    if missing_padding:
                        full_b64 += "=" * (4 - missing_padding)
                    try:
                        img_data = base64.b64decode(full_b64)
                        img = Image.open(io.BytesIO(img_data))
                        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(40, 40))
                        self.group_avatars_cache[self.receiving_group_avatar] = ctk_img
                        self.refresh_groups_list()
                        if self.current_chat_type == "group" and self.current_chat == self.receiving_group_avatar:
                            self.chat_avatar_label.configure(image=ctk_img, text="")
                    except Exception as e:
                        print(f"Ошибка загрузки аватара группы {self.receiving_group_avatar}: {e}")
                    self.receiving_group_avatar = None
                    self.group_avatar_chunks = {}
                return
            if cmd_response.startswith("GROUP_AVATAR_DATA None"):
                return

            # История (запрос с сервера) – используем её для первоначальной загрузки
            if cmd_response.startswith("=== История"):
                self.load_history_into_chat(cmd_response)
                return

            # Системные сообщения (в системный чат)
            if (cmd_response.startswith("Контакт") and "добавлен" in cmd_response) or \
               (cmd_response.startswith("Группа") and "создана" in cmd_response) or \
               (cmd_response == "Этот контакт уже добавлен") or \
               (cmd_response.startswith("Пользователь") and "не найден" in cmd_response) or \
               (cmd_response == "Нельзя добавить самого себя") or \
               (cmd_response.startswith("Пользователь") and "добавлен в группу" in cmd_response) or \
               (cmd_response.startswith("Вы добавлены в группу")) or \
               (cmd_response.startswith("Права группы обновлены")) or \
               (cmd_response.startswith("Устройство переименовано")) or \
               (cmd_response.startswith("SESSION_TERMINATED")) or \
               ("смена аватара" in cmd_response) or \
               (cmd_response.startswith("Группа переименована")):
                self.add_system_message_to_chat(cmd_response)
                if "добавлен" in cmd_response or "создана" in cmd_response or "добавлены в группу" in cmd_response:
                    self.load_contacts_and_groups()
                return

            if cmd_response.startswith("Аватар успешно обновлён"):
                self.add_system_message_to_chat("Ваш аватар обновлён")
                self.client.send_command(f"/get_avatar {self.client.username}")
                return

            if cmd_response.startswith("Аватар группы"):
                self.add_system_message_to_chat(cmd_response)
                if self.current_chat_type == "group":
                    self.client.send_command(f"/get_group_avatar {self.current_chat}")
                return

            if cmd_response.startswith("GROUP_RENAMED"):
                parts = cmd_response.split(maxsplit=1)
                if len(parts) == 2:
                    old_name, new_name = parts[1].split("|")
                    self.load_contacts_and_groups()
                    if self.current_chat == old_name:
                        self.current_chat = new_name
                        self.update_chat_title_status()
                    self.add_system_message_to_chat(f"Группа '{old_name}' переименована в '{new_name}'")
                    # Обновляем кэш: меняем ключ
                    if old_name in self.messages_cache:
                        self.messages_cache[new_name] = self.messages_cache.pop(old_name)
                        # переименовываем файл кэша
                        old_path = self.messages_cache_dir / f"{self.client.username}_{old_name}.json"
                        new_path = self.messages_cache_dir / f"{self.client.username}_{new_name}.json"
                        if old_path.exists():
                            old_path.rename(new_path)
                return

            if cmd_response.startswith("GROUP_PERMS"):
                parts = cmd_response.split(maxsplit=1)
                if len(parts) == 2:
                    data = parts[1]
                    try:
                        group_name, allow_rename, allow_change_avatar, is_creator = data.split("|")
                        self.show_group_permissions_dialog(group_name, allow_rename == '1', allow_change_avatar == '1', is_creator == 'True')
                    except:
                        pass
                return

            if cmd_response.startswith("Участники группы"):
                self.show_group_members_window(cmd_response)
                return

            if cmd_response.startswith("SESSIONS"):
                data = cmd_response.replace("SESSIONS ", "").strip()
                sessions_list = data.split(";") if data else []
                self.handle_sessions_list(sessions_list)
                return

            if self.logged_in:
                self.add_system_message_to_chat(cmd_response)
            
            if cmd_response == "GOOGLE_BOUND True":
                self.google_bound = True
                self.update_google_button_text()
                return
            if cmd_response == "GOOGLE_BOUND False":
                self.google_bound = False
                self.update_google_button_text()
                return

        elif msg.startswith("[MSG]"):
            chat_msg = msg[5:].strip()
            if self.logged_in:
                # Разбор и кэширование входящего сообщения
                is_group = chat_msg.startswith("[ГРУППА")
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if is_group:
                    match = re.match(r'\[ГРУППА (.+?)\] (.+?): (.+)', chat_msg)
                    if match:
                        group_name, sender, text = match.groups()
                        chat_name = group_name
                        is_own = (sender == self.client.username)
                        self.add_message_to_cache(chat_name, sender, text, is_own, timestamp, None)
                        if self.current_chat == chat_name and self.current_chat_type == "group":
                            # Отображаем сразу
                            self.add_chat_message(f"{timestamp} {sender}: {text}")
                else:
                    # Личное сообщение
                    if " (личное):" in chat_msg:
                        sender, text = chat_msg.split(" (личное):", 1)
                        sender = sender.strip()
                        text = text.strip()
                    else:
                        parts = chat_msg.split(":", 1)
                        if len(parts) == 2:
                            sender, text = parts
                            sender = sender.strip()
                            text = text.strip()
                        else:
                            return
                    chat_name = sender
                    is_own = (chat_name == self.client.username)
                    if is_own:
                        # Если это сообщение отправил я, но пришло от сервера (эхо) – не дублируем
                        # В нашем клиенте мы уже сами отображаем своё сообщение, поэтому игнорируем
                        return
                    self.add_message_to_cache(chat_name, sender, text, is_own, timestamp, None)
                    if self.current_chat == chat_name and self.current_chat_type == "user":
                        self.add_chat_message(f"{timestamp} {sender}: {text}")

        else:
            if self.logged_in:
                self.add_system_message_to_chat(msg)

    def load_history_into_chat(self, history_text):
        if not self.current_chat:
            return
        self.clear_messages()
        messages = []
        lines = history_text.split('\n')
        for line in lines:
            if line.startswith("[CMD] === История"):
                continue
            if line.startswith("[CMD]"):
                line = line[5:].strip()
                if line and not line.startswith("==="):
                    match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.+?): (.+) \(id:(\d+)\)', line)
                    if match:
                        timestamp, sender, text, msg_id = match.groups()
                        is_own = (sender == self.client.username)
                        messages.append((sender, text, is_own, timestamp, msg_id))
                        MessageWidget(self.messages_frame, self, sender, text, is_own, timestamp, msg_id)
                    else:
                        frame = ctk.CTkFrame(self.messages_frame, fg_color="transparent")
                        frame.pack(fill="x", padx=10, pady=2)
                        label = ctk.CTkLabel(frame, text=line, font=ctk.CTkFont(size=13), wraplength=500)
                        label.pack(pady=5)
        self.messages_cache[self.current_chat] = messages
        self.save_cache_for_chat(self.current_chat)
        self.messages_frame._parent_canvas.yview_moveto(1.0)

    # ---------- Системные сообщения ----------
    def add_system_message_to_chat(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {text}"
        self.system_messages_queue.append(formatted)
        self.save_system_messages()
        if self.current_chat_type == "system" and self.messages_frame:
            frame = ctk.CTkFrame(self.messages_frame, fg_color="transparent")
            frame.pack(fill="x", padx=10, pady=2)
            label = ctk.CTkLabel(frame, text=formatted, font=ctk.CTkFont(size=12), text_color="gray", wraplength=500, justify="left")
            label.pack(pady=5)
            self.messages_frame._parent_canvas.yview_moveto(1.0)

    def show_system_messages_in_chat(self):
        if not self.messages_frame:
            return
        self.clear_messages()
        for msg in self.system_messages_queue:
            frame = ctk.CTkFrame(self.messages_frame, fg_color="transparent")
            frame.pack(fill="x", padx=10, pady=2)
            label = ctk.CTkLabel(frame, text=msg, font=ctk.CTkFont(size=12), text_color="gray", wraplength=500, justify="left")
            label.pack(pady=5)
        self.messages_frame._parent_canvas.yview_moveto(1.0)

    def save_system_messages(self):
        if not self.client.username:
            return
        path = Path.home() / f".messenger_system_{self.client.username}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.system_messages_queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Не удалось сохранить системные сообщения: {e}")

    def load_system_messages(self):
        if not self.client.username:
            return
        path = Path.home() / f".messenger_system_{self.client.username}.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    self.system_messages_queue = loaded
        except Exception as e:
            print(f"Не удалось загрузить системные сообщения: {e}")

    # ---------- Загрузка контактов и групп ----------
    def load_contacts_and_groups(self):
        self.client.send_command("/contacts")
        self.client.send_command("/groups")

    def refresh_contacts_list(self):
        for widget in self.contacts_frame.winfo_children():
            widget.destroy()
        sys_frame = ctk.CTkFrame(self.contacts_frame, fg_color="transparent")
        sys_frame.pack(fill="x", pady=2, padx=5)
        sys_avatar = ctk.CTkLabel(sys_frame, text="🖥️", width=40, height=40, font=ctk.CTkFont(size=24))
        sys_avatar.pack(side="left", padx=5, pady=5)
        sys_name = ctk.CTkLabel(sys_frame, text="Системные сообщения", font=ctk.CTkFont(size=14, weight="bold"), anchor="w")
        sys_name.pack(side="left", fill="x", expand=True, padx=5)
        sys_frame.bind("<Button-1>", lambda e: self.on_system_chat_select())
        sys_name.bind("<Button-1>", lambda e: self.on_system_chat_select())
        sys_avatar.bind("<Button-1>", lambda e: self.on_system_chat_select())

        for username, is_online in self.contacts:
            contact_frame = ctk.CTkFrame(self.contacts_frame, fg_color="transparent")
            contact_frame.pack(fill="x", pady=2, padx=5)
            if username in self.avatars_cache:
                avatar_label = ctk.CTkLabel(contact_frame, image=self.avatars_cache[username], text="", width=40, height=40)
            else:
                avatar_label = ctk.CTkLabel(contact_frame, text="📷", width=40, height=40, font=ctk.CTkFont(size=20))
            avatar_label.pack(side="left", padx=5, pady=5)
            status_text = "🟢" if is_online else "⚫"
            name_label = ctk.CTkLabel(contact_frame, text=f"{username} {status_text}", font=ctk.CTkFont(size=14), anchor="w")
            name_label.pack(side="left", fill="x", expand=True, padx=5)
            contact_frame.bind("<Button-1>", lambda e, u=username: self.on_contact_select(u))
            name_label.bind("<Button-1>", lambda e, u=username: self.on_contact_select(u))
            avatar_label.bind("<Button-1>", lambda e, u=username: self.on_contact_select(u))

    def refresh_groups_list(self):
        for widget in self.groups_frame.winfo_children():
            widget.destroy()
        for group in self.groups:
            group_frame = ctk.CTkFrame(self.groups_frame, fg_color="transparent")
            group_frame.pack(fill="x", pady=2, padx=5)
            if group in self.group_avatars_cache:
                avatar_label = ctk.CTkLabel(group_frame, image=self.group_avatars_cache[group], text="", width=40, height=40)
            else:
                avatar_label = ctk.CTkLabel(group_frame, text="👥", width=40, height=40, font=ctk.CTkFont(size=20))
            avatar_label.pack(side="left", padx=5, pady=5)
            name_label = ctk.CTkLabel(group_frame, text=group, font=ctk.CTkFont(size=14), anchor="w")
            name_label.pack(side="left", fill="x", expand=True, padx=5)
            group_frame.bind("<Button-1>", lambda e, g=group: self.on_group_select(g))
            name_label.bind("<Button-1>", lambda e, g=group: self.on_group_select(g))
            avatar_label.bind("<Button-1>", lambda e, g=group: self.on_group_select(g))

    # ---------- Переключение чатов ----------
    def on_system_chat_select(self):
        self.current_chat = "system"
        self.current_chat_type = "system"
        self.chat_title.configure(text="Системные сообщения")
        self.add_member_btn.pack_forget()
        self.group_members_btn.pack_forget()
        if hasattr(self, 'group_settings_btn'):
            self.group_settings_btn.pack_forget()
        self.chat_avatar_label.configure(image="", text="🖥️")
        self.message_entry.pack_forget()
        self.send_btn.pack_forget()
        # Кнопка очистки системных сообщений
        if not hasattr(self, 'clear_system_btn'):
            self.clear_system_btn = ctk.CTkButton(self.right_frame, text="🗑 Очистить системные сообщения", 
                                             command=self.clear_system_messages, fg_color="darkred", hover_color="red",
                                             width=220, height=30)
        self.clear_system_btn.pack(pady=(0, 10))
        self.show_system_messages_in_chat()

    def on_contact_select(self, username):
        if username == "Системные сообщения":
            self.on_system_chat_select()
            return
        if hasattr(self, 'clear_system_btn'):
            self.clear_system_btn.pack_forget()
        self.current_chat = username
        self.current_chat_type = "user"
        self.chat_title.configure(text=f"Чат с {username}")
        self.add_member_btn.pack_forget()
        self.group_members_btn.pack_forget()
        if hasattr(self, 'group_settings_btn'):
            self.group_settings_btn.pack_forget()
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0,10))
        self.send_btn.pack(side="right")
        if username in self.avatars_cache:
            self.chat_avatar_label.configure(image=self.avatars_cache[username], text="")
        else:
            self.chat_avatar_label.configure(image="", text="📷")
            self.client.send_command(f"/get_avatar {username}")
        self.load_cache_for_chat(username)
        self._schedule_status_update()

    def on_group_select(self, group):
        if hasattr(self, 'clear_system_btn'):
            self.clear_system_btn.pack_forget()
        self.current_chat = group
        self.current_chat_type = "group"
        self.chat_title.configure(text=f"Группа {group}")
        self.add_member_btn.pack(side="left", padx=(0,10))
        self.group_members_btn.pack(side="left", padx=(0,10))
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0,10))
        self.send_btn.pack(side="right")
        self.update_chat_title_status()
        if group in self.group_avatars_cache:
            self.chat_avatar_label.configure(image=self.group_avatars_cache[group], text="")
        else:
            self.chat_avatar_label.configure(image="", text="👥")
            self.client.send_command(f"/get_group_avatar {group}")
        self.load_cache_for_chat(group)

    # ---------- Действия ----------
    def send_message(self, event=None):
        if self.current_chat_type == "system":
            messagebox.showinfo("Внимание", "В системный чат нельзя отправлять сообщения.")
            return
        if not self.current_chat:
            messagebox.showinfo("Внимание", "Сначала выберите чат")
            return
        msg = self.message_entry.get().strip()
        if not msg:
            return
        self.client.send_command(f"/send {self.current_chat} {msg}")
        self.send_own_message(msg)
        self.message_entry.delete(0, "end")

    def update_chat_title_status(self, is_online=None):
        if self.current_chat_type == "user":
            if is_online is None:
                self.client.send_command(f"/get_status {self.current_chat}")
                return
            status_text = "в сети" if is_online else "не в сети"
            self.chat_title.configure(text=f"Чат с {self.current_chat} - {status_text}")
            if hasattr(self, 'group_settings_btn'):
                self.group_settings_btn.pack_forget()
        else:
            self.chat_title.configure(text=f"Группа {self.current_chat}")
            if not hasattr(self, 'group_settings_btn'):
                self.group_settings_btn = ctk.CTkButton(self.chat_title_frame, text="⚙️", width=30, height=30,
                                                        command=self.open_group_settings, fg_color="transparent")
            self.group_settings_btn.pack(side="right", padx=5)

    def open_group_settings(self):
        if not self.current_chat or self.current_chat_type != "group":
            return
        self.client.send_command(f"/get_group_permissions {self.current_chat}")

    def show_sessions_dialog(self):
        self.client.send_command("/get_sessions")

    def show_group_members(self):
        if not self.current_chat or self.current_chat_type != "group":
            messagebox.showinfo("Внимание", "Эта функция доступна только для групповых чатов")
            return
        self.client.send_command(f"/get_group_members {self.current_chat}")

    def _schedule_status_update(self):
        if hasattr(self, '_status_update_id') and self._status_update_id:
            self.after_cancel(self._status_update_id)
        self._status_update_id = self.after(30000, self.update_current_contact_status)

    def update_current_contact_status(self):
        if self.current_chat and self.current_chat_type == "user" and self.client.running:
            self.client.send_command(f"/get_status {self.current_chat}")
            self._schedule_status_update()

    # ---------- Диалоги ----------
    def add_contact_dialog(self):
        username = ctk.CTkInputDialog(text="Введите имя пользователя:", title="Добавить контакт").get_input()
        if username:
            self.client.send_command(f"/add_contact {username}")

    def create_group_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Создать группу")
        dialog.geometry("400x300")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.transient(self)
        ctk.CTkLabel(dialog, text="Название группы:", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,15))
        name_entry = ctk.CTkEntry(dialog, width=300, height=35)
        name_entry.pack(pady=(0,15))
        ctk.CTkLabel(dialog, text="Участники (через пробел):").pack(pady=(10,5))
        members_entry = ctk.CTkEntry(dialog, width=300, height=35)
        members_entry.pack(pady=(0,20))
        def do_create():
            name = name_entry.get().strip()
            members = members_entry.get().strip().split()
            if name and members:
                self.client.send_command(f"/create_group {name} " + " ".join(members))
                dialog.destroy()
            else:
                messagebox.showerror("Ошибка", "Заполните все поля")
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="Отмена", width=100, command=dialog.destroy, fg_color="gray30").pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Создать", width=100, command=do_create).pack(side="left", padx=10)

    def add_member_dialog(self):
        if not self.current_chat or self.current_chat_type != "group":
            messagebox.showinfo("Внимание", "Эта функция доступна только для групповых чатов")
            return
        username = simpledialog.askstring("Добавить участника", "Введите имя пользователя:", parent=self)
        if username and username.strip():
            self.client.send_command(f"/add_to_group {self.current_chat} {username.strip()}")

    def delete_message_dialog(self):
        if not self.current_chat or self.current_chat_type == "system":
            messagebox.showinfo("Внимание", "Выберите обычный чат")
            return
        msg_id = simpledialog.askstring("Удаление сообщения", "Введите ID сообщения (из истории):", parent=self)
        if not msg_id:
            return
        delete_type = simpledialog.askstring("Удаление сообщения", "Тип удаления:\nself - только у себя\nall - у всех (только для своих сообщений)", parent=self)
        if delete_type not in ["self", "all"]:
            messagebox.showerror("Ошибка", "Неверный тип. Используйте self или all")
            return
        self.client.send_command(f"/delete_message {msg_id} {delete_type}")
        if self.current_chat in self.messages_cache:
            del self.messages_cache[self.current_chat]
            # удаляем файл кэша
            path = self.messages_cache_dir / f"{self.client.username}_{self.current_chat}.json"
            if path.exists():
                path.unlink()
        self.client.send_command(f"/history {self.current_chat} 50")

    # ---------- Аватар ----------
    def change_avatar(self):
        file_path = filedialog.askopenfilename(title="Выберите изображение для аватара", filetypes=[("Image files", "*.png *.jpg *.jpeg *.gif *.bmp")])
        if not file_path:
            return
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                if width != 40 or height != 40:
                    answer = messagebox.askyesno(
                        "Неверный размер",
                        f"Размер изображения {width}x{height}, рекомендуется 40x40.\n\n"
                        "Автоматически масштабировать до 40x40?"
                    )
                    if answer:
                        img = img.resize((40, 40), Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, format='PNG')
                        img_data = base64.b64encode(buf.getvalue()).decode()
                    else:
                        return
                else:
                    with open(file_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode()
            img_data = img_data.strip().replace("\n", "").replace("\r", "")
            chunk_size = 4000
            chunks = [img_data[i:i+chunk_size] for i in range(0, len(img_data), chunk_size)]
            total = len(chunks)
            self.client.send_command(f"/set_avatar_start {total}")
            self.after(200, lambda: self.send_avatar_chunks(chunks))
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить аватар: {e}")

    def send_avatar_chunks(self, chunks):
        for idx, chunk in enumerate(chunks):
            self.client.send_command(f"/set_avatar_chunk {idx} {chunk}")

    def change_group_avatar(self):
        if not self.current_chat or self.current_chat_type != "group":
            messagebox.showinfo("Внимание", "Эта функция доступна только для групповых чатов")
            return
        file_path = filedialog.askopenfilename(title="Выберите изображение для аватара группы", filetypes=[("Image files", "*.png *.jpg *.jpeg *.gif *.bmp")])
        if not file_path:
            return
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                if width != 40 or height != 40:
                    answer = messagebox.askyesno(
                        "Неверный размер",
                        f"Размер изображения {width}x{height}, рекомендуется 40x40.\n\n"
                        "Автоматически масштабировать до 40x40?"
                    )
                    if answer:
                        img = img.resize((40, 40), Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, format='PNG')
                        img_data = base64.b64encode(buf.getvalue()).decode()
                    else:
                        return
                else:
                    with open(file_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode()
            img_data = img_data.strip().replace("\n", "").replace("\r", "")
            chunk_size = 4000
            chunks = [img_data[i:i+chunk_size] for i in range(0, len(img_data), chunk_size)]
            total = len(chunks)
            group_name = self.current_chat
            self.client.send_command(f"/set_group_avatar_start {group_name} {total}")
            self.after(200, lambda: self.send_group_avatar_chunks(chunks))
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить аватар группы: {e}")

    def send_group_avatar_chunks(self, chunks):
        for idx, chunk in enumerate(chunks):
            self.client.send_command(f"/set_group_avatar_chunk {idx} {chunk}")

    # ---------- Устройства ----------
    def handle_sessions_list(self, sessions_list):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Активные устройства")
        dialog.geometry("500x400")
        dialog.resizable(True, True)
        dialog.grab_set()
        dialog.transient(self)

        ctk.CTkLabel(dialog, text="Устройства, подключенные к вашему аккаунту:", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=10)

        if not sessions_list:
            ctk.CTkLabel(dialog, text="Нет активных сессий").pack(pady=20)
            ctk.CTkButton(dialog, text="Закрыть", command=dialog.destroy).pack(pady=10)
            return

        frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        for sess in sessions_list:
            if not sess:
                continue
            token, device, ip, created, last_active, is_current = sess.split("|")
            device_frame = ctk.CTkFrame(frame, fg_color="transparent")
            device_frame.pack(fill="x", pady=5)
            title = f"🟢 {device}" if is_current == "1" else f"📱 {device}"
            ctk.CTkLabel(device_frame, text=title, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(device_frame, text=f"IP: {ip}", font=ctk.CTkFont(size=11)).pack(anchor="w")
            ctk.CTkLabel(device_frame, text=f"Подключено: {created}", font=ctk.CTkFont(size=11)).pack(anchor="w")
            ctk.CTkLabel(device_frame, text=f"Последняя активность: {last_active}", font=ctk.CTkFont(size=11)).pack(anchor="w")
            if is_current == "1":
                rename_btn = ctk.CTkButton(device_frame, text="✏️ Переименовать", width=120, height=25,
                                           command=lambda d=device, t=token: self.rename_current_device(d, t, dialog))
                rename_btn.pack(anchor="w", pady=5)
                ctk.CTkLabel(device_frame, text="(текущее устройство)", font=ctk.CTkFont(size=11, italic=True), text_color="green").pack(anchor="w")
            else:
                terminate_btn = ctk.CTkButton(device_frame, text="Завершить сессию", width=100, height=25,
                                              command=lambda t=token: self.terminate_session(t, dialog))
                terminate_btn.pack(anchor="w", pady=5)
            ctk.CTkFrame(frame, height=1, fg_color="gray20").pack(fill="x", pady=5)

        ctk.CTkButton(dialog, text="Закрыть", command=dialog.destroy).pack(pady=10)

    def rename_current_device(self, current_name, token, parent_dialog):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Переименование устройства")
        dialog.geometry("350x200")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.transient(self)

        ctk.CTkLabel(dialog, text=f"Текущее имя: {current_name}", font=ctk.CTkFont(size=12)).pack(pady=(15,5))
        ctk.CTkLabel(dialog, text="Введите новое имя устройства:", font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(10,5))

        name_entry = ctk.CTkEntry(dialog, width=250)
        name_entry.pack(pady=5)

        def set_computer_name():
            computer_name = self.get_computer_name()
            if computer_name:
                name_entry.delete(0, "end")
                name_entry.insert(0, computer_name)
            else:
                messagebox.showerror("Ошибка", "Не удалось получить имя компьютера.")

        computer_name_btn = ctk.CTkButton(dialog, text="💻 Задать имя как на компьютере", 
                                          command=set_computer_name, 
                                          width=250, height=30,
                                          fg_color="gray30", hover_color="gray40")
        computer_name_btn.pack(pady=(10,5))

        def save():
            new_name = name_entry.get().strip()
            if new_name:
                if len(new_name) > 50:
                    messagebox.showerror("Ошибка", "Имя устройства слишком длинное (макс. 50 символов)")
                    return
                parent_dialog.destroy()
                self.client.send_command(f"/rename_device {new_name}")
            else:
                messagebox.showwarning("Предупреждение", "Имя не может быть пустым.")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Сохранить", command=save, width=100).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Отмена", command=dialog.destroy, width=100, fg_color="gray").pack(side="left", padx=10)

    def terminate_session(self, token, parent_dialog):
        self.client.send_command(f"/terminate_session {token}")
        parent_dialog.destroy()
        self.add_system_message_to_chat("Запрос на завершение сессии отправлен")
    
    def show_input_context_menu(self, event):
        self.input_context_menu.post(event.x_root, event.y_root)

    def paste_from_clipboard(self):
        try:
            # Попытка получить текст из буфера обмена
            text = self.clipboard_get()
            if text:
                current = self.message_entry.get()
                self.message_entry.delete(0, "end")
                self.message_entry.insert(0, current + text)
        except Exception as e:
            self.add_system_message_to_chat(f"Не удалось вставить из буфера обмена: {e}")

    # ---------- Групповые права ----------
    def show_group_permissions_dialog(self, group_name, allow_rename, allow_change_avatar, is_creator):
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Настройки группы: {group_name}")
        dialog.geometry("400x500")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.transient(self)

        ctk.CTkLabel(dialog, text=f"Группа: {group_name}", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)

        actions_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        actions_frame.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(actions_frame, text="Действия:", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(5,0))

        rename_btn = ctk.CTkButton(actions_frame, text="✏️ Переименовать группу",
                                   command=lambda: self.rename_group_dialog(dialog))
        if not is_creator and not allow_rename:
            rename_btn.configure(state="disabled", text="✏️ Переименовать группу (запрещено)")
        rename_btn.pack(fill="x", pady=5)

        avatar_btn = ctk.CTkButton(actions_frame, text="🖼️ Сменить аватар группы",
                                   command=lambda: [dialog.destroy(), self.change_group_avatar()])
        if not is_creator and not allow_change_avatar:
            avatar_btn.configure(state="disabled", text="🖼️ Сменить аватар группы (запрещено)")
        avatar_btn.pack(fill="x", pady=5)

        ctk.CTkFrame(dialog, height=2, fg_color="gray20").pack(fill="x", padx=20, pady=10)

        if is_creator:
            ctk.CTkLabel(dialog, text="Настройка прав участников:", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10,5))
            rename_var = ctk.BooleanVar(value=allow_rename)
            avatar_var = ctk.BooleanVar(value=allow_change_avatar)

            def save_permissions():
                rename_perm = "allow" if rename_var.get() else "deny"
                avatar_perm = "allow" if avatar_var.get() else "deny"
                self.client.send_command(f"/set_group_permissions {group_name} {rename_perm} {avatar_perm}")
                dialog.destroy()
                self.add_system_message_to_chat("Права группы обновлены")

            rename_check = ctk.CTkCheckBox(dialog, text="Разрешить переименовывать группу", variable=rename_var)
            rename_check.pack(anchor="w", padx=20, pady=5)
            avatar_check = ctk.CTkCheckBox(dialog, text="Разрешить менять аватар группы", variable=avatar_var)
            avatar_check.pack(anchor="w", padx=20, pady=5)

            btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            btn_frame.pack(pady=20)
            ctk.CTkButton(btn_frame, text="Сохранить", command=save_permissions, width=100).pack(side="left", padx=10)
            ctk.CTkButton(btn_frame, text="Отмена", command=dialog.destroy, width=100, fg_color="gray").pack(side="left", padx=10)
        else:
            ctk.CTkLabel(dialog, text="Ваши права:", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10,5))
            ctk.CTkLabel(dialog, text=f"- Переименование: {'разрешено' if allow_rename else 'запрещено'}",
                         font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
            ctk.CTkLabel(dialog, text=f"- Смена аватара: {'разрешена' if allow_change_avatar else 'запрещена'}",
                         font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
            ctk.CTkButton(dialog, text="Закрыть", command=dialog.destroy, width=100).pack(pady=20)

    def rename_group_dialog(self, parent_dialog=None):
        if parent_dialog:
            parent_dialog.destroy()
        new_name = simpledialog.askstring("Переименование группы", "Введите новое название группы:", parent=self)
        if new_name and new_name.strip():
            self.client.send_command(f"/rename_group {self.current_chat} {new_name.strip()}")

    def show_group_members_window(self, cmd_response):
        try:
            header, members_data_raw = cmd_response.split(": ", 1)
            group_name = header.replace("Участники группы", "").strip()
            members = []
            if members_data_raw:
                for item in members_data_raw.split(";"):
                    if "|" in item:
                        username, role = item.split("|", 1)
                        is_admin = (role == "Админ")
                    else:
                        username = item
                        is_admin = False
                    members.append((username, is_admin))
            dialog = ctk.CTkToplevel(self)
            dialog.title(f"Участники группы: {group_name}")
            dialog.geometry("400x500")
            dialog.resizable(False, False)
            dialog.grab_set()
            dialog.transient(self)

            title_label = ctk.CTkLabel(dialog, text=f"Группа: {group_name}", font=ctk.CTkFont(size=18, weight="bold"))
            title_label.pack(pady=(15,10))

            members_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
            members_frame.pack(fill="both", expand=True, padx=10, pady=10)

            for username, is_admin in members:
                member_frame = ctk.CTkFrame(members_frame, fg_color="transparent")
                member_frame.pack(fill="x", pady=5, padx=5)
                if username in self.avatars_cache:
                    avatar_label = ctk.CTkLabel(member_frame, image=self.avatars_cache[username], text="", width=40, height=40)
                else:
                    avatar_label = ctk.CTkLabel(member_frame, text="👤", width=40, height=40, font=ctk.CTkFont(size=24))
                avatar_label.pack(side="left", padx=5, pady=5)
                name_text = f"{username} (Админ)" if is_admin else username
                name_label = ctk.CTkLabel(member_frame, text=name_text, font=ctk.CTkFont(size=14), anchor="w")
                name_label.pack(side="left", fill="x", expand=True, padx=5)
            close_btn = ctk.CTkButton(dialog, text="Закрыть", command=dialog.destroy, width=100)
            close_btn.pack(pady=10)
        except Exception as e:
            messagebox.showinfo("Участники группы", cmd_response)

    # ---------- Выход и завершение ----------
    def logout(self):
        if hasattr(self, 'clear_system_btn'):
            self.clear_system_btn.pack_forget()
        self.client.send_command("/logout")
        self.logged_in = False
        self.client.close()
        self.connected = False
        self.clear_session()
        self.contacts = []
        self.groups = []
        self.avatars_cache.clear()
        self.group_avatars_cache.clear()
        self.messages_cache.clear()
        if hasattr(self, 'group_settings_btn'):
            self.group_settings_btn.pack_forget()
        if self._status_update_id:
            self.after_cancel(self._status_update_id)
        self.create_login_screen()

    def on_closing(self):
        if self.logged_in:
            self.client.send_command("/logout")
        self.client.close()
        self.destroy()
    
    def on_google_bound(self, user_info):
        if user_info is None:
            self.add_system_message_to_chat("Ошибка привязки Google")
            return
        email = user_info.get('email')
        if email:
            self.add_system_message_to_chat(f"Аккаунт Google {email} привязан")
            self.client.send_command(f"/bind_google {email}")
            # Обновляем статус привязки
            self.google_bound = True
            self.settings["google_bound"] = True
            self.save_settings()
            # Обновляем текст кнопки, если она существует
            if hasattr(self, 'google_bind_btn'):
                self.google_bind_btn.configure(text="Привязать аккаунт Google (привязан)")
        else:
            self.add_system_message_to_chat("Не удалось получить email от Google")
    
    def update_google_button_text(self):
        if hasattr(self, 'google_bind_btn'):
            if self.google_bound:
                self.google_bind_btn.configure(text="Привязать аккаунт Google (привязан)")
            else:
                self.google_bind_btn.configure(text="Привязать аккаунт Google")
    
    def setup_google_binding(self):
        if not hasattr(self, 'google_auth'):
            self.google_auth = GoogleAuthManager(
                client_id="724125973432-0rmc8vtupsrv63ao7dbrip8jgqpbomld.apps.googleusercontent.com",
                client_secret=os.getenv("Google_Client_Secret", "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"),
                callback=self.on_google_bound
            )
        self.google_auth.start_login()
    
    def start_google_binding(self):
        """Запускает процесс привязки аккаунта Google (вызывается из кнопки)"""
        if not hasattr(self, 'google_auth'):
            self.google_auth = GoogleAuthManager(
                client_id="724125973432-0rmc8vtupsrv63ao7dbrip8jgqpbomld.apps.googleusercontent.com",
                client_secret=os.getenv("Google_Client_Secret", "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"),
                callback=self.on_google_bound
        )
        self.google_auth.start_login()

    # ---------- Создание главного интерфейса ----------
    def create_main_ui(self):
        for widget in self.winfo_children():
            widget.destroy()

        self.top_frame = ctk.CTkFrame(self, height=50, fg_color="transparent")
        self.top_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(5,0))
        self.settings_btn = ctk.CTkButton(self.top_frame, text="⚙️", width=40, height=40,
                                          command=self.open_settings_dialog, fg_color="transparent", hover_color="gray30")
        self.settings_btn.pack(side="right", padx=10, pady=5)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.left_frame = ctk.CTkFrame(self, width=280, corner_radius=0)
        self.left_frame.grid(row=1, column=0, sticky="nsew")
        self.left_frame.grid_propagate(False)

        user_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        user_frame.pack(fill="x", padx=10, pady=(10,5))
        self.user_avatar_label = ctk.CTkLabel(user_frame, text="📷", width=40, height=40, font=ctk.CTkFont(size=20))
        self.user_avatar_label.pack(side="left", padx=(0,10))
        self.user_name_label = ctk.CTkLabel(user_frame, text=self.client.username, font=ctk.CTkFont(size=16, weight="bold"))
        self.user_name_label.pack(side="left", fill="x", expand=True)
        change_avatar_btn = ctk.CTkButton(user_frame, text="✏️", width=30, height=30, command=self.change_avatar)
        change_avatar_btn.pack(side="right", padx=5)

        ctk.CTkFrame(self.left_frame, height=2, fg_color="gray20").pack(fill="x", padx=10, pady=5)

        self.contacts_frame = ctk.CTkScrollableFrame(self.left_frame, label_text="Контакты", height=200)
        self.contacts_frame.pack(fill="x", padx=10, pady=5)
        self.groups_frame = ctk.CTkScrollableFrame(self.left_frame, label_text="Группы", height=150)
        self.groups_frame.pack(fill="x", padx=10, pady=5)

        btn_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        btn_frame.pack(pady=20, fill="x", padx=10)
        ctk.CTkButton(btn_frame, text="➕ Добавить контакт", command=self.add_contact_dialog, height=35).pack(fill="x", pady=3)
        ctk.CTkButton(btn_frame, text="👥 Создать группу", command=self.create_group_dialog, height=35).pack(fill="x", pady=3)
        ctk.CTkButton(btn_frame, text="🔄 Обновить", command=self.load_contacts_and_groups, height=35, fg_color="gray30").pack(fill="x", pady=3)
        devices_btn = ctk.CTkButton(btn_frame, text="📱 Устройства", command=self.show_sessions_dialog, height=35, fg_color="gray30", hover_color="gray40")
        devices_btn.pack(fill="x", pady=3)
        ctk.CTkButton(btn_frame, text="🚪 Выйти", command=self.logout, height=35, fg_color="darkred", hover_color="red").pack(fill="x", pady=(20,3))

        self.right_frame = ctk.CTkFrame(self, corner_radius=0)
        self.right_frame.grid(row=1, column=1, sticky="nsew")
        self.chat_title_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.chat_title_frame.pack(fill="x", padx=10, pady=(10,5))
        self.chat_avatar_label = ctk.CTkLabel(self.chat_title_frame, text="", width=40, height=40)
        self.chat_avatar_label.pack(side="left", padx=(0,10))
        self.chat_title = ctk.CTkLabel(self.chat_title_frame, text="Выберите чат", font=ctk.CTkFont(size=18, weight="bold"))
        self.chat_title.pack(side="left", fill="x", expand=True)

        ctk.CTkFrame(self.right_frame, height=2, fg_color="gray20").pack(fill="x", padx=10, pady=5)

        self.messages_frame = ctk.CTkScrollableFrame(self.right_frame, fg_color="transparent")
        self.messages_frame.pack(fill="both", expand=True, padx=10, pady=10)

        input_frame = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        input_frame.pack(fill="x", padx=10, pady=(0,15))
        self.message_entry = ctk.CTkEntry(input_frame, placeholder_text="Введите сообщение...", height=40)
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0,10))
        self.message_entry.bind("<Return>", self.send_message)

        # --- Контекстное меню для поля ввода ---
        self.message_entry.bind("<Button-3>", self.show_input_context_menu)
        self.input_context_menu = tk.Menu(self, tearoff=0)
        self.input_context_menu.add_command(label="📋 Вставить последнее сообщение из буфера обмена", 
                                    command=self.paste_from_clipboard)
        self.send_btn = ctk.CTkButton(input_frame, text="Отправить", width=100, height=40, command=self.send_message)
        self.send_btn.pack(side="right")

        self.add_member_btn = ctk.CTkButton(self.right_frame, text="➕ Добавить участника", width=150, height=30,
                                            command=self.add_member_dialog, fg_color="green", hover_color="darkgreen")
        self.group_members_btn = ctk.CTkButton(self.right_frame, text="👥 Участники", width=130, height=30,
                                               command=self.show_group_members, fg_color="gray30")
        self.add_member_btn.pack_forget()
        self.group_members_btn.pack_forget()

        self.refresh_contacts_list()
        self.refresh_groups_list()

if __name__ == "__main__":
    app = MessengerGUI()
    app.mainloop()