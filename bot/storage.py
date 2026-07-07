# -*- coding: utf-8 -*-
"""
Простое персистентное хранилище на JSON-файле.
Один asyncio.Lock на весь файл — этого достаточно для нагрузки одной группы.

Структура данных:
{
  "next_clan_id": int,
  "next_duel_id": int,
  "group_chat_id": int | null,          # чат, куда бот шлёт объявления о дуэлях
  "clans": {
      "<clan_id>": {
          "id": int,
          "name": str,
          "motto": str,
          "avatar_file_id": str | null,
          "creator_id": int,
          "points": float,
          "max_win_streak": int,
          "current_win_streak": int,
          "wars_won": int,
          "best_single_multiplier": {"value": float, "user_id": int, "username": str} | null,
          "members": {
              "<user_id>": {
                  "user_id": int,
                  "username": str,
                  "first_name": str,
                  "matches_played": int,
                  "last_played_at": float (timestamp)
              }, ...
          },
          "last_played_at": float
      }, ...
  },
  "pending_invite": {                    # текущий разосланный вызов на дуэль (ожидает /minduel)
      "clan_a_id": int, "player_a_id": int,
      "clan_b_id": int, "player_b_id": int,
      "created_at": float
  } | null,
  "active_duels": {
      "<duel_id>": { ... см. bot/game.py ... }
  }
}
"""

import json
import os
import asyncio
import time
from typing import Any, Dict, Optional

from config import DATA_FILE

_lock = asyncio.Lock()

_DEFAULT_DB: Dict[str, Any] = {
    "next_clan_id": 1,
    "next_duel_id": 1,
    "group_chat_id": None,
    "clans": {},
    "pending_invite": None,
    "active_duels": {},
    "season_started_at": None,
    "last_convergence_at": None,
    "reputation_reset_year": None,
    "players": {},
}


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
        except json.JSONDecodeError:
            data = dict(_DEFAULT_DB)
    # подстрахуемся от отсутствующих ключей (например, после обновления схемы)
    for key, value in _DEFAULT_DB.items():
        data.setdefault(key, value)
    return data


def _write(data: Dict[str, Any]) -> None:
    tmp_path = DATA_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, DATA_FILE)


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
