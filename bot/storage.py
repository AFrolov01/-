# -*- coding: utf-8 -*-
"""
Простое персистентное хранилище на JSON-файле.
Один asyncio.Lock на весь файл.

Структура данных (мультигрупповая):
{
  "chats": {
      "<chat_id>": {                      # своя независимая война для каждой группы
          "next_clan_id": int, "next_duel_id": int,
          "clans": {...}, "pending_invite": {...} | null,
          "active_duels": {...}, "players": {...},
          "season_started_at": float | null, ...
          "title": str | null              # название группы (для общемирового топа)
      }, ...
  },
  "ad": {                                  # реклама владельца — рассылается во ВСЕ группы
      "text": str | null, "photo_file_id": str | null,
      "interval_days": float | null, "last_sent_at": float | null
  }
}

Подробности структуры одной "войны" (chat) — см. bot/chat_state.py.
"""

import json
import os
import asyncio
import logging
import time
from typing import Any, Dict, Optional

from config import DATA_FILE

logger = logging.getLogger("storage")
_lock = asyncio.Lock()

_DEFAULT_DB: Dict[str, Any] = {
    "chats": {},
    "ad": {"text": None, "photo_file_id": None, "interval_days": None, "last_sent_at": None},
    # --- СЕЗОН ОБЩИЙ ДЛЯ ВСЕХ ГРУПП (таймер один, а места/ранги в каждой группе свои) ---
    "season_started_at": None,
    "last_weekly_modifier_at": None,
    "reputation_reset_year": None,
}

# Поля старой (одногрупповой) схемы — если они есть на верхнем уровне, значит
# файл ещё не мигрирован на мультигрупповую структуру.
_LEGACY_TOP_LEVEL_KEYS = [
    "next_clan_id", "next_duel_id", "clans", "pending_invite", "active_duels",
    "players", "pending_skip_notes", "group_chat_id", "matchmaking_alternate_leader",
    "active_votes",
]


def _migrate_legacy_if_needed(data: Dict[str, Any]) -> Dict[str, Any]:
    """Старые версии бота хранили ОДНУ общую войну прямо в корне файла.
    Если такие поля обнаружены — переносим их в chats[<group_chat_id>], чтобы
    не потерять существующие кланы/очки/игроков при обновлении."""
    if "chats" in data and any(k in data for k in _LEGACY_TOP_LEVEL_KEYS) is False:
        return data
    if "clans" not in data and "players" not in data:
        return data  # нечего мигрировать (либо уже новый пустой файл)

    legacy_chat_id = data.get("group_chat_id") or "legacy"
    chat_state = {
        "next_clan_id": data.get("next_clan_id", 1),
        "next_duel_id": data.get("next_duel_id", 1),
        "clans": data.get("clans", {}),
        # ВАЖНО: pending_invite/active_duels НЕ переносим — это временное
        # состояние текущей игры, а не ценные данные. Перенос старых
        # "зависших" дуэлей (например, из версий бота с давно исправленным
        # багом) приводил к тому, что игроки навсегда считались "занятыми"
        # и новые дуэли переставали объявляться.
        "pending_invite": None,
        "active_duels": {},
        "players": data.get("players", {}),
        "pending_skip_notes": [],
        "matchmaking_alternate_leader": data.get("matchmaking_alternate_leader", False),
        "active_votes": {},
        "next_duel_due_at": None,
        "title": None,
    }
    chats = data.setdefault("chats", {})
    chats[str(legacy_chat_id)] = chat_state
    # сезонные поля (season_started_at и т.п.) теперь ОБЩИЕ — просто остаются
    # на верхнем уровне data, их не трогаем и не удаляем ниже
    for key in _LEGACY_TOP_LEVEL_KEYS:
        data.pop(key, None)
    logger.info("Мигрировал старую одногрупповую БД в chats['%s']", legacy_chat_id)
    return data


def _ensure_file() -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_DB, f, ensure_ascii=False, indent=2)


def _read() -> Dict[str, Any]:
    _ensure_file()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(
                "ФАЙЛ БАЗЫ ДАННЫХ ПОВРЕЖДЁН (%s) — %s. Сбрасываю в состояние по "
                "умолчанию (кланы будут потеряны!). Проверьте, не запущено ли "
                "два экземпляра бота одновременно с одним и тем же файлом.",
                DATA_FILE, e,
            )
            data = dict(_DEFAULT_DB)
    data = _migrate_legacy_if_needed(data)
    # подстрахуемся от отсутствующих ключей (например, после обновления схемы)
    for key, value in _DEFAULT_DB.items():
        data.setdefault(key, value.copy() if isinstance(value, (dict, list)) else value)
    return data


def _write(data: Dict[str, Any]) -> None:
    tmp_path = DATA_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DATA_FILE)
    except Exception as e:
        logger.error("НЕ УДАЛОСЬ записать файл базы данных %s: %s", DATA_FILE, e)
        raise


class Storage:
    """Асинхронная обёртка над JSON-файлом. Использовать через `async with Storage() as db:`"""

    def __init__(self) -> None:
        self._data: Optional[Dict[str, Any]] = None

    async def __aenter__(self) -> Dict[str, Any]:
        await _lock.acquire()
        self._data = _read()
        return self._data

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None and self._data is not None:
                _write(self._data)
        finally:
            _lock.release()


async def read_only() -> Dict[str, Any]:
    """Быстрое чтение без намерения писать (всё равно берём лок, чтобы не словить гонку записи)."""
    async with _lock:
        return _read()


def now() -> float:
    return time.time()
