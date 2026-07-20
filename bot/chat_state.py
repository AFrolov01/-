# -*- coding: utf-8 -*-
"""
Мультигрупповая архитектура.

У КАЖДОЙ группы, где используется бот, — своя полностью независимая война:
db["chats"][str(chat_id)] содержит всё то, что раньше лежало прямо в db
(clans, players, active_duels, pending_invite, состояние сезона и т.д.).
Дуэли происходят ТОЛЬКО внутри своей группы.

Группа регистрируется автоматически при первом использовании любой игровой
команды в ней — вручную назначать ничего не нужно (команда /setgroup убрана).

Поверх локальных войн есть ДВА топа:
 - локальный (/top) — топ-25 кланов ЭТОЙ конкретной группы;
 - общемировой (/globaltop) — топ-25 кланов среди ВСЕХ групп, где есть бот.
"""

from typing import Optional


def _new_chat_state() -> dict:
    return {
        "next_clan_id": 1,
        "next_duel_id": 1,
        "clans": {},
        "pending_invite": None,
        "active_duels": {},
        "players": {},
        "pending_skip_notes": [],
        "matchmaking_alternate_leader": False,
        "active_votes": {},
        "next_duel_due_at": None,
        "title": None,  # человекочитаемое название группы (для общемирового топа)
        "silent_mode": False,  # /tixa — короткие уведомления, без длинных пояснений
    }


def get_chat(db: dict, chat_id: int) -> dict:
    """Возвращает (создавая при необходимости) состояние войны конкретной группы."""
    key = str(chat_id)
    chats = db.setdefault("chats", {})
    chat = chats.setdefault(key, _new_chat_state())
    # докатка недостающих полей (для групп, заведённых до обновления схемы)
    for k, v in _new_chat_state().items():
        chat.setdefault(k, v.copy() if isinstance(v, (dict, list)) else v)
    return chat


def all_chat_ids(db: dict) -> list:
    return [int(k) for k in db.get("chats", {}).keys()]


def total_groups(db: dict) -> int:
    return len(db.get("chats", {}))


def total_unique_players(db: dict) -> int:
    """Уникальные Telegram user_id среди ВСЕХ групп — и тех, кто уже играл
    (есть профиль в chat["players"]), и тех, кто просто состоит в клане, но
    ещё ни разу не участвовал в раунде/не пользовался /iam-/shop-/bank."""
    seen = set()
    for chat in db.get("chats", {}).values():
        seen.update(chat.get("players", {}).keys())
        for clan in chat.get("clans", {}).values():
            seen.update(clan.get("members", {}).keys())
    return len(seen)


def find_user_chats(db: dict, user_id: int) -> list:
    """Все chat_id, где этот пользователь состоит в каком-либо клане ИЛИ уже
    имеет игровой профиль (использовал бота в этой группе)."""
    uid = str(user_id)
    result = []
    for chat_id_str, chat in db.get("chats", {}).items():
        found = uid in chat.get("players", {})
        if not found:
            for clan in chat.get("clans", {}).values():
                if uid in clan.get("members", {}):
                    found = True
                    break
        if found:
            result.append(int(chat_id_str))
    return result


async def resolve_chat_for_message(message, db: dict) -> "tuple[Optional[int], Optional[str]]":
    """Определяет, к какой группе относится команда.
    - Если вызвано прямо в группе — используется эта группа (и она регистрируется,
      если это первое обращение).
    - Если вызвано в ЛС — ищем, в скольких группах пользователь уже играет:
      ровно в одной -> используем её; в нуле или нескольких -> просим вызвать
      команду прямо в нужной группе.
    Возвращает (chat_id, текст_ошибки). Если текст_ошибки не None — chat_id
    использовать нельзя, нужно просто показать этот текст пользователю."""
    if message.chat.type in ("group", "supergroup"):
        chat = get_chat(db, message.chat.id)
        if message.chat.title:
            chat["title"] = message.chat.title
        return message.chat.id, None

    chats = find_user_chats(db, message.from_user.id)
    if len(chats) == 1:
        return chats[0], None
    if len(chats) == 0:
        return None, (
            "Вы ещё не участвуете ни в одной войне кланов. Эту команду нужно "
            "вызвать прямо в группе с ботом."
        )
    return None, (
        "Вы состоите в кланах нескольких разных групп — эту команду нужно "
        "вызвать прямо в нужной группе, а не в личных сообщениях."
    )
