# -*- coding: utf-8 -*-
"""
Банк для валюты Те (своя экономика в каждой группе).

 - /bank (без аргументов) — правила + текущий баланс, и сразу спрашивает,
   сколько внести (следующим сообщением). Если в ответ пришло не число —
   запрос отменяется, повторно сумма не считывается.
 - /bank положить СУММА (или "всё"/"все") — внести сразу, без лишнего шага.
 - /bank снять СУММА (или "всё"/"все") — снять сразу. Отдельной команды
   /bank_snyat больше нет — снятие идёт через /bank или через фразу "Банк".
 - Снять деньги можно в ЛЮБОЙ момент, без ожидания (задержки на вывод нет).
 - Бот также понимает обычные фразы без слэша (в группе и в ЛС):
   "Банк", "Банк положить 50", "Банк положить всё",
   "Банк снять 50", "Банк снять всё".
 - Везде только русский текст (внести / снять / остаток), без deposit/withdraw.
 - В конце любого сообщения банка всегда есть подсказка, как положить/снять
   (в том числе словом "всё"/"все"), чтобы не нужно было помнить синтаксис.
"""

from typing import Tuple

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

import config
from bot.storage import Storage, now
from bot.chat_state import get_chat, resolve_chat_for_message
from bot import players
from bot.fsm_utils import check_command_escape

router = Router(name="bank")

RULES_TEXT = (
    "🏦 <b>Банк</b>\n\n"
    f"💹 Ставка: {config.BANK_DAILY_RATE * 100:g}% в день (сложный процент — "
    "проценты сами капают на проценты).\n"
    "✅ Снять деньги можно в любой момент, без ожидания."
)

HINT_TEXT = (
    "\n\n💬 Напишите «положить СУММА» для пополнения банка или «снять СУММА» "
    "для снятия денег со вклада (работает и слово «всё»/«все», чтобы "
    "положить/снять сразу всё)."
)


class BankStates(StatesGroup):
    waiting_deposit_amount = State()
    waiting_withdraw_amount = State()


def _info_text(player: dict) -> str:
    balance = players.bank_current_balance(player)
    total_interest = players.bank_total_interest_earned(player)
    text = (
        RULES_TEXT
        + f"\n\n💰 На руках: {player.get('currency', 0):.2f} Те\n"
        + f"🏦 На вкладе (с процентами): {balance:.2f} Те"
    )
    if total_interest > 0:
        text += f"\n💹 Всего начислено процентами за всё время: {total_interest:.2f} Те"
    return text


def _parse_amount(raw: str, player: dict, for_withdraw: bool) -> Tuple[float, str]:
    """Возвращает (сумма, ошибка). Если ошибка не пустая — сумма не валидна."""
    raw = raw.strip().lower()
    if raw in ("всё", "все"):
        amount = players.bank_current_balance(player) if for_withdraw else player.get("currency", 0.0)
        if amount <= 0:
            return 0.0, "Сумма для операции получилась нулевой."
        return amount, ""
    try:
        amount = float(raw.replace(",", "."))
    except ValueError:
        return 0.0, "не_число"
    if amount <= 0:
        return 0.0, "Сумма должна быть положительной."
    return amount, ""


async def _do_deposit(message: Message, player: dict, amount: float) -> None:
    if player.get("currency", 0.0) < amount:
        await message.reply(f"Недостаточно Те на руках (есть {player.get('currency', 0):.2f})." + HINT_TEXT)
        return
    players.bank_settle(player)  # фиксируем накопленные проценты в общий счётчик, не теряем их
    bank = player["bank"]
    player["currency"] = round(player["currency"] - amount, 2)
    bank["balance"] = round(bank["balance"] + amount, 2)
    bank["deposited_at"] = now()
    await message.reply(
        f"✅ Внесено {amount:.2f} Те. Остаток на вкладе: {bank['balance']:.2f} Те." + HINT_TEXT
    )


async def _do_withdraw(message: Message, player: dict, amount: float) -> None:
    players.bank_settle(player)  # фиксируем накопленные проценты в общий счётчик, не теряем их
    bank = player["bank"]
    if amount > bank["balance"]:
        await message.reply(f"На вкладе только {bank['balance']:.2f} Те." + HINT_TEXT)
        return
    remaining = round(bank["balance"] - amount, 2)
    bank["balance"] = remaining
    bank["deposited_at"] = now() if remaining > 0 else None
    player["currency"] = round(player.get("currency", 0.0) + amount, 2)
    await message.reply(
        f"✅ Снято {amount:.2f} Те. На руках теперь: {player['currency']:.2f} Те.\n"
        f"🏦 Осталось на вкладе: {remaining:.2f} Те." + HINT_TEXT
    )


