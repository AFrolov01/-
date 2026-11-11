# -*- coding: utf-8 -*-
"""
Банк для валюты Те (своя экономика в каждой группе).

ВВОД СУММЫ (исправлено): после /bank бот просит сумму и показывает поле ответа
(ForceReply) — ответ на сообщение бота доходит до него даже в группе с включённым
режимом приватности. Сумма понимается в любом разумном виде: «50», «50,5», «1 000»,
«50 Те», «положить 50», «снять 50», «всё». Раньше подсказка просила писать
«положить СУММА», а режим ожидания принимал только голое число — из-за этого ввод
отменялся, а следующее число никто не слушал. Теперь при непонятной сумме бот
переспрашивает (до 3 раз), а не отменяет сразу.

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

import math
import re
from typing import Tuple

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ForceReply

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
    "\n\n💬 Положить: «/bank положить 50» или «/bank», затем ответьте суммой. "
    "Снять: «/bank снять 50». Вместо суммы можно написать «всё»."
)

PROMPT_DEPOSIT = (
    "\n\n💬 <b>Сколько Те внести?</b> Ответьте на это сообщение суммой — например "
    "<code>50</code> — или словом «всё». Чтобы снять со вклада, напишите «снять 50»."
)
PROMPT_WITHDRAW = (
    "\n\n💬 <b>Сколько Те снять?</b> Ответьте на это сообщение суммой — например "
    "<code>50</code> — или словом «всё»."
)
MAX_TRIES = 3  # сколько раз бот переспрашивает сумму, прежде чем отменить ввод

_ALL_WORDS = ("всё", "все", "всю", "all")
_DEPOSIT_VERBS = ("положить", "внести", "пополнить")
_WITHDRAW_VERBS = ("снять", "вывести")


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


def _split_verb(raw: str) -> Tuple[str, str]:
    """«положить 50» -> ("положить", "50"); без глагола -> ("", raw)."""
    parts = raw.strip().split(maxsplit=1)
    if parts and parts[0].lower() in _DEPOSIT_VERBS + _WITHDRAW_VERBS:
        return parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    return "", raw.strip()


def _parse_amount(raw: str, player: dict, for_withdraw: bool) -> Tuple[float, str]:
    """Возвращает (сумма, ошибка). Если ошибка не пустая — сумма не валидна.
    Понимает: «50», «50,5», «1 000», «50 Те», «всё»."""
    raw = (raw or "").replace("\u00a0", " ").strip().lower()
    if raw in _ALL_WORDS:
        amount = players.bank_current_balance(player) if for_withdraw else player.get("currency", 0.0)
        if amount <= 0:
            return 0.0, "Сумма для операции получилась нулевой."
        return amount, ""
    cleaned = re.sub(r"\s*(?:те|te)\.?$", "", raw)            # «50 Те» -> «50»
    cleaned = re.sub(r"(?<=\d)[ _](?=\d{3}(?:\D|$))", "", cleaned)  # «1 000» -> «1000»
    cleaned = cleaned.replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError:
        return 0.0, "не_число"
    if not math.isfinite(amount):
        return 0.0, "не_число"
    amount = round(amount, 2)
    if amount <= 0:
        return 0.0, "Сумма должна быть положительной."
    return amount, ""


async def _do_deposit(message: Message, player: dict, amount: float) -> bool:
    """True — вклад внесён; False — отказ (сообщение пользователю уже отправлено)."""
    if player.get("currency", 0.0) + 0.005 < amount:
        await message.reply(f"Недостаточно Те на руках (есть {player.get('currency', 0):.2f})." + HINT_TEXT)
        return False
    amount = min(amount, player.get("currency", 0.0))  # гасим расхождение в пол-копейки из-за округления
    players.bank_settle(player)  # фиксируем накопленные проценты в общий счётчик, не теряем их
    bank = player["bank"]
    player["currency"] = round(player["currency"] - amount, 2)
    bank["balance"] = round(bank["balance"] + amount, 2)
    bank["deposited_at"] = now()
    await message.reply(
        f"✅ Внесено {amount:.2f} Те. Остаток на вкладе: {bank['balance']:.2f} Те." + HINT_TEXT
    )
    return True


async def _do_withdraw(message: Message, player: dict, amount: float) -> bool:
    """True — деньги сняты; False — отказ (сообщение пользователю уже отправлено)."""
    players.bank_settle(player)  # фиксируем накопленные проценты в общий счётчик, не теряем их
    bank = player["bank"]
    if amount > bank["balance"] + 0.005:
        await message.reply(f"На вкладе только {bank['balance']:.2f} Те." + HINT_TEXT)
        return False
    amount = min(amount, bank["balance"])
    remaining = round(bank["balance"] - amount, 2)
    bank["balance"] = remaining
    bank["deposited_at"] = now() if remaining > 0 else None
    player["currency"] = round(player.get("currency", 0.0) + amount, 2)
    await message.reply(
        f"✅ Снято {amount:.2f} Те. На руках теперь: {player['currency']:.2f} Те.\n"
        f"🏦 Осталось на вкладе: {remaining:.2f} Те." + HINT_TEXT
    )
    return True


async def _ask_amount(message: Message, state: FSMContext, chat_id: int, player: dict, deposit: bool) -> None:
    """Показывает баланс и просит сумму. ForceReply открывает поле ответа на это сообщение:
    такие ответы бот получает всегда, даже в группе с включённым режимом приватности."""
    await message.reply(
        _info_text(player) + (PROMPT_DEPOSIT if deposit else PROMPT_WITHDRAW),
        reply_markup=ForceReply(selective=True, input_field_placeholder="Сумма, например 50, или «всё»"),
    )
    await state.update_data(chat_id=chat_id, tries=0)
    await state.set_state(BankStates.waiting_deposit_amount if deposit else BankStates.waiting_withdraw_amount)


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
                await _ask_amount(message, state, chat_id, player, deposit=True)
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
                await _ask_amount(message, state, chat_id, player, deposit=False)
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

        # «Банк 50» / «/bank всё» — голая сумма без глагола тоже считается внесением
        if action_text.strip():
            amount, err = _parse_amount(action_text, player, for_withdraw=False)
            if not err:
                await _do_deposit(message, player, amount)
                return

        # без аргументов (или нераспознанное слово) — просто показать информацию
        # и сразу спросить, сколько внести, чтобы не нужно было звать команду снова
        await _ask_amount(message, state, chat_id, player, deposit=True)


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


async def _reprompt(message: Message, state: FSMContext, deposit: bool, why: str) -> None:
    """Сумма не распознана — не отменяем сразу, а переспрашиваем (до MAX_TRIES раз)."""
    data = await state.get_data()
    tries = data.get("tries", 0) + 1
    if tries >= MAX_TRIES:
        await state.clear()
        await message.reply(why + " Ввод отменён — наберите /bank ещё раз." + HINT_TEXT)
        return
    await state.update_data(tries=tries)
    await message.reply(
        why + " Напишите число — например <code>50</code> — или «всё».",
        reply_markup=ForceReply(selective=True, input_field_placeholder="Сумма, например 50, или «всё»"),
    )


async def _process_amount(message: Message, state: FSMContext, deposit: bool) -> None:
    """Общий разбор ответа с суммой. Внутри режима «внести» понимает и «снять 50»,
    внутри режима «снять» — и «положить 50»; лишние слова («Те», пробелы в числе) не мешают."""
    if await check_command_escape(message, state):
        return
    data = await state.get_data()
    chat_id = data.get("chat_id")
    verb, rest = _split_verb(message.text or "")
    if verb in _WITHDRAW_VERBS:
        deposit_op = False
    elif verb in _DEPOSIT_VERBS:
        deposit_op = True
    else:
        deposit_op = deposit
    if verb and not rest:
        await _reprompt(message, state, deposit_op, "Не вижу суммы.")
        return

    async with Storage() as db:
        chat = get_chat(db, chat_id)
        player = players.get_or_create_player(
            chat, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Игрок"
        )
        amount, error = _parse_amount(rest, player, for_withdraw=not deposit_op)
        if error:
            done = None
        else:
            done = await (_do_deposit if deposit_op else _do_withdraw)(message, player, amount)

    if error:
        await _reprompt(message, state, deposit_op, "Не понял сумму." if error == "не_число" else error)
    elif done:
        await state.clear()
    else:  # не хватает денег — отказ уже показан, даём попробовать другую сумму
        await _reprompt(message, state, deposit_op, "Давайте другую сумму.")


@router.message(StateFilter(BankStates.waiting_deposit_amount))
async def process_deposit_amount(message: Message, state: FSMContext) -> None:
    await _process_amount(message, state, deposit=True)


@router.message(StateFilter(BankStates.waiting_withdraw_amount))
async def process_withdraw_amount(message: Message, state: FSMContext) -> None:
    await _process_amount(message, state, deposit=False)
