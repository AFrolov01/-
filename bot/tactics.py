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
    "fortuna": {
        "emoji": "🍀", "name": "Фортуна", "win_mult": 1.20, "loss_mult": None, "te_mult": 1.0,
        "desc": "+20% к очкам за победу в этом раунде",
    },
    "bronya": {
        "emoji": "🛡", "name": "Броня", "win_mult": 1.0, "loss_mult": 0.85, "te_mult": 1.0,
        "desc": "при поражении штраф всего x0.85 (вместо x0.75)",
    },
    "zoloto": {
        "emoji": "💰", "name": "Золотая жила", "win_mult": 1.0, "loss_mult": None, "te_mult": 3.0,
        "desc": "валюта Те за этот раунд x3",
    },
    "bezumie": {
        "emoji": "💣", "name": "Безумие", "win_mult": 1.20, "loss_mult": 0.65, "te_mult": 1.0,
        "desc": "+20% к очкам за победу, но при поражении штраф x0.65",
    },
    "neudacha": {
        "emoji": "☠", "name": "Неудача", "win_mult": 0.80, "loss_mult": None, "te_mult": 1.0,
        "desc": "−20% к очкам за победу в этом раунде",
    },
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


def weekly_modifier_fraction(clan: dict) -> float:
    """Текущий накопленный %-баф/дебафф клана в виде доли (например -0.20 = -20%)."""
    return clan.get("weekly_percent_modifier", 0) / 100


def weekly_modifier_multiplier(clan: dict) -> float:
    """Множитель к очкам раунда с учётом накопленного недельного бафа/дебаффа (не ниже 0)."""
    return max(0.0, 1 + weekly_modifier_fraction(clan))


def describe_tactic_bonus(clan: dict, side: dict, player: dict, applied_mult: float) -> str:
    """Текст про то, какой бонус дала тактика В ЭТОМ раунде и какой даст в
    СЛЕДУЮЩИЙ раз (только для тактик, где это предсказуемо: серия побед и
    хомяк — для "азарта" бонус каждый раз случайный, для остальных тактик
    win_points_multiplier не меняется от победы, показывать нечего)."""
    tactic = clan.get("tactic")
    if tactic not in ("streak", "hamster"):
        return ""

    import config
    tactic_name = config.SEASON_TACTICS.get(tactic, tactic)
    this_pct = round((applied_mult - 1) * 100)

    # предсказываем бонус следующей победы — состояние клана/игрока уже
    # обновлено этим раундом (register_round_result уже вызван к этому моменту)
    next_mult = win_points_multiplier(clan, side, player)
    next_pct = round((next_mult - 1) * 100)

    this_sign = "+" if this_pct >= 0 else ""
    next_sign = "+" if next_pct >= 0 else ""
    return (
        f"🎯 Тактика «{tactic_name}»: бонус в этом раунде {this_sign}{this_pct}%, "
        f"при следующей победе будет {next_sign}{next_pct}%"
    )


def register_round_result(clan: dict, won: bool) -> None:
    """Обновляет серии побед/поражений НА УРОВНЕ ОТДЕЛЬНОГО РАУНДА (для тактик
    "мы уже красные" / "да да нет нет" — не путать с серией побед в дуэли)."""
    if won:
        clan["consecutive_losses"] = 0
        clan["tactic_consecutive_wins"] = clan.get("tactic_consecutive_wins", 0) + 1
    else:
        clan["consecutive_losses"] = clan.get("consecutive_losses", 0) + 1
        clan["tactic_consecutive_wins"] = 0