async def _handle_bank_request(message: Message, state: FSMContext, action_text: str) -> None:
    """action_text — всё, что идёт после слова 'банк'/'/bank' (может быть пустым)."""
    parts = action_text.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else ""
    amount_raw = parts[1] if len(parts) > 1 else ""

    async with Storage() as db:
        chat_id, error = await resolve_chat_for_message(message, db)
        if error:
            await message.reply(error)
            return
        chat = get_chat(db, chat_id)
        player = players.get_or_create_player(
            chat, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Игрок"
        )

        if action in ("положить", "внести"):
            if not amount_raw:
                await message.reply(_info_text(player) + HINT_TEXT)
                await state.update_data(chat_id=chat_id)
                await state.set_state(BankStates.waiting_deposit_amount)
                return
            amount, error = _parse_amount(amount_raw, player, for_withdraw=False)
            if error == "не_число":
                await message.reply("Сумма должна быть числом (или словом «всё»)." + HINT_TEXT)
                return
            if error:
                await message.reply(error + HINT_TEXT)
                return
            await _do_deposit(message, player, amount)
            return

        if action in ("снять", "вывести"):
            if not amount_raw:
                await message.reply(_info_text(player) + HINT_TEXT)
                await state.update_data(chat_id=chat_id)
                await state.set_state(BankStates.waiting_withdraw_amount)
                return
            amount, error = _parse_amount(amount_raw, player, for_withdraw=True)
            if error == "не_число":
                await message.reply("Сумма должна быть числом (или словом «всё»)." + HINT_TEXT)
                return
            if error:
                await message.reply(error + HINT_TEXT)
                return
            await _do_withdraw(message, player, amount)
            return

        # без аргументов (или нераспознанное слово) — просто показать информацию
        # и сразу спросить, сколько внести, чтобы не нужно было звать команду снова
        await message.reply(_info_text(player) + HINT_TEXT)
        await state.update_data(chat_id=chat_id)
        await state.set_state(BankStates.waiting_deposit_amount)


@router.message(Command("bank"))
async def cmd_bank(message: Message, state: FSMContext) -> None:
    args = message.text.split(maxsplit=1)
    action_text = args[1] if len(args) > 1 else ""
    await _handle_bank_request(message, state, action_text)


@router.message(F.text.regexp(r"(?i)^банк(\s|$)"))
async def text_bank_trigger(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    action_text = text[4:].strip()  # всё, что после слова "банк"
    await _handle_bank_request(message, state, action_text)


@router.message(StateFilter(BankStates.waiting_deposit_amount))
async def process_deposit_amount(message: Message, state: FSMContext) -> None:
    if await check_command_escape(message, state):
        return
    data = await state.get_data()
    chat_id = data.get("chat_id")
    async with Storage() as db:
        chat = get_chat(db, chat_id)
        player = players.get_or_create_player(
            chat, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Игрок"
        )
        # позволяем прямо здесь переключиться на снятие словом "снять ..."
        raw = (message.text or "").strip()
        low = raw.lower()
        if low.startswith("снять") or low.startswith("вывести"):
            rest = raw.split(maxsplit=1)
            amount_raw = rest[1] if len(rest) > 1 else ""
            amount, error = _parse_amount(amount_raw, player, for_withdraw=True)
            if error == "не_число" or not amount_raw:
                await state.clear()
                await message.reply("Не похоже на сумму — запрос отменён." + HINT_TEXT)
                return
            if error:
                await state.clear()
                await message.reply(error + HINT_TEXT)
                return
            await _do_withdraw(message, player, amount)
            await state.clear()
            return

        amount, error = _parse_amount(message.text or "", player, for_withdraw=False)
        if error == "не_число":
            await state.clear()
            await message.reply("Не похоже на число — запрос на внесение отменён." + HINT_TEXT)
            return
        if error:
            await state.clear()
            await message.reply(error + HINT_TEXT)
            return
        await _do_deposit(message, player, amount)
    await state.clear()


@router.message(StateFilter(BankStates.waiting_withdraw_amount))
async def process_withdraw_amount(message: Message, state: FSMContext) -> None:
    if await check_command_escape(message, state):
        return
    data = await state.get_data()
    chat_id = data.get("chat_id")
    async with Storage() as db:
        chat = get_chat(db, chat_id)
        player = players.get_or_create_player(
            chat, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Игрок"
        )
        amount, error = _parse_amount(message.text or "", player, for_withdraw=True)
        if error == "не_число":
            await state.clear()
            await message.reply("Не похоже на число — запрос на снятие отменён." + HINT_TEXT)
            return
        if error:
            await state.clear()
            await message.reply(error + HINT_TEXT)
            return
        await _do_withdraw(message, player, amount)
    await state.clear()
