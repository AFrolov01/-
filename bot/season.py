# -*- coding: utf-8 -*-
"""
Сезон войны кланов.

СЕЗОН ОДИН ДЛЯ ВСЕХ ГРУПП (общий таймер: 30 дней, еженедельные события,
годовой сброс репутации — всё происходит одновременно во всех группах сразу).
Но КОНКУРЕНЦИЯ (места, топ/аутсайдер, достижения "1 место в сезоне" и т.д.)
считается ОТДЕЛЬНО внутри каждой группы — группы не соревнуются друг с другом
напрямую, только между своими кланами.

 - Сезон длится config.SEASON_LENGTH_DAYS (30) дней, завершается автоматически
   ВЕЗДЕ одновременно: в каждой группе подводятся её собственные итоги (топ +
   победитель ЭТОЙ группы), очки/серии/победы/тактика/недельный модификатор
   сбрасываются, а УРОВЕНЬ/ОПЫТ/РЕПУТАЦИЯ/ДОСТИЖЕНИЯ/ИСТОРИЯ КЛАНА — НЕТ, они
   копятся из сезона в сезон.
 - Каждые 7 дней (одновременно во всех группах) каждый клан получает +-% (по
   месту в рейтинге ВНУТРИ СВОЕЙ ГРУППЫ) к очкам, получаемым в раундах мин
   (не трогает текущий баланс клана напрямую). Этот %% накапливается неделя к
   неделе. Топ группы получает -10%, аутсайдер +10%, промежуточные места —
   линейно между ними. Тактика "Тихо не спеша" заменяет это на фиксированные
   +-6% для клана, который её выбрал.
 - Репутация клана обнуляется в начале каждого календарного года везде сразу
   (сам клан, уровень и достижения — нет, это отдельная система).
"""

import asyncio
import datetime

from aiogram import Bot

import config
from bot.storage import Storage, now
from bot.chat_state import all_chat_ids, get_chat
from bot.leaderboard import build_top_text
from bot.clan_utils import ensure_clan_fields
from bot.leveling import level_from_xp, apply_level_up_reputation
from bot.reputation import add_reputation
from bot import players


# ------------------------------------------------------- завершение сезона -

def _check_clan_achievements(clan: dict, rank: int, total_clans: int) -> list:
    """Проверяет и разблокирует ачивки клана по итогам сезона (в рамках своей
    группы). Возвращает список новых ключей (для объявления в чате)."""
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


