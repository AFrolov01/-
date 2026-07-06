# -*- coding: utf-8 -*-
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from bot.storage import Storage, now
from bot.keyboards import skip_avatar_kb

router = Router(name="clan_create")


class CreateClan(StatesGroup):
    waiting_name = State()
    waiting_avatar = State()
    waiting_motto = State()


def _user_already_in_clan(db: dict, user_id: int) -> bool:
    for clan in db["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return True
    return False


@router.message(Command("createclan"))
async def cmd_create_clan(message: Message, state: FSMContext) -> None:
    async with Storage() as db:
        if _user_already_in_clan(db, message.from_user.id):
            await message.reply(
                "Вы уже состоите в клане. Сначала покиньте текущий клан, чтобы создать новый."
            )
            return
    await state.set_state(CreateClan.waiting_name)
    await message.reply("🏰 Введите название вашего клана:")


@router.message(StateFilter(CreateClan.waiting_name))
async def process_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 40:
        await message.reply("Название должно быть от 1 до 40 символов. Попробуйте ещё раз:")
        return
    await state.update_data(name=name)
    await state.set_state(CreateClan.waiting_avatar)
    await message.reply(
        "🖼 Пришлите аватарку клана (фото) или нажмите «Пропустить».",
        reply_markup=skip_avatar_kb(),
    )


@router.message(StateFilter(CreateClan.waiting_avatar), F.photo)
async def process_avatar_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(avatar_file_id=file_id)
    await state.set_state(CreateClan.waiting_motto)
    await message.reply("📝 Введите девиз клана:")


@router.callback_query(StateFilter(CreateClan.waiting_avatar), F.data == "createclan:skip_avatar")
async def process_avatar_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(avatar_file_id=None)
    await state.set_state(CreateClan.waiting_motto)
    await callback.message.edit_text("📝 Введите девиз клана:")
    await callback.answer()


@router.message(StateFilter(CreateClan.waiting_motto))
async def process_motto(message: Message, state: FSMContext) -> None:
    motto = (message.text or "").strip()
    if not motto or len(motto) > 120:
        await message.reply("Девиз должен быть от 1 до 120 символов. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    name = data["name"]
    avatar_file_id = data.get("avatar_file_id")

    async with Storage() as db:
        if _user_already_in_clan(db, message.from_user.id):
            await state.clear()
            await message.reply("Вы уже успели вступить в клан, создание отменено.")
            return

        clan_id = db["next_clan_id"]
        db["next_clan_id"] += 1

        user = message.from_user
        member = {
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "Игрок",
            "matches_played": 0,
            "last_played_at": 0,
        }
        db["clans"][str(clan_id)] = {
            "id": clan_id,
            "name": name,
            "motto": motto,
            "avatar_file_id": avatar_file_id,
            "creator_id": user.id,
            "points": 1000,  # стартовые очки клана
            "max_win_streak": 0,
            "current_win_streak": 0,
            "wars_won": 0,
            "best_single_multiplier": None,
            "members": {str(user.id): member},
            "last_played_at": 0,
        }

    await state.clear()
    await message.reply(
        f"🎉 Клан «{name}» создан и уже участвует в войне кланов!\n"
        f"Девиз: {motto}\n\n"
        "Приглашайте друзей в чат — вступить можно командой /join."
    )
