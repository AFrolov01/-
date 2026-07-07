# -*- coding: utf-8 -*-
"""
Тактики сезона — выбирает создатель клана командой /tactic, действуют весь сезон.

Трактовки неочевидных формулировок ТЗ (см. также README):
 - "Тихо не спеша" — влияет ТОЛЬКО на еженедельное сжатие очков (см. bot/season.py),
   здесь не участвует.
 - "Мы уже красные" и "Да да нет нет да будет свет" по-разному считают штраф за
   подрыв на мине в зависимости от текущей серии ПОДРЯДных поражений/побед
   именно в отдельных раундах (не путать с "серией побед в дуэли" из /clan).
 - "Азарт" — эффект выпадает случайно ОДИН раз при старте раунда (когда игрок
   выбрал количество мин) и держится до конца этого раунда.
"""

import random

import config

GAMBLE_EFFECTS = {
    "fortuna": {"emoji": "🍀", "name": "Фортуна", "win_mult": 1.20, "loss_mult": None, "te_mult": 1.0},
    "bronya": {"emoji": "🛡", "name": "Броня", "win_mult": 1.0, "loss_mult": 0.85, "te_mult": 1.0},
    "zoloto": {"emoji": "💰", "name": "Золотая жила", "win_mult": 1.0, "loss_mult": None, "te_mult": 3.0},
    "bezumie": {"emoji": "💣", "name": "Безумие", "win_mult": 1.20, "loss_mult": 0.65, "te_mult": 1.0},
    "neudacha": {"emoji": "☠", "name": "Неудача", "win_mult": 0.80, "loss_mult": None, "te_mult": 1.0},
}


def roll_gamble_effect() -> str:
    return random.choice(list(GAMBLE_EFFECTS.keys()))


def effective_loss_multiplier(clan: dict, side: dict) -> float:
    """Множитель, применяемый к очкам клана при подрыве на мине (чем больше — тем мягче штраф)."""
    tactic = clan.get("tactic")
    if tactic == "red":
        base = config.LOSS_MULTIPLIER + 0.05 * clan.get("consecutive_losses", 0)
        return min(base, 0.95)
    if tactic == "streak":
        consecutive = clan.get("consecutive_losses", 0)
        return 0.70 if consecutive == 0 else 0.80
    if tactic == "gamble":
        effect = GAMBLE_EFFECTS.get(side.get("gamble_effect"))
        if effect and effect.get("loss_mult") is not None:
            return effect["loss_mult"]
    return config.LOSS_MULTIPLIER


def win_points_multiplier(clan: dict, side: dict, player: dict) -> float:
    """Дополнительный множитель к очкам клана при выигрыше (сверх x_множителя раунда)."""
    tactic = clan.get("tactic")
    mult = 1.0
    if tactic == "streak":
        wins_in_row = clan.get("tactic_consecutive_wins", 0)
        bonus = min(0.5, 0.10 * wins_in_row)
        mult *= (1 + bonus)
    elif tactic == "hamster":
        currency = player.get("currency", 0.0) if player else 0.0
        bonus = min(0.15, currency * 0.0001)
        mult *= (1 + bonus)
    if tactic == "gamble":
        effect = GAMBLE_EFFECTS.get(side.get("gamble_effect"))
        if effect:
            mult *= effect.get("win_mult", 1.0)
    return mult


def currency_multiplier(clan: dict, side: dict) -> float:
    """Множитель к заработанной валюте Те при выигрыше раунда."""
    tactic = clan.get("tactic")
    mult = 1.0
    if tactic == "hamster":
        mult *= 2.0
    if tactic == "gamble":
        effect = GAMBLE_EFFECTS.get(side.get("gamble_effect"))
        if effect:
            mult *= effect.get("te_mult", 1.0)
    return mult


def register_round_result(clan: dict, won: bool) -> None:
    """Обновляет серии побед/поражений НА УРОВНЕ ОТДЕЛЬНОГО РАУНДА (для тактик
    "мы уже красные" / "да да нет нет" — не путать с серией побед в дуэли)."""
    if won:
        clan["consecutive_losses"] = 0
        clan["tactic_consecutive_wins"] = clan.get("tactic_consecutive_wins", 0) + 1
    else:
        clan["consecutive_losses"] = clan.get("consecutive_losses", 0) + 1
        clan["tactic_consecutive_wins"] = 0