def finalize_season_locked(chat: dict) -> str:
    """Подводит итоги сезона ОДНОЙ группы: опыт/уровни/репутация/медали/
    достижения её кланов, затем сбрасывает очки/серии/тактики/недельный
    модификатор. `chat` — состояние конкретной группы (bot/chat_state.py).
    Вызывать ТОЛЬКО внутри `async with Storage() as db:`."""
    clans = list(chat["clans"].values())
    for clan in clans:
        ensure_clan_fields(clan)

    if not clans:
        chat["pending_invite"] = None
        chat["active_duels"] = {}
        return ""

    final_text = build_top_text(chat, "🏁 <b>Сезон завершён!</b>", declare_winner=True)

    clans_sorted = sorted(clans, key=lambda c: c.get("points", 0), reverse=True)
    total = len(clans_sorted)
    achievement_lines = []

    for rank, clan in enumerate(clans_sorted, start=1):
        # --- репутация за место в сезоне (внутри своей группы) ---
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

        # --- "победитель" для игроков клана-чемпиона (своей группы) ---
        if rank == 1:
            for uid_str, member in clan.get("members", {}).items():
                player = players.get_or_create_player(
                    chat, int(uid_str), member.get("username", ""), member.get("first_name", "Игрок")
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

    chat["pending_invite"] = None
    chat["active_duels"] = {}
    return final_text


async def _check_and_finalize(bot: Bot) -> None:
    """Сезон общий: проверяем ОДИН глобальный таймер, но при завершении
    подводим итоги в КАЖДОЙ группе отдельно и рассылаем туда её собственный текст."""
    to_send = []  # (chat_id, text)

    async with Storage() as db:
        started = db.get("season_started_at")
        if started is None:
            db["season_started_at"] = now()
            db["last_weekly_modifier_at"] = now()
            return

        elapsed_days = (now() - started) / 86400
        if elapsed_days < config.SEASON_LENGTH_DAYS:
            return

        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            if not chat["clans"]:
                continue
            final_text = finalize_season_locked(chat)
            if final_text:
                to_send.append((chat_id, final_text + "\n\n🔄 Начинается новый сезон!"))

        db["season_started_at"] = now()
        db["last_weekly_modifier_at"] = now()

    for chat_id, text in to_send:
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            pass


# ------------------------------------------- еженедельный баф/дебафф очков -

def _rank_based_percent(rank_index: int, total: int) -> float:
    """rank_index: 0 = топ, total-1 = аутсайдер. Линейная растяжка -10%..+10%."""
    if total <= 1:
        return 0.0
    span = config.WEEKLY_MODIFIER_MAX_PERCENT * 2
    return -config.WEEKLY_MODIFIER_MAX_PERCENT + (rank_index / (total - 1)) * span


def _apply_weekly_modifier_for_chat(chat: dict) -> str:
    """Каждые 7 дней (общий таймер) происходят ДВА РАЗНЫХ события ВНУТРИ
    каждой группы отдельно:
    1) сжатие текущих очков клана к среднему ПО СВОЕЙ ГРУППЕ (10%);
    2) отдельно — накопительный %-бонус/штраф к БУДУЩИМ очкам из раундов мин,
       зависящий от места в рейтинге СВОЕЙ ГРУППЫ (или от тактики "Тихо не спеша")."""
    from bot.texts import fmt_num

    clans = list(chat["clans"].values())
    for clan in clans:
        ensure_clan_fields(clan)
    if len(clans) < 2:
        return ""

    clans_sorted = sorted(clans, key=lambda c: c.get("points", 0), reverse=True)
    total = len(clans_sorted)
    mean_points = sum(c.get("points", 0) for c in clans) / total

    lines = [
        "🧲 <b>Еженедельное событие войны кланов!</b>",
        "Очки приведены к среднему по группе, бонус к будущим очкам обновлён.",
        "",
    ]

    for rank_index, clan in enumerate(clans_sorted):
        # --- 1) сжатие очков к среднему (внутри группы) ---
        old_points = clan.get("points", 0)
        new_points = round(old_points + (mean_points - old_points) * config.CONVERGENCE_FACTOR, 2)
        clan["points"] = new_points
        points_delta = round(new_points - old_points, 2)
        pts_sign = "+" if points_delta >= 0 else "−"

        # --- 2) накопительный %-бонус к будущим очкам ---
        if clan.get("tactic") == "quiet":
            delta_pct = -config.QUIET_TACTIC_PERCENT if rank_index < total / 2 else config.QUIET_TACTIC_PERCENT
        else:
            delta_pct = _rank_based_percent(rank_index, total)

        had_previous_snapshot = clan.get("weekly_snapshots_done", 0) > 0
        old_mod = clan.get("weekly_percent_modifier", 0)
        new_mod = round(old_mod + delta_pct, 1)
        clan["weekly_percent_modifier"] = new_mod
        clan["weekly_snapshots_done"] = clan.get("weekly_snapshots_done", 0) + 1

        arrow = "🔺" if new_mod > 0 else ("🔻" if new_mod < 0 else "▪️")
        new_mod_s = f"{'+' if new_mod > 0 else ''}{new_mod:g}%"
        if had_previous_snapshot:
            old_mod_s = f"{'+' if old_mod > 0 else ''}{old_mod:g}%"
            bonus_line = f"🔮 бонус к очкам: {old_mod_s} → {new_mod_s} {arrow}"
        else:
            bonus_line = f"🔮 бонус к очкам: {new_mod_s} (первый срез для этого клана) {arrow}"

        lines.append(f"▫️ {clan['name']}")
        lines.append(f"📊 {fmt_num(old_points)} → {fmt_num(new_points)} ({pts_sign}{fmt_num(abs(points_delta))})")
        lines.append(bonus_line)
        lines.append("")

    return "\n".join(lines).rstrip()


async def _check_weekly_modifier(bot: Bot) -> None:
    to_send = []

    async with Storage() as db:
        season_started = db.get("season_started_at")
        last_mod = db.get("last_weekly_modifier_at") or season_started
        if season_started is None or last_mod is None:
            return

        elapsed_since_last = (now() - last_mod) / 86400
        if elapsed_since_last < config.WEEKLY_MODIFIER_INTERVAL_DAYS:
            return

        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            text = _apply_weekly_modifier_for_chat(chat)
            if text:
                to_send.append((chat_id, text))

        db["last_weekly_modifier_at"] = now()

    for chat_id, text in to_send:
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            pass


# --------------------------------------------------- годовой сброс репутации

async def _check_yearly_reputation_reset(bot: Bot) -> None:
    chat_ids_to_notify = []

    async with Storage() as db:
        current_year = datetime.datetime.now().year
        last_year = db.get("reputation_reset_year")
        if last_year is None:
            db["reputation_reset_year"] = current_year
            return
        if last_year == current_year:
            return

        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            if not chat["clans"]:
                continue
            for clan in chat["clans"].values():
                ensure_clan_fields(clan)
                clan["reputation"] = 0
            chat_ids_to_notify.append(chat_id)
        db["reputation_reset_year"] = current_year

    for chat_id in chat_ids_to_notify:
        try:
            await bot.send_message(
                chat_id,
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
