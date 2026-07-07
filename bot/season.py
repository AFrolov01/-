# -*- coding: utf-8 -*-
"""
Сезон войны кланов.

 - Сезон длится config.SEASON_LENGTH_DAYS (30) дней, завершается автоматически:
   итоги (топ + победитель) публикуются в боевой чат, очки/серии/победы/тактика
   сбрасываются, а УРОВЕНЬ/ОПЫТ/РЕПУТАЦИЯ/ДОСТИЖЕНИЯ/ИСТОРИЯ КЛАНА — НЕТ,
   они копятся из сезона в сезон.
 - Каждые 7 дней сезона очки всех кланов "сжимаются" к среднему на 10%
   (см. CONVERGENCE_FACTOR), кроме кланов с тактикой "Тихо не спеша" — для них
   действует не общая формула, а фиксированные -4% (если выше среднего) /
   +6% (если ниже среднего).
 - Репутация клана обнуляется в начале каждого календарного года (сам клан,
   уровень и достижения — нет, это отдельная система).
"""

import asyncio
import datetime

from aiogram import Bot

import config
from bot.storage import Storage, now
from bot.leaderboard import build_top_text
from bot.clan_utils import ensure_clan_fields
from bot.leveling import level_from_xp, apply_level_up_reputation
from bot.reputation import add_reputation
from bot import players


# ------------------------------------------------------- завершение сезона -

def _check_clan_achievements(clan: dict, rank: int, total_clans: int) -> list:
    """Проверяет и разблокирует ачивки клана по итогам сезона. Возвращает
    список новых ключей (для объявления в чате)."""
    new_keys = []
    points_at_end = clan.get("points", 0)
    achievements = clan.setdefault("achievements", [])

    def unlock(key: str) -> None:
        if key not in achievements:
            achievements.append(key)
            new_keys.append(key)
            rep_mult = config.CLAN_ACHIEVEMENTS[key].get("rep_mult", 1.0)
            if rep_mult != 1.0:
                clan["reputation_multiplier"] = round(clan.get("reputation_multiplier", 1.0) * rep_mult, 4)

    if points_at_end >= 20000:
        unlock("bogatyri")
    if points_at_end >= 10000:
        unlock("malo_bl")
    if rank == 1:
        unlock("ultrovye_kotiki")
    if rank == 2:
        unlock("eshe_posidim")
    if clan.get("reputation", 0) >= config.MAX_REPUTATION:
        unlock("bazara_net")

    return new_keys


def finalize_season_locked(db: dict) -> str:
    """Подводит итоги сезона: опыт/уровни/репутация/медали/достижения кланов,
    затем сбрасывает очки/серии/тактики сезона. Вызывать ТОЛЬКО внутри
    `async with Storage() as db:`."""
    clans = list(db["clans"].values())
    for clan in clans:
        ensure_clan_fields(clan)

    final_text = build_top_text(db, "🏁 <b>Сезон войны кланов завершён!</b>", declare_winner=True)

    clans_sorted = sorted(clans, key=lambda c: c.get("points", 0), reverse=True)
    total = len(clans_sorted)
    achievement_lines = []

    for rank, clan in enumerate(clans_sorted, start=1):
        # --- репутация за место в сезоне ---
        if total == 1:
            placement_rep = config.REPUTATION_PLACEMENT_1ST
        elif rank == total:
            placement_rep = config.REPUTATION_PLACEMENT_LAST
        elif rank == 1:
            placement_rep = config.REPUTATION_PLACEMENT_1ST
        elif rank == 2:
            placement_rep = config.REPUTATION_PLACEMENT_2ND
        else:
            placement_rep = config.REPUTATION_PLACEMENT_OTHER
        add_reputation(clan, placement_rep, apply_multiplier=True)

        # --- опыт и уровень ---
        xp_gain = round(clan.get("points", 0) * config.XP_PER_POINT, 2)
        old_level, _, _ = level_from_xp(clan.get("xp", 0))
        clan["xp"] = clan.get("xp", 0) + xp_gain
        new_level, _, _ = level_from_xp(clan["xp"])
        apply_level_up_reputation(clan, old_level, new_level)

        # --- медали и история ---
        clan["seasons_played"] = clan.get("seasons_played", 0) + 1
        medals = clan.setdefault("medals", {"gold": 0, "silver": 0, "bronze": 0})
        if rank == 1:
            medals["gold"] += 1
        elif rank == 2:
            medals["silver"] += 1
        elif rank == 3:
            medals["bronze"] += 1

        # --- достижения клана ---
        new_ach = _check_clan_achievements(clan, rank, total)
        for key in new_ach:
            info = config.CLAN_ACHIEVEMENTS.get(key, {})
            achievement_lines.append(f'«{clan["name"]}» получает {info.get("emoji", "")} {info.get("name", key)}!')

        # --- "победитель" для игроков клана-чемпиона ---
        if rank == 1:
            for uid_str, member in clan.get("members", {}).items():
                player = players.get_or_create_player(
                    db, int(uid_str), member.get("username", ""), member.get("first_name", "Игрок")
                )
                players.unlock_achievement(player, "pobeditel")

        # --- сброс параметров ТЕКУЩЕГО сезона (не трогаем xp/level/rep/history) ---
        clan["points"] = 1000
        clan["max_win_streak"] = 0
        clan["current_win_streak"] = 0
        clan["wars_won"] = 0
        clan["best_single_multiplier"] = None
        clan["tactic"] = None
        clan["consecutive_losses"] = 0
        clan["tactic_consecutive_wins"] = 0

    if achievement_lines:
        final_text += "\n\n🏆 <b>Новые достижения кланов:</b>\n" + "\n".join(achievement_lines)

    db["pending_invite"] = None
    db["active_duels"] = {}
    db["season_started_at"] = now()
    db["last_convergence_at"] = now()
    return final_text


