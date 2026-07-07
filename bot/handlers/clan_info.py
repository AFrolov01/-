# -*- coding: utf-8 -*-
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardRemove

import config
from bot.storage import Storage
from bot.matchmaking import announce_duel
from bot.clan_utils import ensure_clan_fields
from bot.leveling import clan_level_block, clan_prefix
from bot.reputation import reputation_block

router = Router(name="clan_info")


def _find_user_clan(db: dict, user_id: int):
    for clan in db["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return clan
    return None


START_TEXT = (
    "⚔️ <b>Битвы кланов</b>\n\n"
    "Привет! Я слежу за войной кланов в этом чате: дуэли на поле 5×5, "
    "ставка — очки клана, риск — количество мин.\n\n"
    "<b>Команды:</b>\n"
    "🏰 /createclan — создать свой клан\n"
    "🤝 /join — вступить в существующий клан\n"
    "📊 /clan — карточка своего клана (очки, участники, серии побед)\n"
    "🚪 /leaveclan — покинуть свой клан\n"
    "👢 /kick — исключить участника (только для создателя клана)\n"
    "🗑 /deleteclan — расформировать клан (только для создателя)\n"
    "🏆 /top — топ кланов и топ игроков прямо сейчас\n"
    "📅 /season — сколько дней осталось до конца сезона\n"
    "🎯 /tactic — выбрать тактику клана на сезон (создатель клана)\n"
    "🛒 /shop — магазин привилегий за валюту Те\n"
    "👤 /iam (или просто слово «Б» в чат) — ваш профиль\n"
    "⚔️ /minduel — начать назначенную дуэль (доступно только вызванным игрокам)\n\n"
    "Совет: ответьте на чьё-то сообщение словами «твой б», чтобы увидеть "
    "профиль этого человека.\n\n"
    "Дуэли между кланами бот объявляет сам, примерно раз в 6 часов. Сезон "
    "войны длится 30 дней, по истечении которых бот сам подведёт итоги и "
    "объявит победителя, начислит опыт и репутацию кланам, после чего "
    "начнётся новый сезон.\n"
    "Если вы админ этого чата — вызовите /setgroup, чтобы назначить его "
    "основным чатом войны кланов."
)


@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    text = START_TEXT
    if message.from_user.id == config.ADMIN_ID:
        text += (
            "\n\n👑 <b>Команды владельца:</b>\n"
            "/setgroup — назначить текущий чат боевым (вызывать в самой группе)\n"
            "/forceduel — объявить дуэль прямо сейчас, не дожидаясь расписания "
            "(можно вызвать даже здесь, в ЛС)"
        )
    await message.reply(text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


@router.message(Command("clan"))
async def cmd_clan(message: Message) -> None:
    async with Storage() as db:
        clan = _find_user_clan(db, message.from_user.id)
        if clan:
            ensure_clan_fields(clan)

    if not clan:
        await message.reply("Вы не состоите ни в одном клане. Используйте /join или /createclan.")
        return

    members = list(clan.get("members", {}).values())
    members_lines = []
    for m in members:
        name = f'@{m["username"]}' if m.get("username") else m.get("first_name", "Игрок")
        crown = " 👑" if m["user_id"] == clan.get("creator_id") else ""
        members_lines.append(f"• {name}{crown}")

    best = clan.get("best_single_multiplier")
    best_line = "—"
    if best:
        best_name = f'@{best["username"]}' if best.get("username") else "игрок"
        best_line = f'x{best["value"]:.2f}'.replace(".", ",") + f" ({best_name})"

    medals = clan.get("medals", {"gold": 0, "silver": 0, "bronze": 0})
    tactic_name = config.SEASON_TACTICS.get(clan.get("tactic")) if clan.get("tactic") else "не выбрана (см. /tactic)"

    text = (
        f"{clan_prefix(clan)}\n"
        f"🏰 <b>{clan['name']}</b>\n"
        f"📝 Девиз: {clan['motto']}\n\n"
        f"{clan_level_block(clan)}\n\n"
        f"{reputation_block(clan)}\n\n"
        f"🏆 Очки сезона: {clan.get('points', 0):g}\n"
        f"🔥 Текущая серия побед: {clan.get('current_win_streak', 0)}\n"
        f"⭐ Максимальная серия побед: {clan.get('max_win_streak', 0)}\n"
        f"🏅 Побед в войне кланов: {clan.get('wars_won', 0)}\n"
        f"💎 Лучший выигрышный множитель за бой: {best_line}\n"
        f"🎯 Тактика сезона: {tactic_name}\n\n"
        f"📜 <b>История:</b>\n"
        f"Сезонов сыграно: {clan.get('seasons_played', 0)}\n"
        f"🥇 x{medals.get('gold', 0)}  🥈 x{medals.get('silver', 0)}  🥉 x{medals.get('bronze', 0)}\n\n"
        f"👥 <b>Участники ({len(members)}):</b>\n" + "\n".join(members_lines)
    )
    await message.reply(text, parse_mode="HTML")


@router.message(Command("setgroup"))
async def cmd_set_group(message: Message) -> None:
    if message.from_user.id != config.ADMIN_ID:
        await message.reply("Эта команда доступна только администратору бота.")
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Эту команду нужно вызывать в групповом чате войны кланов.")
        return
    async with Storage() as db:
        db["group_chat_id"] = message.chat.id
    await message.reply("✅ Этот чат назначен основным чатом войны кланов.")


@router.message(Command("forceduel"))
async def cmd_force_duel(message: Message, bot: Bot) -> None:
    """Владелец бота может вручную вызвать очередную дуэль — в том числе
    прямо из личных сообщений боту, не дожидаясь ежедневного расписания."""
    if message.from_user.id != config.ADMIN_ID:
        await message.reply("Эта команда доступна только владельцу бота.")
        return

    success, reason = await announce_duel(bot)
    if success:
        await message.reply(f"✅ {reason}")
    else:
        await message.reply(f"⚠️ Дуэль не объявлена: {reason}")
