# -*- coding: utf-8 -*-
"""
Профиль игрока.

 - /iam или сообщение "Б" (без слэша) — показывает СОБСТВЕННЫЙ профиль.
 - "твой б" ответом на чьё-то сообщение — показывает профиль ТОГО, кому
   отвечаешь; без ответа бот пишет "сообщение не выбрано".
 - "я помылся" — секретное достижение (без объявления, что это триггер).
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

import config
from bot.storage import Storage
from bot import players
from bot.clan_utils import ensure_clan_fields
from bot.leveling import clan_prefix

router = Router(name="player_profile")


def _find_user_clan(db: dict, user_id: int):
    for clan in db["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return clan
    return None


def _build_profile_text(db: dict, user_id: int) -> str:
    player = players.find_player(db, user_id)
    if not player:
        return "У этого игрока пока нет ни одного сыгранного раунда."

    clan = _find_user_clan(db, user_id)
    clan_line = "не состоит в клане"
    if clan:
        ensure_clan_fields(clan)
        clan_line = f"{clan_prefix(clan)} «{clan['name']}»"

    name = players.display_name(player)
    avg_all = players.average_multiplier_all_time(player)
    avg_30d = players.average_multiplier_30d(player)
    wr = players.win_rate(player)

    return (
        f"👤 <b>Профиль: {name}</b>\n"
        f"🏰 Клан: {clan_line}\n\n"
        f"💥 Лучший множитель: x{player.get('best_multiplier', 0):.2f}\n"
        f"🎯 Шанс победы: {wr}% ({player.get('wins', 0)}/{player.get('total_rounds', 0)})\n"
        f"📊 Средний множитель (всё время): x{avg_all:.2f}\n"
        f"📅 Средний множитель (30 дней): x{avg_30d:.2f}\n"
        f"💰 Баланс: {player.get('currency', 0):.2f} Те\n\n"
        f"🏅 Достижения: {players.achievements_text(player)}"
    )


@router.message(Command("iam"))
async def cmd_iam(message: Message) -> None:
    async with Storage() as db:
        text = _build_profile_text(db, message.from_user.id)
    await message.reply(text, parse_mode="HTML")


@router.message(F.text.lower() == "б")
async def trigger_own_profile(message: Message) -> None:
    async with Storage() as db:
        text = _build_profile_text(db, message.from_user.id)
    await message.reply(text, parse_mode="HTML")


@router.message(F.text.lower() == "твой б")
async def trigger_other_profile(message: Message) -> None:
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("сообщение не выбрано")
        return
    target_id = message.reply_to_message.from_user.id
    async with Storage() as db:
        text = _build_profile_text(db, target_id)
    await message.reply(text, parse_mode="HTML")


@router.message(F.text.lower() == "я помылся")
async def secret_achievement(message: Message) -> None:
    async with Storage() as db:
        player = players.get_or_create_player(
            db, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Игрок"
        )
        is_new = players.unlock_achievement(player, "ya_pomylsya")

    if is_new:
        await message.reply("ору с тебя 🛀")
