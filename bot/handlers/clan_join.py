# -*- coding: utf-8 -*-
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InputMediaPhoto

from bot.storage import Storage
from bot.chat_state import get_chat
from bot.keyboards import clan_carousel_kb

router = Router(name="clan_join")


def _user_already_in_clan(chat: dict, user_id: int) -> bool:
    for clan in chat["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return True
    return False


def _clan_card_text(clan: dict) -> str:
    members_count = len(clan.get("members", {}))
    return (
        f"🏰 <b>{clan['name']}</b>\n"
        f"📝 Девиз: {clan['motto']}\n"
        f"👥 Участников: {members_count}\n"
        f"🏆 Очки: {clan.get('points', 0):g}"
    )


async def _render_card(callback_or_message, clans: list, index: int, edit: bool) -> None:
    if not clans:
        text = "Пока нет ни одного клана в этой группе. Создайте свой командой /createclan!"
        if edit:
            await callback_or_message.message.edit_text(text)
        else:
            await callback_or_message.answer(text)
        return

    index = index % len(clans)
    clan = clans[index]
    text = _clan_card_text(clan)
    kb = clan_carousel_kb(index, len(clans), clan["id"])
    avatar = clan.get("avatar_file_id")

    target = callback_or_message.message if edit else callback_or_message
    try:
        if avatar:
            media = InputMediaPhoto(media=avatar, caption=text, parse_mode="HTML")
            if edit:
                await target.edit_media(media=media, reply_markup=kb)
            else:
                await target.answer_photo(avatar, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            if edit:
                # если предыдущее сообщение было фото — edit_text упадёт, подстрахуемся
                try:
                    await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
                except Exception:
                    await target.edit_caption(caption=text, parse_mode="HTML", reply_markup=kb)
            else:
                await target.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


@router.message(Command("join"))
async def cmd_join(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await message.reply(
            "Вступить в клан можно только внутри группы — у каждой группы своя "
            "независимая война. Вызовите /join прямо в нужном чате."
        )
        return

    async with Storage() as db:
        chat = get_chat(db, message.chat.id)
        if _user_already_in_clan(chat, message.from_user.id):
            await message.reply("Вы уже состоите в клане.")
            return
        clans = sorted(chat["clans"].values(), key=lambda c: c["id"])
    await _render_card(message, clans, 0, edit=False)


@router.callback_query(F.data.startswith("join:prev:"))
async def cb_prev(callback: CallbackQuery) -> None:
    index = int(callback.data.split(":")[2])
    async with Storage() as db:
        chat = get_chat(db, callback.message.chat.id)
        clans = sorted(chat["clans"].values(), key=lambda c: c["id"])
    await _render_card(callback, clans, index - 1, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("join:next:"))
async def cb_next(callback: CallbackQuery) -> None:
    index = int(callback.data.split(":")[2])
    async with Storage() as db:
        chat = get_chat(db, callback.message.chat.id)
        clans = sorted(chat["clans"].values(), key=lambda c: c["id"])
    await _render_card(callback, clans, index + 1, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("join:select:"))
async def cb_select(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    clan_id = int(parts[2])
    user = callback.from_user

    async with Storage() as db:
        chat = get_chat(db, callback.message.chat.id)
        if _user_already_in_clan(chat, user.id):
            await callback.answer("Вы уже состоите в клане.", show_alert=True)
            return
        clan = chat["clans"].get(str(clan_id))
        if not clan:
            await callback.answer("Этот клан больше не существует.", show_alert=True)
            return
        clan["members"][str(user.id)] = {
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "Игрок",
            "matches_played": 0,
            "last_played_at": 0,
        }
        clan_name = clan["name"]

    await callback.answer(f"Вы вступили в клан «{clan_name}»!", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
