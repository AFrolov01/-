# -*- coding: utf-8 -*-
"""Система уровней клана: опыт (ОП) -> уровень -> титул + значок + прогресс-бар."""

import config


def level_from_xp(xp: float):
    """Возвращает (уровень, ОП внутри текущего уровня, ОП нужно для след. уровня)."""
    level = 1
    remaining = xp
    while remaining >= config.xp_required_for_level(level):
        remaining -= config.xp_required_for_level(level)
        level += 1
    needed = config.xp_required_for_level(level)
    return level, remaining, needed


def title_for_level(level: int) -> str:
    title = config.LEVEL_TITLES[0][1]
    for threshold, name in config.LEVEL_TITLES:
        if level >= threshold:
            title = name
        else:
            break
    return title


def badge_for_level(level: int) -> str:
    badge = config.LEVEL_BADGES[0][1]
    for threshold, emoji in config.LEVEL_BADGES:
        if level >= threshold:
            badge = emoji
        else:
            break
    return badge


def progress_bar(current: float, needed: float, segments: int = 8) -> str:
    if needed <= 0:
        filled = segments
    else:
        filled = round((current / needed) * segments)
    filled = max(0, min(segments, filled))
    return "🟩" * filled + "⬜" * (segments - filled)


def clan_level_block(clan: dict) -> str:
    """Полный блок вывода уровня клана: полоса + числа + уровень."""
    xp = clan.get("xp", 0)
    level, current, needed = level_from_xp(xp)
    bar = progress_bar(current, needed)
    return f"{bar}  {round(current)}/{needed}  {level} уровень"


def clan_prefix(clan: dict) -> str:
    """Значок + титул для отображения рядом с названием клана."""
    xp = clan.get("xp", 0)
    level, _, _ = level_from_xp(xp)
    badge = badge_for_level(level)
    title = title_for_level(level)
    return f"{badge} {title}"


def apply_level_up_reputation(clan: dict, old_level: int, new_level: int) -> int:
    """При повышении уровня начисляет репутацию за каждый пройденный уровень
    (с учётом множителя репутации от достижений клана). Возвращает сколько начислено."""
    if new_level <= old_level:
        return 0
    mult = clan.get("reputation_multiplier", 1.0)
    total_gain = 0
    for lvl in range(old_level + 1, new_level + 1):
        gain = round(config.LEVEL_UP_REP_MULT * lvl * mult)
        total_gain += gain
    clan["reputation"] = max(0, min(config.MAX_REPUTATION, clan.get("reputation", 0) + total_gain))
    return total_gain
