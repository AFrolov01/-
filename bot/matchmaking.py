# -*- coding: utf-8 -*-
"""
Матчмейкинг для войны кланов.

Правила из ТЗ (реализованы приближённо, но осмысленно):
 - если кланов всего 2 — играют всегда друг с другом;
 - если кланов больше 2:
     * первым выбирается клан, который дольше всех не играл (или ещё не играл вообще)
       и/или у которого меньше всего очков ("аутсайдеры" в приоритете);
     * его соперник должен чередоваться: то другой аутсайдер, то лидер по очкам —
       чтобы у лидера тоже не было простоя, а у аутсайдеров был шанс "выстрелить";
 - внутри клана на бой отправляется участник, который дольше всех не участвовал
   в дуэлях (round-robin), чтобы не играл всё время один и тот же человек.

Состояние чередования (кого позвать соперником — другого аутсайдера или лидера)
хранится в самом db как db["matchmaking_alternate_leader"] (bool).
"""

import asyncio
import random
from typing import Optional, Tuple

from aiogram import Bot

import config
from bot.storage import Storage, now
from bot import texts


def _pick_member(clan: dict) -> Optional[dict]:
    members = list(clan.get("members", {}).values())
    if not members:
        return None
    # тот, кто дольше всех не играл (или ни разу не играл -> last_played_at = 0)
    members.sort(key=lambda m: (m.get("matches_played", 0), m.get("last_played_at", 0)))
    return members[0]


def pick_duel_pair(db: dict) -> Optional[Tuple[dict, dict, dict, dict]]:
    """Возвращает (clan_a, member_a, clan_b, member_b) либо None, если играть некому."""
    clans = [c for c in db["clans"].values() if c.get("members")]
    if len(clans) < 2:
        return None

    if len(clans) == 2:
        clan_a, clan_b = clans[0], clans[1]
    else:
        # аутсайдер: меньше очков + дольше не играл
        clans_sorted_outsider = sorted(
            clans, key=lambda c: (c.get("points", 0), c.get("last_played_at", 0))
        )
        clan_a = clans_sorted_outsider[0]

        alternate_leader = db.get("matchmaking_alternate_leader", False)
        rest = [c for c in clans if c["id"] != clan_a["id"]]
        if alternate_leader:
            # соперник — лидер по очкам
            rest_sorted = sorted(rest, key=lambda c: -c.get("points", 0))
        else:
            # соперник — следующий по "аутсайдерству"
            rest_sorted = sorted(rest, key=lambda c: (c.get("points", 0), c.get("last_played_at", 0)))
        clan_b = rest_sorted[0]
        db["matchmaking_alternate_leader"] = not alternate_leader

    member_a = _pick_member(clan_a)
    member_b = _pick_member(clan_b)
    if not member_a or not member_b:
        return None
    return clan_a, member_a, clan_b, member_b


async def announce_duel(bot: Bot) -> Tuple[bool, str]:
    """Возвращает (успех, причина/описание) — удобно и для планировщика, и для
    ручного вызова админом командой /forceduel из ЛС бота."""
    async with Storage() as db:
        group_id = db.get("group_chat_id")
        if not group_id:
            return False, "Боевой чат ещё не назначен. Вызовите /setgroup в нужной группе."
        if db.get("pending_invite"):
            return False, "Предыдущий вызов на дуэль ещё не сыгран (ждём /minduel от вызванных игроков)."
        pair = pick_duel_pair(db)
        if not pair:
            return False, "Недостаточно кланов с участниками для дуэли (нужно минимум 2 клана с игроками)."
        clan_a, member_a, clan_b, member_b = pair

        db["pending_invite"] = {
            "clan_a_id": clan_a["id"],
            "player_a_id": member_a["user_id"],
            "clan_b_id": clan_b["id"],
            "player_b_id": member_b["user_id"],
            "created_at": now(),
        }
        name_a = f'@{member_a["username"]}' if member_a.get("username") else member_a.get("first_name", "Игрок")
        name_b = f'@{member_b["username"]}' if member_b.get("username") else member_b.get("first_name", "Игрок")
        text = texts.duel_invite_text(name_a, name_b)

    try:
        await bot.send_message(group_id, text, parse_mode="HTML")
    except Exception as e:
        return False, f"Не удалось отправить сообщение в боевой чат: {e}"

    return True, f"Дуэль объявлена: «{clan_a['name']}» vs «{clan_b['name']}»."


async def scheduler_loop(bot: Bot) -> None:
    """Фоновая задача: раз в ~2 дня (со случайным разбросом) объявляет новую дуэль."""
    while True:
        interval_hours = random.uniform(
            config.DUEL_INTERVAL_MIN_HOURS, config.DUEL_INTERVAL_MAX_HOURS
        )
        await asyncio.sleep(interval_hours * 3600)
        try:
            await announce_duel(bot)
        except Exception:
            # планировщик не должен падать из-за единичной ошибки
            pass
