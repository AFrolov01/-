# -*- coding: utf-8 -*-
"""
Сезон войны кланов.

По умолчанию сезон длится config.SEASON_LENGTH_DAYS (30) дней. Фоновая задача
`season_watcher_loop` раз в час проверяет, не истёк ли срок, и если да —
сама публикует итоги (топ + победитель) в боевой чат и начинает новый сезон
(очки/серии/победы кланов обнуляются, сами кланы и участники остаются).

Отсчёт сезона стартует с первого запуска бота (когда впервые сработает
проверка) либо сбрасывается вручную командой /resetwar.
"""

import asyncio

from aiogram import Bot

import config
from bot.storage import Storage, now
from bot.leaderboard import build_top_text


def finalize_season_locked(db: dict) -> str:
    """Подводит итоги сезона и сбрасывает статистику. Вызывать ТОЛЬКО внутри
    `async with Storage() as db:` — функция не открывает хранилище сама."""
    final_text = build_top_text(db, "🏁 <b>Сезон войны кланов завершён!</b>", declare_winner=True)
    for clan in db["clans"].values():
        clan["points"] = 1000
        clan["max_win_streak"] = 0
        clan["current_win_streak"] = 0
        clan["wars_won"] = 0
        clan["best_single_multiplier"] = None
    db["pending_invite"] = None
    db["active_duels"] = {}
    db["season_started_at"] = now()
    return final_text


async def _check_and_finalize(bot: Bot) -> None:
    final_text = None
    group_id = None

    async with Storage() as db:
        started = db.get("season_started_at")
        if started is None:
            # первый запуск — просто фиксируем старт отсчёта, ничего не сбрасываем
            db["season_started_at"] = now()
            return

        elapsed_days = (now() - started) / 86400
        if elapsed_days < config.SEASON_LENGTH_DAYS:
            return

        if not db["clans"]:
            # нечего подводить — просто начинаем отсчёт заново
            db["season_started_at"] = now()
            return

        group_id = db.get("group_chat_id")
        final_text = finalize_season_locked(db)

    if final_text and group_id:
        try:
            await bot.send_message(group_id, final_text + "\n\n🔄 Начинается новый сезон!", parse_mode="HTML")
        except Exception:
            pass


async def season_watcher_loop(bot: Bot) -> None:
    while True:
        try:
            await _check_and_finalize(bot)
        except Exception:
            pass
        await asyncio.sleep(config.SEASON_CHECK_INTERVAL_SECONDS)
