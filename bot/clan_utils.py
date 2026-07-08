# -*- coding: utf-8 -*-
"""Гарантирует, что у клана есть все поля новых систем (уровни/репутация/тактики/
достижения) — нужно для кланов, созданных ДО этого обновления."""

_DEFAULTS = {
    "xp": 0,
    "reputation": 0,
    "reputation_multiplier": 1.0,
    "achievements": [],
    "seasons_played": 0,
    "medals": {"gold": 0, "silver": 0, "bronze": 0},
    "tactic": None,
    "tactic_locked": False,
    "consecutive_losses": 0,
    "tactic_consecutive_wins": 0,
    "queue": [],
    "carried_attempts": 1,
    "weekly_percent_modifier": 0,
}


def ensure_clan_fields(clan: dict) -> dict:
    for key, default in _DEFAULTS.items():
        if key not in clan:
            clan[key] = default.copy() if isinstance(default, dict) else default
    return clan
