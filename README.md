# Мессенджер на Python

GoodVibe - это простой мессенджер с клиент-серверной архитектурой.  
Поддерживает личные и групповые чаты, аватарки, онлайн-статусы, удаление сообщений, автовход и настройки.

## Возможности
- Регистрация и логин (с сохранением сессии)
- Личные и групповые чаты
- Отправка и получение сообщений в реальном времени
- Аватарки пользователей и групп
- Онлайн-статусы контактов
- Удаление сообщений (у себя или у всех)
- Настройки темы, цветовой схемы, размера шрифта
- Автовход при повторном запуске
- Привязка аккаунта Google (OAuth)
- Кроссплатформенность (Windows, Linux, macOS)

## Установка и запуск

### Сервер
1. Установите зависимости: `pip install -r requirements.txt`
2. Запустите сервер: `python server.py`

### Клиент
1. Установите зависимости: `pip install -r requirements.txt`
2. Запустите клиент: `python client_gui_custom.py`

## Сборка .exe
```bash
pip install pyinstaller
pyinstaller --onefile --name messenger_server server.py
pyinstaller --onefile --windowed --name messenger_client --hidden-import=CTkListbox client_gui_custom.py