# -*- coding: utf-8 -*-
"""
Игроки (отдельно от кланов) — хранятся в db["players"][str(user_id)].

Структура:
{
  "user_id": int, "username": str, "first_name": str,
  "currency": float,                       # валюта "Те"
  "best_multiplier": float,
  "total_rounds": int, "wins": int, "losses": int,
  "all_time_mult_sum": float, "all_time_mult_count": int,
  "recent_multipliers": [[timestamp, value], ...],   # для среднего за 30 дней
  "achievements": [key, ...],
  "shop": {
      "avoid_punishment": int,       # сколько раз доступно
      "next_win_boost": bool,
      "next_loss_forgiven": bool,
  },
  "purchases_count": int,
}
"""

import time
from typing import Optional

import config

RECENT_WINDOW_SECONDS = 30 * 86400


def get_or_create_player(db: dict, user_id: int, username: str = "", first_name: str = "Игрок") -> dict:
    uid = str(user_id)
    players = db.setdefault("players", {})
    player = players.get(uid)
    if not player:
        player = {
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name or "Игрок",
            "currency": 0.0,
            "best_multiplier": 0.0,
            "total_rounds": 0, "wins": 0, "losses": 0,
            "all_time_mult_sum": 0.0, "all_time_mult_count": 0,
            "recent_multipliers": [],
            "achievements": [],
            "shop": {"avoid_punishment": 0, "next_win_boost": False, "next_loss_forgiven": False},
            "purchases_count": 0,
        }
        players[uid] = player
    else:
        # обновим отображаемое имя на случай смены ника
        if username:
            player["username"] = username
        if first_name:
            player["first_name"] = first_name
    return player


def find_player(db: dict, user_id: int) -> Optional[dict]:
    return db.get("players", {}).get(str(user_id))


def record_round_result(player: dict, won: bool, multiplier: float, currency_gain: Optional[float] = None) -> list:
    """Обновляет статистику раунда, начисляет Те при победе. `multiplier` — это
    ЧИСТЫЙ х множитель раунда (для статистики/ачивок), `currency_gain` —
    сколько Те реально начислить (если тактика клана его меняет; по умолчанию
    равен multiplier). Возвращает список НОВЫХ достижений (ключи)."""
    new_achievements = []
    player["total_rounds"] = player.get("total_rounds", 0) + 1

    if won:
        player["wins"] = player.get("wins", 0) + 1
        gain = multiplier if currency_gain is None else currency_gain
        player["currency"] = round(player.get("currency", 0.0) + gain, 2)
        player["best_multiplier"] = max(player.get("best_multiplier", 0.0), multiplier)

        player["all_time_mult_sum"] = player.get("all_time_mult_sum", 0.0) + multiplier
        player["all_time_mult_count"] = player.get("all_time_mult_count", 0) + 1
        recent = player.setdefault("recent_multipliers", [])
        recent.append([time.time(), multiplier])
        cutoff = time.time() - RECENT_WINDOW_SECONDS
        player["recent_multipliers"] = [r for r in recent if r[0] >= cutoff]

        if player["wins"] == 1 and "pervye_shagi" not in player["achievements"]:
            player["achievements"].append("pervye_shagi")
            new_achievements.append("pervye_shagi")
        if multiplier >= 7 and "za_granyu" not in player["achievements"]:
            player["achievements"].append("za_granyu")
            new_achievements.append("za_granyu")
        elif multiplier >= 5 and "kak_eto" not in player["achievements"]:
            player["achievements"].append("kak_eto")
            new_achievements.append("kak_eto")
    else:
        player["losses"] = player.get("losses", 0) + 1

    return new_achievements


def average_multiplier_all_time(player: dict) -> float:
    count = player.get("all_time_mult_count", 0)
    if not count:
        return 0.0
    return round(player.get("all_time_mult_sum", 0.0) / count, 2)


def average_multiplier_30d(player: dict) -> float:
    recent = player.get("recent_multipliers", [])
    cutoff = time.time() - RECENT_WINDOW_SECONDS
    values = [r[1] for r in recent if r[0] >= cutoff]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def win_rate(player: dict) -> float:
    total = player.get("total_rounds", 0)
    if not total:
        return 0.0
    return round(player.get("wins", 0) / total * 100, 1)


def display_name(player: dict) -> str:
    return f'@{player["username"]}' if player.get("username") else player.get("first_name", "Игрок")


def unlock_achievement(player: dict, key: str) -> bool:
    """Разблокирует достижение, если его ещё не было. Возвращает True если новое."""
    if key not in player.get("achievements", []):
        player.setdefault("achievements", []).append(key)
        return True
    return False


def achievements_text(player: dict) -> str:
    keys = player.get("achievements", [])
    if not keys:
        return "пока нет"
    parts = []
    for key in keys:
        info = config.PLAYER_ACHIEVEMENTS.get(key)
        if info:
            parts.append(f'{info["emoji"]} {info["name"]}')
    return ", ".join(parts) if parts else "пока нет"
