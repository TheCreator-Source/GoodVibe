import asyncio
import sqlite3
import hashlib
import base64
import secrets
from datetime import datetime
from pathlib import Path
import os

DB_NAME = "messenger.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS contacts (
                    user_id INTEGER,
                    contact_id INTEGER,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(contact_id) REFERENCES users(id),
                    PRIMARY KEY(user_id, contact_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    creator_id INTEGER,
                    allow_rename INTEGER DEFAULT 0,
                    allow_change_avatar INTEGER DEFAULT 0,
                    FOREIGN KEY(creator_id) REFERENCES users(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS group_members (
                    group_id INTEGER,
                    user_id INTEGER,
                    FOREIGN KEY(group_id) REFERENCES groups(id),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    PRIMARY KEY(group_id, user_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER,
                    to_user_id INTEGER,
                    to_group_id INTEGER,
                    message TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    delivered INTEGER DEFAULT 0,
                    FOREIGN KEY(from_user_id) REFERENCES users(id),
                    FOREIGN KEY(to_user_id) REFERENCES users(id),
                    FOREIGN KEY(to_group_id) REFERENCES groups(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS avatars (
                    user_id INTEGER PRIMARY KEY,
                    avatar_base64 TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS group_avatars (
                    group_id INTEGER PRIMARY KEY,
                    avatar_base64 TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(group_id) REFERENCES groups(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS hidden_messages (
                    user_id INTEGER,
                    message_id INTEGER,
                    PRIMARY KEY (user_id, message_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    session_token TEXT UNIQUE NOT NULL,
                    device_name TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_current INTEGER DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(id))''')
    try:
        c.execute("ALTER TABLE groups ADD COLUMN allow_rename INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE groups ADD COLUMN allow_change_avatar INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE messages ADD COLUMN delivered INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN google_email TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN google_email TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

online_users = {}          # username -> (writer, user_id, session_token)
avatar_uploads = {}
group_avatar_uploads = {}

def generate_session_token():
    return secrets.token_urlsafe(32)

async def handle_command(reader, writer, username, command, args, user_id, session_token):
    if command == "add_contact":
        if not args:
            return "[CMD] Ошибка: укажите имя контакта"
        contact_name = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        if contact_name == username:
            conn.close()
            return "[CMD] Нельзя добавить самого себя"
        c.execute("SELECT id FROM users WHERE username=?", (contact_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Пользователь {contact_name} не найден"
        contact_id = row[0]
        c.execute("SELECT 1 FROM contacts WHERE user_id=? AND contact_id=?", (user_id, contact_id))
        if c.fetchone():
            conn.close()
            return "[CMD] Этот контакт уже добавлен"
        c.execute("INSERT INTO contacts (user_id, contact_id) VALUES (?, ?)", (user_id, contact_id))
        c.execute("INSERT INTO contacts (user_id, contact_id) VALUES (?, ?)", (contact_id, user_id))
        conn.commit()
        conn.close()
        return f"[CMD] Контакт {contact_name} добавлен"

    elif command == "create_group":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите название группы и минимум одного участника"
        group_name = args[0]
        members = args[1:]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO groups (name, creator_id) VALUES (?, ?)", (group_name, user_id))
        group_id = c.lastrowid
        c.execute("INSERT INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
        added = []
        for m in members:
            c.execute("SELECT id FROM users WHERE username=?", (m,))
            row = c.fetchone()
            if row:
                c.execute("INSERT INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, row[0]))
                added.append(m)
            else:
                added.append(f"{m} (не найден)")
        conn.commit()
        conn.close()
        return f"[CMD] Группа '{group_name}' создана. Участники: {', '.join(added)}"

    elif command == "set_avatar_start":
        if len(args) < 1:
            return "[CMD] Ошибка: укажите количество частей"
        total_chunks = int(args[0])
        avatar_uploads[user_id] = {"chunks": {}, "total_chunks": total_chunks}
        return "[CMD] OK"

    elif command == "set_avatar_chunk":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите индекс части и данные"
        chunk_index = int(args[0])
        chunk_data = args[1]
        if user_id not in avatar_uploads:
            return "[CMD] Ошибка: сессия загрузки аватара не найдена"
        upload = avatar_uploads[user_id]
        upload["chunks"][chunk_index] = chunk_data
        if len(upload["chunks"]) == upload["total_chunks"]:
            full_b64 = "".join(upload["chunks"][i] for i in range(upload["total_chunks"]))
            full_b64 = full_b64.strip().replace("\n", "").replace("\r", "")
            try:
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("REPLACE INTO avatars (user_id, avatar_base64, updated_at) VALUES (?, ?, ?)",
                          (user_id, full_b64, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                conn.close()
                del avatar_uploads[user_id]
                return "[CMD] Аватар успешно обновлён"
            except Exception as e:
                del avatar_uploads[user_id]
                return f"[CMD] Ошибка сохранения аватара: {e}"
        return "[CMD] CHUNK_OK"

    elif command == "get_avatar":
        if len(args) < 1:
            return "[CMD] Укажите имя пользователя"
        target_user = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=?", (target_user,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Пользователь {target_user} не найден"
        target_id = row[0]
        c.execute("SELECT avatar_base64 FROM avatars WHERE user_id=?", (target_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            data = row[0].strip().replace("\n", "").replace("\r", "")
            chunk_size = 8000
            chunks = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]
            total = len(chunks)
            writer.write(f"[CMD] AVATAR_START {target_user} {total}\n".encode())
            await writer.drain()
            for idx, chunk in enumerate(chunks):
                writer.write(f"[CMD] AVATAR_CHUNK {idx} {chunk}\n".encode())
                await writer.drain()
            writer.write(f"[CMD] AVATAR_END\n".encode())
            await writer.drain()
            return None
        else:
            return "[CMD] AVATAR_DATA None"

    elif command == "set_group_avatar_start":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите название группы и количество частей"
        group_name = args[0]
        total_chunks = int(args[1])
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, creator_id, allow_change_avatar FROM groups WHERE name=?", (group_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Группа '{group_name}' не найдена"
        group_id, creator_id, allow_change_avatar = row
        c.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))
        if not c.fetchone():
            conn.close()
            return "[CMD] Вы не состоите в этой группе"
        if user_id != creator_id and allow_change_avatar == 0:
            conn.close()
            return "[CMD] Вы не можете изменить аватар группы (запрещено администратором)"
        conn.close()
        key = (group_id, user_id)
        group_avatar_uploads[key] = {"chunks": {}, "total_chunks": total_chunks, "group_id": group_id}
        return "[CMD] OK"

    elif command == "set_group_avatar_chunk":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите индекс части и данные"
        chunk_index = int(args[0])
        chunk_data = args[1]
        found_key = None
        for key in group_avatar_uploads:
            if key[1] == user_id:
                found_key = key
                break
        if not found_key:
            return "[CMD] Ошибка: сессия загрузки аватара группы не найдена"
        upload = group_avatar_uploads[found_key]
        upload["chunks"][chunk_index] = chunk_data
        if len(upload["chunks"]) == upload["total_chunks"]:
            full_b64 = "".join(upload["chunks"][i] for i in range(upload["total_chunks"]))
            full_b64 = full_b64.strip().replace("\n", "").replace("\r", "")
            try:
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("REPLACE INTO group_avatars (group_id, avatar_base64, updated_at) VALUES (?, ?, ?)",
                          (upload["group_id"], full_b64, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                conn.close()
                del group_avatar_uploads[found_key]
                return "[CMD] Аватар группы успешно обновлён"
            except Exception as e:
                del group_avatar_uploads[found_key]
                return f"[CMD] Ошибка сохранения аватара группы: {e}"
        return "[CMD] CHUNK_OK"

    elif command == "get_group_avatar":
        if len(args) < 1:
            return "[CMD] Укажите название группы"
        group_name = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM groups WHERE name=?", (group_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Группа '{group_name}' не найдена"
        group_id = row[0]
        c.execute("SELECT avatar_base64 FROM group_avatars WHERE group_id=?", (group_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            data = row[0].strip().replace("\n", "").replace("\r", "")
            chunk_size = 8000
            chunks = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]
            total = len(chunks)
            writer.write(f"[CMD] GROUP_AVATAR_START {group_name} {total}\n".encode())
            await writer.drain()
            for idx, chunk in enumerate(chunks):
                writer.write(f"[CMD] GROUP_AVATAR_CHUNK {idx} {chunk}\n".encode())
                await writer.drain()
            writer.write(f"[CMD] GROUP_AVATAR_END\n".encode())
            await writer.drain()
            return None
        else:
            return "[CMD] GROUP_AVATAR_DATA None"

    elif command == "send":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите получателя и текст сообщения"
        recipient = args[0]
        message_text = " ".join(args[1:])
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("SELECT id FROM groups WHERE name=?", (recipient,))
        group_row = c.fetchone()
        if group_row:
            group_id = group_row[0]
            c.execute("INSERT INTO messages (from_user_id, to_group_id, message, timestamp, delivered) VALUES (?, ?, ?, ?, 1)",
                      (user_id, group_id, message_text, now_local))
            conn.commit()
            c.execute("SELECT user_id FROM group_members WHERE group_id=?", (group_id,))
            members = [row[0] for row in c.fetchall()]
            for member_id in members:
                c.execute("SELECT username FROM users WHERE id=?", (member_id,))
                member_name = c.fetchone()[0]
                if member_name in online_users and member_name != username:
                    target_writer = online_users[member_name][0]
                    display = f"[MSG] [ГРУППА {recipient}] {username}: {message_text}"
                    target_writer.write((display + "\n").encode())
                    await target_writer.drain()
            conn.close()
            return f"[CMD] Сообщение в группу '{recipient}' отправлено"
        else:
            c.execute("SELECT id FROM users WHERE username=?", (recipient,))
            to_row = c.fetchone()
            if not to_row:
                conn.close()
                return f"[CMD] Пользователь {recipient} не найден"
            to_id = to_row[0]
            c.execute("INSERT INTO messages (from_user_id, to_user_id, message, timestamp, delivered) VALUES (?, ?, ?, ?, 0)",
                      (user_id, to_id, message_text, now_local))
            conn.commit()
            if recipient in online_users:
                target_writer = online_users[recipient][0]
                display = f"[MSG] {username} (личное): {message_text}"
                target_writer.write((display + "\n").encode())
                await target_writer.drain()
                c.execute("UPDATE messages SET delivered = 1 WHERE from_user_id=? AND to_user_id=? AND message=? AND timestamp=?",
                          (user_id, to_id, message_text, now_local))
                conn.commit()
                conn.close()
                return f"[CMD] Сообщение для {recipient} доставлено"
            else:
                conn.close()
                return f"[CMD] Сообщение для {recipient} сохранено (пользователь офлайн)"

    elif command == "delete_message":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите ID сообщения и тип удаления (self/all)"
        msg_id = int(args[0])
        delete_type = args[1]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT from_user_id FROM messages WHERE id=?", (msg_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return "[CMD] Сообщение не найдено"
        from_user_id = row[0]
        if delete_type == "all":
            if from_user_id != user_id:
                conn.close()
                return "[CMD] Вы можете удалить у всех только свои сообщения"
            c.execute("DELETE FROM messages WHERE id=?", (msg_id,))
            conn.commit()
            conn.close()
            return "[CMD] Сообщение удалено у всех"
        elif delete_type == "self":
            c.execute("INSERT OR IGNORE INTO hidden_messages (user_id, message_id) VALUES (?, ?)", (user_id, msg_id))
            conn.commit()
            conn.close()
            return "[CMD] Сообщение скрыто у вас"
        else:
            conn.close()
            return "[CMD] Неверный тип удаления (используйте self или all)"

    elif command == "history":
        if not args:
            return "[CMD] Укажите имя пользователя или группы"
        target = args[0]
        limit = 50
        if len(args) > 1 and args[1].isdigit():
            limit = int(args[1])
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM groups WHERE name=?", (target,))
        group_row = c.fetchone()
        if group_row:
            group_id = group_row[0]
            c.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))
            if not c.fetchone():
                conn.close()
                return "[CMD] Вы не состоите в этой группе"
            c.execute('''SELECT m.id, u.username, m.message, m.timestamp 
                         FROM messages m
                         JOIN users u ON m.from_user_id = u.id
                         WHERE m.to_group_id = ?
                         AND NOT EXISTS (SELECT 1 FROM hidden_messages hm WHERE hm.user_id=? AND hm.message_id=m.id)
                         ORDER BY m.timestamp DESC LIMIT ?''', (group_id, user_id, limit))
            rows = c.fetchall()
            if not rows:
                conn.close()
                return f"[CMD] История группы '{target}' пуста"
            result = [f"[CMD] === История группы {target} ==="]
            for r in reversed(rows):
                result.append(f"[CMD] {r[3]} {r[1]}: {r[2]} (id:{r[0]})")
            conn.close()
            return "\n".join(result)
        else:
            c.execute("SELECT id FROM users WHERE username=?", (target,))
            other_row = c.fetchone()
            if not other_row:
                conn.close()
                return f"[CMD] Пользователь {target} не найден"
            other_id = other_row[0]
            c.execute('''SELECT m.id, u.username, m.message, m.timestamp 
                         FROM messages m
                         JOIN users u ON m.from_user_id = u.id
                         WHERE ((m.from_user_id=? AND m.to_user_id=?) OR (m.from_user_id=? AND m.to_user_id=?))
                         AND NOT EXISTS (SELECT 1 FROM hidden_messages hm WHERE hm.user_id=? AND hm.message_id=m.id)
                         ORDER BY m.timestamp DESC LIMIT ?''',
                      (user_id, other_id, other_id, user_id, user_id, limit))
            rows = c.fetchall()
            if not rows:
                conn.close()
                return f"[CMD] Нет истории с {target}"
            result = [f"[CMD] === История с {target} ==="]
            for r in reversed(rows):
                result.append(f"[CMD] {r[3]} {r[1]}: {r[2]} (id:{r[0]})")
            conn.close()
            return "\n".join(result)

    elif command == "contacts":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''SELECT u.username FROM contacts c
                     JOIN users u ON c.contact_id = u.id
                     WHERE c.user_id = ?''', (user_id,))
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "[CMD] У вас нет контактов"
        contact_list = []
        for (username_,) in rows:
            is_online = username_ in online_users
            contact_list.append(f"{username_}|{'online' if is_online else 'offline'}")
        return "[CMD] Контакты: " + ";".join(contact_list)

    elif command == "groups":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''SELECT g.name FROM groups g
                     JOIN group_members gm ON g.id = gm.group_id
                     WHERE gm.user_id = ?''', (user_id,))
        groups = [row[0] for row in c.fetchall()]
        conn.close()
        if not groups:
            return "[CMD] Вы не состоите ни в одной группе"
        return "[CMD] Группы: " + ", ".join(groups)

    elif command == "add_to_group":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите название группы и имя пользователя"
        group_name = args[0]
        target_username = args[1]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM groups WHERE name=?", (group_name,))
        group_row = c.fetchone()
        if not group_row:
            conn.close()
            return f"[CMD] Группа '{group_name}' не найдена"
        group_id = group_row[0]
        c.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))
        if not c.fetchone():
            conn.close()
            return "[CMD] Вы не состоите в этой группе"
        c.execute("SELECT id FROM users WHERE username=?", (target_username,))
        target_row = c.fetchone()
        if not target_row:
            conn.close()
            return f"[CMD] Пользователь {target_username} не найден"
        target_id = target_row[0]
        c.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (group_id, target_id))
        if c.fetchone():
            conn.close()
            return f"[CMD] Пользователь {target_username} уже состоит в группе"
        c.execute("INSERT INTO group_members (group_id, user_id) VALUES (?, ?)", (group_id, target_id))
        conn.commit()
        conn.close()
        if target_username in online_users:
            target_writer = online_users[target_username][0]
            target_writer.write(f"[CMD] Вы добавлены в группу {group_name}\n".encode())
            await target_writer.drain()
        return f"[CMD] Пользователь {target_username} добавлен в группу {group_name}"

    elif command == "rename_group":
        if len(args) < 2:
            return "[CMD] Ошибка: укажите текущее и новое название группы"
        old_name = args[0]
        new_name = args[1]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, creator_id, allow_rename FROM groups WHERE name=?", (old_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Группа '{old_name}' не найдена"
        group_id, creator_id, allow_rename = row
        c.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))
        if not c.fetchone():
            conn.close()
            return "[CMD] Вы не состоите в этой группе"
        if user_id != creator_id and allow_rename == 0:
            conn.close()
            return "[CMD] Вы не можете переименовать группу (запрещено администратором)"
        c.execute("SELECT id FROM groups WHERE name=? AND id != ?", (new_name, group_id))
        if c.fetchone():
            conn.close()
            return f"[CMD] Группа с именем '{new_name}' уже существует"
        c.execute("UPDATE groups SET name=? WHERE id=?", (new_name, group_id))
        conn.commit()
        c.execute("SELECT user_id FROM group_members WHERE group_id=?", (group_id,))
        members = [row[0] for row in c.fetchall()]
        for member_id in members:
            c.execute("SELECT username FROM users WHERE id=?", (member_id,))
            member_name = c.fetchone()[0]
            if member_name in online_users:
                target_writer = online_users[member_name][0]
                target_writer.write(f"[CMD] GROUP_RENAMED {old_name}|{new_name}\n".encode())
                await target_writer.drain()
        conn.close()
        return f"[CMD] Группа переименована в '{new_name}'"

    elif command == "get_status":
        if len(args) < 1:
            return "[CMD] Укажите имя пользователя"
        target_user = args[0]
        is_online = target_user in online_users
        return f"[CMD] STATUS {target_user}|{'online' if is_online else 'offline'}"

    elif command == "get_group_members":
        if len(args) < 1:
            return "[CMD] Ошибка: укажите название группы"
        group_name = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, creator_id FROM groups WHERE name=?", (group_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Группа '{group_name}' не найдена"
        group_id, creator_id = row
        c.execute("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))
        if not c.fetchone():
            conn.close()
            return "[CMD] Вы не состоите в этой группе"
        c.execute('''SELECT u.username, u.id FROM group_members gm
                     JOIN users u ON gm.user_id = u.id
                     WHERE gm.group_id = ?''', (group_id,))
        members = c.fetchall()
        conn.close()
        members_list = []
        for username_, uid in members:
            if uid == creator_id:
                members_list.append(f"{username_}|Админ")
            else:
                members_list.append(f"{username_}|")
        return f"[CMD] Участники группы {group_name}: " + ";".join(members_list)

    elif command == "set_group_permissions":
        if len(args) < 3:
            return "[CMD] Ошибка: укажите название группы, права на переименование (allow/deny) и права на смену аватара (allow/deny)"
        group_name = args[0]
        rename_perm = args[1].lower()
        avatar_perm = args[2].lower()
        if rename_perm not in ['allow','deny'] or avatar_perm not in ['allow','deny']:
            return "[CMD] Используйте allow или deny"
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, creator_id FROM groups WHERE name=?", (group_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Группа '{group_name}' не найдена"
        group_id, creator_id = row
        if creator_id != user_id:
            conn.close()
            return "[CMD] Только создатель группы может изменять настройки"
        c.execute("UPDATE groups SET allow_rename=?, allow_change_avatar=? WHERE id=?",
                  (1 if rename_perm == 'allow' else 0, 1 if avatar_perm == 'allow' else 0, group_id))
        conn.commit()
        conn.close()
        return f"[CMD] Права группы обновлены: переименование - {rename_perm}, смена аватара - {avatar_perm}"

    elif command == "get_group_permissions":
        if len(args) < 1:
            return "[CMD] Укажите название группы"
        group_name = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT creator_id, allow_rename, allow_change_avatar FROM groups WHERE name=?", (group_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return f"[CMD] Группа '{group_name}' не найдена"
        creator_id, allow_rename, allow_change_avatar = row
        conn.close()
        is_creator = (creator_id == user_id)
        return f"[CMD] GROUP_PERMS {group_name}|{allow_rename}|{allow_change_avatar}|{is_creator}"

    elif command == "rename_device":
        if len(args) < 1:
            return "[CMD] Ошибка: укажите новое имя устройства"
        new_name = args[0]
        if len(new_name) > 50:
            return "[CMD] Имя устройства слишком длинное (макс. 50 символов)"
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE sessions SET device_name=? WHERE session_token=?", (new_name, session_token))
        if c.rowcount == 0:
            conn.close()
            return "[CMD] Сессия не найдена"
        conn.commit()
        conn.close()
        # Обновляем имя в online_users (только для отображения, не критично)
        if username in online_users:
            online_users[username] = (writer, user_id, session_token)
        return f"[CMD] Устройство переименовано в '{new_name}'"
    # ---------- Управление сессиями ----------
    elif command == "get_sessions":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''SELECT session_token, device_name, ip, created_at, last_activity, 
                            CASE WHEN session_token = ? THEN 1 ELSE 0 END as is_current
                     FROM sessions WHERE user_id = ?
                     ORDER BY is_current DESC, last_activity DESC''', (session_token, user_id))
        rows = c.fetchall()
        conn.close()
        sessions = []
        for token, device, ip, created, last_active, is_current in rows:
            sessions.append(f"{token}|{device}|{ip}|{created}|{last_active}|{is_current}")
        return "[CMD] SESSIONS " + ";".join(sessions)

    elif command == "terminate_session":
        if len(args) < 1:
            return "[CMD] Укажите токен сессии"
        target_token = args[0]
        if target_token == session_token:
            return "[CMD] Нельзя завершить текущую сессию (выйдите из аккаунта вместо этого)"
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE session_token=? AND user_id=?", (target_token, user_id))
        if c.rowcount == 0:
            conn.close()
            return "[CMD] Сессия не найдена"
        conn.commit()
        conn.close()
        # Если устройство онлайн, отключаем его
        for uname, (w, uid, tok) in online_users.items():
            if uid == user_id and tok == target_token:
                try:
                    w.write(f"[CMD] SESSION_TERMINATED\n".encode())
                    w.close()
                except:
                    pass
                online_users.pop(uname, None)
                break
        return "[CMD] Сессия завершена"

    elif command == "link_google":
        if len(args) < 1:
            return "[CMD] Ошибка: не указан email Google"
        google_email = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        # Проверяем, не привязан ли этот email к другому аккаунту
        c.execute("SELECT id FROM users WHERE google_email=? AND id != ?", (google_email, user_id))
        if c.fetchone():
            conn.close()
            return "[CMD] Этот Google-аккаунт уже привязан к другому пользователю"
        c.execute("UPDATE users SET google_email=? WHERE id=?", (google_email, user_id))
        conn.commit()
        conn.close()
        return "[CMD] Google-аккаунт успешно привязан"

    elif command == "bind_google":
        if len(args) < 1:
            return "[CMD] Ошибка: не указан email"
        email = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE users SET google_email=? WHERE id=?", (email, user_id))
        conn.commit()
        conn.close()
        return "[CMD] Google аккаунт привязан!"
    elif command == "google_login":
        print(f"DEBUG: google_login called with args={args}")
        if len(args) < 1:
            writer.write("[CMD] Ошибка: не указан email\n".encode())
            await writer.drain()
            return
        email = args[0]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        # Ищем пользователя по google_email или по username (если email совпадает с логином)
        c.execute("SELECT id, username FROM users WHERE google_email=? OR username=?", (email, email))
        row = c.fetchone()
        if not row:
            # Автоматическая регистрация
            random_pass = hashlib.sha256(os.urandom(32)).hexdigest()
            c.execute("INSERT INTO users (username, password, google_email) VALUES (?, ?, ?)", (email, random_pass, email))
            user_id = c.lastrowid
            username = email
            conn.commit()
        else:
            user_id, username = row
        conn.close()
        # Аутентифицируем пользователя
        authenticated = True
        online_users[username] = (writer, user_id)
        writer.write(f"[CMD] Добро пожаловать, {username}!\n".encode())
        await writer.drain()
        await deliver_offline_messages(writer, username, user_id)
        return  # Не отправляем дополнительный ответ
    elif command == "check_google_binding":
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT google_email FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return "[CMD] GOOGLE_BOUND True"
        else:
            return "[CMD] GOOGLE_BOUND False"
    elif command == "google_login":
        email = args[0]
        # ... поиск/создание пользователя ...
        authenticated = True
        online_users[username] = (writer, user_id)
        writer.write(f"[CMD] Добро пожаловать, {username}!\n".encode())
        await writer.drain()
        await deliver_offline_messages(writer, username, user_id)
        return   # важно: не отправлять больше ничего
    else:
        return "[CMD] Неизвестная команда"

async def deliver_offline_messages(writer, username, user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''SELECT u.username, m.message, m.id, m.timestamp 
                 FROM messages m
                 JOIN users u ON m.from_user_id = u.id
                 WHERE m.to_user_id = ? AND m.delivered = 0
                 AND NOT EXISTS (SELECT 1 FROM hidden_messages hm WHERE hm.user_id=? AND hm.message_id=m.id)''', (user_id, user_id))
    personal = c.fetchall()
    c.execute('''SELECT g.name, u.username, m.message, m.id, m.timestamp
                 FROM messages m
                 JOIN groups g ON m.to_group_id = g.id
                 JOIN users u ON m.from_user_id = u.id
                 JOIN group_members gm ON g.id = gm.group_id
                 WHERE gm.user_id = ? AND m.delivered = 0
                 AND NOT EXISTS (SELECT 1 FROM hidden_messages hm WHERE hm.user_id=? AND hm.message_id=m.id)''', (user_id, user_id))
    group_msgs = c.fetchall()
    for msg in personal:
        sender, text, msg_id, ts = msg
        writer.write(f"[MSG] {sender} (личное): {text}\n".encode())
        await writer.drain()
    for msg in group_msgs:
        group, sender, text, msg_id, ts = msg
        writer.write(f"[MSG] [ГРУППА {group}] {sender}: {text}\n".encode())
        await writer.drain()
    c.execute("UPDATE messages SET delivered = 1 WHERE (to_user_id = ? OR to_group_id IN (SELECT group_id FROM group_members WHERE user_id=?)) AND delivered = 0", (user_id, user_id))
    conn.commit()
    conn.close()

async def handle_client(reader, writer):
    addr = writer.get_extra_info('peername')
    print(f"Новое подключение {addr}")
    username = None
    user_id = None
    authenticated = False
    session_token = None
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            message = data.decode().strip()
            if not message:
                continue
            if not message.startswith('/'):
                writer.write("[CMD] Неизвестная команда. Используйте /help\n".encode())
                await writer.drain()
                continue
            parts = message.split()
            cmd = parts[0][1:]
            args = parts[1:]

            if cmd == "register":
                if len(args) != 2:
                    writer.write("[CMD] Использование: /register username password\n".encode())
                    await writer.drain()
                    continue
                reg_username, reg_password = args
                hashed = hash_password(reg_password)
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                try:
                    c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (reg_username, hashed))
                    conn.commit()
                    writer.write(f"[CMD] Пользователь {reg_username} успешно зарегистрирован\n".encode())
                except sqlite3.IntegrityError:
                    writer.write("[CMD] Имя пользователя уже занято\n".encode())
                conn.close()
                await writer.drain()

            elif cmd == "login":
                if authenticated:
                    writer.write("[CMD] Вы уже вошли\n".encode())
                    await writer.drain()
                    continue
                if len(args) < 2:
                    writer.write("[CMD] Использование: /login username password [device_name]\n".encode())
                    await writer.drain()
                    continue
                login_username = args[0]
                login_password = args[1]
                device_name = args[2] if len(args) > 2 else "Неизвестное устройство"
                hashed = hash_password(login_password)
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("SELECT id FROM users WHERE username=? AND password=?", (login_username, hashed))
                row = c.fetchone()
                if not row:
                    writer.write("[CMD] Неверное имя пользователя или пароль\n".encode())
                    await writer.drain()
                    conn.close()
                    continue
                user_id = row[0]
                # Генерируем токен
                token = generate_session_token()
                ip = addr[0]
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("INSERT INTO sessions (user_id, session_token, device_name, ip, created_at, last_activity, is_current) VALUES (?, ?, ?, ?, ?, ?, 1)",
                          (user_id, token, device_name, ip, now, now))
                # Сбрасываем флаг is_current для старых сессий этого пользователя (если нужно хранить только одну активную? оставляем как есть)
                conn.commit()
                conn.close()
                authenticated = True
                username = login_username
                session_token = token
                online_users[username] = (writer, user_id, session_token)
                writer.write(f"[CMD] Добро пожаловать, {username}!\n".encode())
                await writer.drain()
                await deliver_offline_messages(writer, username, user_id)

            elif cmd == "logout":
                if authenticated:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute("DELETE FROM sessions WHERE session_token=?", (session_token,))
                    conn.commit()
                    conn.close()
                    writer.write("[CMD] Вы вышли из системы\n".encode())
                    await writer.drain()
                    online_users.pop(username, None)
                    authenticated = False
                    username = None
                    user_id = None
                    session_token = None
                else:
                    writer.write("[CMD] Вы не авторизованы\n".encode())
                    await writer.drain()

            elif cmd == "quit":
                writer.write("[CMD] До свидания!\n".encode())
                await writer.drain()
                break

            elif cmd == "help":
                help_text = """
[CMD] Доступные команды:
/register username password
/login username password [device_name]
/logout
/add_contact username
/create_group group_name member1 member2 ...
/send recipient message
/delete_message message_id self|all
/history user_or_group [limit]
/contacts
/groups
/add_to_group group_name username
/rename_group old_name new_name
/set_avatar_start total_chunks
/set_avatar_chunk index chunk_data
/get_avatar username
/set_group_avatar_start group_name total_chunks
/set_group_avatar_chunk index chunk_data
/get_group_avatar group_name
/get_status username
/get_group_members group_name
/set_group_permissions group_name allow/deny allow/deny
/get_group_permissions group_name
/get_sessions
/terminate_session session_token
/quit
"""
                writer.write(help_text.encode())
                await writer.drain()

            else:
                if not authenticated:
                    writer.write("[CMD] Сначала войдите: /login username password\n".encode())
                    await writer.drain()
                    continue
                result = await handle_command(reader, writer, username, cmd, args, user_id, session_token)
                if result:
                    writer.write((result + "\n").encode())
                    await writer.drain()

    except Exception as e:
        print(f"Ошибка в обработке клиента {addr}: {e}")
    finally:
        if authenticated and username:
            # Удаляем сессию из БД при отключении
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("DELETE FROM sessions WHERE session_token=?", (session_token,))
            conn.commit()
            conn.close()
            online_users.pop(username, None)
        writer.close()
        await writer.wait_closed()
        print(f"Отключение {addr}")

async def main():
    init_db()
    try:
        server = await asyncio.start_server(handle_client, '0.0.0.0', 8888)
        print("Сервер запущен на 127.0.0.1:8888")
        async with server:
            await server.serve_forever()
    except OSError as e:
        print(f"Ошибка при запуске сервера: {e}")
        print("Возможно, порт 8888 уже занят. Закройте другой экземпляр сервера и повторите попытку.")
        input("Нажмите Enter для выхода...")

if __name__ == "__main__":
    asyncio.run(main())