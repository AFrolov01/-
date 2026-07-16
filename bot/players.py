# -*- coding: utf-8 -*-
"""
Игроки (отдельно от кланов) — хранятся в chat["players"][str(user_id)], где
chat — состояние ОДНОЙ конкретной группы (bot/chat_state.py). Своя экономика
Те/банк/достижения в каждой группе. Параметр функций ниже называется `db` по
историческим причинам, но по факту принимает именно `chat`.

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
      "noob_dice_rounds": int,       # "Кубик-нубика" — осталось раундов с порталом
      "grapes_rounds": int,          # "Сливы, виноград" — осталось раундов эффекта
  },
  "purchases_count": int,
  "bank": {"balance": float, "deposited_at": float | None},
}
"""

import time
from typing import Optional

import config

RECENT_WINDOW_SECONDS = 30 * 86400

_SHOP_DEFAULTS = {
    "avoid_punishment": 0, "next_win_boost": False, "next_loss_forgiven": False,
    "noob_dice_rounds": 0, "grapes_rounds": 0,
}


def _ensure_player_fields(player: dict) -> dict:
    shop = player.setdefault("shop", {})
    for key, default in _SHOP_DEFAULTS.items():
        shop.setdefault(key, default)
    bank = player.setdefault("bank", {"balance": 0.0, "deposited_at": None, "total_interest_earned": 0.0})
    bank.setdefault("total_interest_earned", 0.0)
    return player


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
            "shop": dict(_SHOP_DEFAULTS),
            "purchases_count": 0,
            "bank": {"balance": 0.0, "deposited_at": None, "total_interest_earned": 0.0},
        }
        players[uid] = player
    else:
        # обновим отображаемое имя на случай смены ника
        if username:
            player["username"] = username
        if first_name:
            player["first_name"] = first_name
        _ensure_player_fields(player)
    return player


def find_player(db: dict, user_id: int) -> Optional[dict]:
    player = db.get("players", {}).get(str(user_id))
    if player:
        _ensure_player_fields(player)
    return player


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


def tick_temporary_items(player: dict, portal_used_this_round: bool) -> list:
    """Вызывается ОДИН РАЗ после каждого завершённого раунда (победа/поражение —
    не важно). Списывает по 1 использованию с временных предметов, если они
    активны, и возвращает список строк для показа игроку."""
    notes = []
    shop = player.setdefault("shop", {})

    dice_left = shop.get("noob_dice_rounds", 0)
    if dice_left > 0:
        dice_left -= 1
        shop["noob_dice_rounds"] = dice_left
        if portal_used_this_round:
            notes.append(f"🎲 «Кубик-нубика»: осталось раундов — {dice_left}")
        else:
            notes.append(f"🎲 «Кубик-нубика»: портал не найден, сгорает попытка — осталось раундов {dice_left}")

    grapes_left = shop.get("grapes_rounds", 0)
    if grapes_left > 0:
        grapes_left -= 1
        shop["grapes_rounds"] = grapes_left
        notes.append(f"🍇 «Сливы, виноград»: осталось использований — {grapes_left}")

    return notes


def portals_count_for(clan: dict, player: dict) -> int:
    """Сколько клеток-порталов должно быть на поле игрока в этом раунде."""
    count = 0
    if clan and clan.get("tactic") == "barrel":
        count += 1
    if player.get("shop", {}).get("noob_dice_rounds", 0) > 0:
        count += 1
    return count


# ------------------------------------------------------------------ банк ---

def bank_current_balance(player: dict) -> float:
    """Текущий баланс вклада с учётом сложных процентов, накопленных со дня внесения."""
    bank = player.get("bank", {"balance": 0.0, "deposited_at": None})
    balance = bank.get("balance", 0.0)
    deposited_at = bank.get("deposited_at")
    if not balance or not deposited_at:
        return round(balance, 2)
    days_passed = (time.time() - deposited_at) / 86400
    grown = balance * ((1 + config.BANK_DAILY_RATE) ** days_passed)
    return round(grown, 2)


def bank_total_interest_earned(player: dict) -> float:
    """Сколько ВСЕГО процентов накапало с самого первого вклада (не сбрасывается
    при пополнении/снятии — в отличие от текущего баланса, это честный счётчик
    "сколько банк вам заработал" за всё время)."""
    bank = player.get("bank", {})
    settled = bank.get("total_interest_earned", 0.0)
    live_growth = bank_current_balance(player) - bank.get("balance", 0.0)
    return round(settled + live_growth, 2)


def bank_settle(player: dict) -> None:
    """Фиксирует накопленный за текущий период процент в общий счётчик
    total_interest_earned и обновляет 'принцип' (balance) до текущей
    выросшей суммы — вызывать ПЕРЕД любым изменением суммы вклада
    (пополнение/снятие), чтобы проценты не терялись и не обнулялись в
    отображении."""
    bank = player.setdefault("bank", {"balance": 0.0, "deposited_at": None, "total_interest_earned": 0.0})
    current_balance = bank_current_balance(player)
    growth = current_balance - bank.get("balance", 0.0)
    bank["total_interest_earned"] = round(bank.get("total_interest_earned", 0.0) + growth, 2)
    bank["balance"] = current_balance


def bank_days_left_to_withdraw(player: dict) -> float:
    bank = player.get("bank", {})
    deposited_at = bank.get("deposited_at")
    if not deposited_at:
        return 0.0
    elapsed_days = (time.time() - deposited_at) / 86400
    return max(0.0, config.BANK_MIN_HOLD_DAYS - elapsed_days)


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
