# -*- coding: utf-8 -*-
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import config
from bot.storage import Storage

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
    "⚔️ /minduel — начать назначенную дуэль (доступно только вызванным игрокам)\n\n"
    "Дуэли между кланами бот объявляет сам, примерно раз в день. "
    "Если вы админ этого чата — вызовите /setgroup, чтобы назначить его "
    "основным чатом войны кланов."
)


@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.reply(START_TEXT, parse_mode="HTML")


@router.message(Command("clan"))
async def cmd_clan(message: Message) -> None:
    async with Storage() as db:
        clan = _find_user_clan(db, message.from_user.id)

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

    text = (
        f"🏰 <b>{clan['name']}</b>\n"
        f"📝 Девиз: {clan['motto']}\n"
        f"🏆 Очки: {clan.get('points', 0):g}\n"
        f"🔥 Текущая серия побед: {clan.get('current_win_streak', 0)}\n"
        f"⭐ Максимальная серия побед: {clan.get('max_win_streak', 0)}\n"
        f"🏅 Побед в войне кланов: {clan.get('wars_won', 0)}\n"
        f"💎 Лучший выигрышный множитель за бой: {best_line}\n\n"
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
