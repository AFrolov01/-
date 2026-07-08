# -*- coding: utf-8 -*-
"""
Сезон войны кланов.

 - Сезон длится config.SEASON_LENGTH_DAYS (30) дней, завершается автоматически:
   итоги (топ + победитель) публикуются в боевой чат, очки/серии/победы/тактика/
   недельный модификатор сбрасываются, а УРОВЕНЬ/ОПЫТ/РЕПУТАЦИЯ/ДОСТИЖЕНИЯ/
   ИСТОРИЯ КЛАНА — НЕТ, они копятся из сезона в сезон. В конце сезона бот
   также напоминает всем владельцам кланов выбрать тактику на новый сезон.
 - Каждые 7 дней сезона каждый клан получает +-% (по месту в рейтинге) к
   ОЧКАМ, ПОЛУЧАЕМЫМ В РАУНДАХ МИН (не трогает текущий баланс клана напрямую).
   Этот %% НАКАПЛИВАЕТСЯ неделя к неделе (складывается, может уйти в 0 или
   поменять знак, если ранг клана изменился). Топ получает -10%, аутсайдер
   +10%, промежуточные места — линейно между ними (чем больше кланов, тем
   мельче шаг). Тактика "Тихо не спеша" заменяет это на фиксированные +-6%
   для клана, который её выбрал.
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
    затем сбрасывает очки/серии/тактики/недельный модификатор сезона.
    Вызывать ТОЛЬКО внутри `async with Storage() as db:`."""
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
        clan["tactic_locked"] = False
        clan["consecutive_losses"] = 0
        clan["tactic_consecutive_wins"] = 0
        clan["weekly_percent_modifier"] = 0

    if achievement_lines:
        final_text += "\n\n🏆 <b>Новые достижения кланов:</b>\n" + "\n".join(achievement_lines)

    final_text += (
        "\n\n🎯 <b>Владельцы кланов!</b> Не забудьте выбрать тактику клана на "
        "новый сезон командой /tactic — сменить её потом до конца сезона будет нельзя."
    )

    db["pending_invite"] = None
    db["active_duels"] = {}
    db["season_started_at"] = now()
    db["last_weekly_modifier_at"] = now()
    return final_text


async def _check_and_finalize(bot: Bot) -> None:
    final_text = None
    group_id = None

    async with Storage() as db:
        started = db.get("season_started_at")
        if started is None:
            db["season_started_at"] = now()
            db["last_weekly_modifier_at"] = now()
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


# ------------------------------------------- еженедельный баф/дебафф очков -

def _rank_based_percent(rank_index: int, total: int) -> float:
    """rank_index: 0 = топ, total-1 = аутсайдер. Линейная растяжка -10%..+10%."""
    if total <= 1:
        return 0.0
    span = config.WEEKLY_MODIFIER_MAX_PERCENT * 2
    return -config.WEEKLY_MODIFIER_MAX_PERCENT + (rank_index / (total - 1)) * span


def _apply_weekly_modifier_locked(db: dict) -> str:
    """Каждые 7 дней происходят ДВА РАЗНЫХ события:
    1) сжатие текущих очков клана к среднему по всем кланам (10%);
    2) отдельно — накопительный %-бонус/штраф к БУДУЩИМ очкам из раундов мин,
       зависящий от места в рейтинге (или от тактики "Тихо не спеша")."""
    clans = list(db["clans"].values())
    for clan in clans:
        ensure_clan_fields(clan)
    if len(clans) < 2:
        return ""

    # ранжируем ДО каких-либо изменений — по текущим очкам
    clans_sorted = sorted(clans, key=lambda c: c.get("points", 0), reverse=True)
    total = len(clans_sorted)
    mean_points = sum(c.get("points", 0) for c in clans) / total

    lines = ["🧲 <b>Еженедельное событие войны кланов!</b>", ""]

    for rank_index, clan in enumerate(clans_sorted):
        # --- 1) сжатие очков к среднему ---
        old_points = clan.get("points", 0)
        new_points = round(old_points + (mean_points - old_points) * config.CONVERGENCE_FACTOR, 2)
        clan["points"] = new_points
        points_delta = new_points - old_points
        pts_sign = "+" if points_delta >= 0 else ""

        # --- 2) накопительный %-бонус к будущим очкам ---
        if clan.get("tactic") == "quiet":
            delta_pct = -config.QUIET_TACTIC_PERCENT if rank_index < total / 2 else config.QUIET_TACTIC_PERCENT
        else:
            delta_pct = _rank_based_percent(rank_index, total)

        old_mod = clan.get("weekly_percent_modifier", 0)
        new_mod = round(old_mod + delta_pct, 1)
        clan["weekly_percent_modifier"] = new_mod
        pct_sign = "+" if delta_pct >= 0 else ""
        total_pct_sign = "+" if new_mod >= 0 else ""

        lines.append(
            f"«{clan['name']}»: очки {old_points:g} → {new_points:g} ({pts_sign}{points_delta:.1f}) | "
            f"бонус к будущим очкам {pct_sign}{delta_pct:.0f}% → итого {total_pct_sign}{new_mod:g}%"
        )

    return "\n".join(lines)


async def _check_weekly_modifier(bot: Bot) -> None:
    text = None
    group_id = None

    async with Storage() as db:
        season_started = db.get("season_started_at")
        last_mod = db.get("last_weekly_modifier_at") or season_started
        if season_started is None or last_mod is None:
            return

        elapsed_since_last = (now() - last_mod) / 86400
        if elapsed_since_last < config.WEEKLY_MODIFIER_INTERVAL_DAYS:
            return

        if not db["clans"]:
            db["last_weekly_modifier_at"] = now()
            return

        group_id = db.get("group_chat_id")
        text = _apply_weekly_modifier_locked(db)
        db["last_weekly_modifier_at"] = now()

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
            await _check_weekly_modifier(bot)
            await _check_yearly_reputation_reset(bot)
        except Exception:
            pass
        await asyncio.sleep(config.SEASON_CHECK_INTERVAL_SECONDS)
