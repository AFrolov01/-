# -*- coding: utf-8 -*-
"""Репутация клана: 0..MAX_REPUTATION, с прогресс-баром на 10 делений."""

import config


def add_reputation(clan: dict, amount: float, apply_multiplier: bool = True) -> int:
    """Добавляет репутацию клану (может быть отрицательной), учитывая множитель
    от достижений клана (если apply_multiplier=True — не применяется к очкам,
    полученным НАПРЯМУЮ за ачивки, только к очкам за места в сезоне и уровни)."""
    mult = clan.get("reputation_multiplier", 1.0) if apply_multiplier else 1.0
    gain = round(amount * mult)
    clan["reputation"] = max(0, min(config.MAX_REPUTATION, clan.get("reputation", 0) + gain))
    return gain


def reputation_bar(clan: dict, segments: int = 10) -> str:
    rep = clan.get("reputation", 0)
    filled = round((rep / config.MAX_REPUTATION) * segments)
    filled = max(0, min(segments, filled))
    return "█" * filled + "░" * (segments - filled)


def reputation_block(clan: dict) -> str:
    return f"👑 Репутация\n{reputation_bar(clan)} {clan.get('reputation', 0)}"
