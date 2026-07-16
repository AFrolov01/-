# -*- coding: utf-8 -*-
"""
Секретная реклама владельца бота.

Команда /creatadd НЕ показывается в меню команд и НИКАК не реагирует, если
её вызывает кто-то, кроме владельца (config.ADMIN_ID) — для не-владельца
бот просто молчит, как будто такой команды не существует.

Поток:
 1. /creatadd — бот просит прислать ОДНИМ сообщением фото с подписью
    (или слово "выкл", чтобы отключить текущую рекламу).
 2. Владелец присылает фото+текст.
 3. Бот спрашивает интервал в днях (можно дробный: 1.5, 0.2 и т.п.).
 4. Владелец присылает число — реклама сохраняется и начинает рассылаться
    во ВСЕ группы, где есть бот, с этим интервалом.

Фоновая задача (ad_watcher_loop) проверяет раз в AD_CHECK_INTERVAL_SECONDS,
не пора ли разослать рекламу, и рассылает её (фото + подпись) во все
известные группы.
"""

import asyncio

from aiogram import Router, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

import config
from bot.storage import Storage, now
from bot.chat_state import all_chat_ids
from bot.fsm_utils import check_command_escape

router = Router(name="ads")


class AdSetup(StatesGroup):
    waiting_content = State()
    waiting_interval = State()


@router.message(Command("creatadd"))
async def cmd_creatadd(message: Message, state: FSMContext) -> None:
    if message.from_user.id != config.ADMIN_ID:
        return  # полная тишина для всех, кроме владельца — команда как будто не существует
    await state.set_state(AdSetup.waiting_content)
    await message.reply(
        "🔒 Пришлите ОДНИМ сообщением фото с подписью (текст рекламы) — так, как "
        "это увидят в группах.\nИли напишите «выкл», чтобы отключить текущую рекламу."
    )


@router.message(StateFilter(AdSetup.waiting_content))
async def process_ad_content(message: Message, state: FSMContext) -> None:
    if message.from_user.id != config.ADMIN_ID:
        return
    if await check_command_escape(message, state):
        return

    if message.text and message.text.strip().lower() in ("выкл", "off", "стоп"):
        async with Storage() as db:
            db["ad"] = {"text": None, "photo_file_id": None, "interval_days": None, "last_sent_at": None}
        await state.clear()
        await message.reply("🔕 Реклама отключена.")
        return

    if not message.photo:
        await message.reply("Нужно фото с подписью одним сообщением. Попробуйте ещё раз, или напишите «выкл».")
        return

    photo_file_id = message.photo[-1].file_id
    caption = message.caption or ""
    await state.update_data(photo_file_id=photo_file_id, text=caption)
    await state.set_state(AdSetup.waiting_interval)
    await message.reply(
        "⏱ Через сколько дней повторять показ? Можно дробное число, "
        "например 1.5 (полтора дня) или 0.2 (~5 часов)."
    )


@router.message(StateFilter(AdSetup.waiting_interval))
async def process_ad_interval(message: Message, state: FSMContext) -> None:
    if message.from_user.id != config.ADMIN_ID:
        return
    if await check_command_escape(message, state):
        return

    try:
        interval_days = float((message.text or "").strip().replace(",", "."))
    except ValueError:
        await message.reply("Нужно число (можно дробное), например 1.5 или 0.2. Попробуйте ещё раз.")
        return
    if interval_days <= 0:
        await message.reply("Интервал должен быть положительным числом.")
        return

    data = await state.get_data()
    async with Storage() as db:
        db["ad"] = {
            "text": data.get("text", ""),
            "photo_file_id": data.get("photo_file_id"),
            "interval_days": interval_days,
            "last_sent_at": now(),
        }
        groups_count = len(all_chat_ids(db))
    await state.clear()
    await message.reply(
        f"✅ Реклама настроена. Будет рассылаться каждые {interval_days:g} дн. "
        f"во все группы бота (сейчас их {groups_count})."
    )


async def _check_and_broadcast(bot: Bot) -> None:
    to_send = []

    async with Storage() as db:
        ad = db.get("ad", {})
        if not ad.get("text") and not ad.get("photo_file_id"):
            return
        if not ad.get("interval_days"):
            return
        last_sent = ad.get("last_sent_at")
        if last_sent is not None:
            elapsed_days = (now() - last_sent) / 86400
            if elapsed_days < ad["interval_days"]:
                return

        chat_ids = all_chat_ids(db)
        db["ad"]["last_sent_at"] = now()

    for chat_id in chat_ids:
        to_send.append(chat_id)

    for chat_id in to_send:
        try:
            if ad.get("photo_file_id"):
                await bot.send_photo(chat_id, ad["photo_file_id"], caption=ad.get("text") or None)
            elif ad.get("text"):
                await bot.send_message(chat_id, ad["text"])
        except Exception:
            pass


async def ad_watcher_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(config.AD_CHECK_INTERVAL_SECONDS)
        try:
            await _check_and_broadcast(bot)
        except Exception:
            pass