async def _check_and_finalize(bot: Bot) -> None:
    final_text = None
    group_id = None

    async with Storage() as db:
        started = db.get("season_started_at")
        if started is None:
            db["season_started_at"] = now()
            db["last_convergence_at"] = now()
            return

        elapsed_days = (now() - started) / 86400
        if elapsed_days < config.SEASON_LENGTH_DAYS:
            return

        if not db["clans"]:
            db["season_started_at"] = now()
            return

        group_id = db.get("group_chat_id")
        final_text = finalize_season_locked(db)

    if final_text and group_id:
        try:
            await bot.send_message(group_id, final_text + "\n\n🔄 Начинается новый сезон!", parse_mode="HTML")
        except Exception:
            pass


# --------------------------------------------------- еженедельное сжатие ---

def _apply_weekly_convergence_locked(db: dict) -> str:
    clans = list(db["clans"].values())
    for clan in clans:
        ensure_clan_fields(clan)
    if len(clans) < 2:
        return ""

    mean_points = sum(c.get("points", 0) for c in clans) / len(clans)
    lines = ["🧲 <b>Еженедельное сжатие очков войны!</b>", ""]

    for clan in clans:
        old_points = clan.get("points", 0)
        if clan.get("tactic") == "quiet":
            if old_points > mean_points:
                new_points = old_points * 0.96
            elif old_points < mean_points:
                new_points = old_points * 1.06
            else:
                new_points = old_points
        else:
            new_points = old_points + (mean_points - old_points) * config.CONVERGENCE_FACTOR
        new_points = round(new_points, 2)
        clan["points"] = new_points
        delta = new_points - old_points
        sign = "+" if delta >= 0 else ""
        lines.append(f"«{clan['name']}»: {old_points:g} → {new_points:g} ({sign}{delta:.1f})")

    return "\n".join(lines)


async def _check_weekly_convergence(bot: Bot) -> None:
    text = None
    group_id = None

    async with Storage() as db:
        season_started = db.get("season_started_at")
        last_conv = db.get("last_convergence_at") or season_started
        if season_started is None or last_conv is None:
            return

        elapsed_since_last = (now() - last_conv) / 86400
        if elapsed_since_last < config.CONVERGENCE_INTERVAL_DAYS:
            return

        if not db["clans"]:
            db["last_convergence_at"] = now()
            return

        group_id = db.get("group_chat_id")
        text = _apply_weekly_convergence_locked(db)
        db["last_convergence_at"] = now()

    if text and group_id:
        try:
            await bot.send_message(group_id, text, parse_mode="HTML")
        except Exception:
            pass


# --------------------------------------------------- годовой сброс репутации

async def _check_yearly_reputation_reset(bot: Bot) -> None:
    should_announce = False
    group_id = None

    async with Storage() as db:
        current_year = datetime.datetime.now().year
        last_year = db.get("reputation_reset_year")
        if last_year is None:
            db["reputation_reset_year"] = current_year
            return
        if last_year == current_year:
            return

        for clan in db["clans"].values():
            ensure_clan_fields(clan)
            clan["reputation"] = 0
        db["reputation_reset_year"] = current_year
        group_id = db.get("group_chat_id")
        should_announce = True

    if should_announce and group_id:
        try:
            await bot.send_message(
                group_id,
                "📆 Наступил новый год — репутация всех кланов обнулена (уровни и достижения сохранены).",
            )
        except Exception:
            pass


# --------------------------------------------------------------- цикл ------

async def season_watcher_loop(bot: Bot) -> None:
    while True:
        try:
            await _check_and_finalize(bot)
            await _check_weekly_convergence(bot)
            await _check_yearly_reputation_reset(bot)
        except Exception:
            pass
        await asyncio.sleep(config.SEASON_CHECK_INTERVAL_SECONDS)
