# -*- coding: utf-8 -*-
"""
Банк для валюты Те (своя экономика в каждой группе).

ОДНО СООБЩЕНИЕ-ПАНЕЛЬ. /bank присылает ОДНО сообщение с информацией о банке и двумя
кнопками «➕ Положить» / «➖ Снять». Дальше чат не засоряется: любое нажатие кнопки и
любой ввод суммы ИЗМЕНЯЮТ это же сообщение, а не пишут новое.

 - В панели всегда видно: сколько Те на руках, сколько на вкладе (уже С УЧЁТОМ
   накапавших процентов), сколько накапало и сколько начислено за всё время.
 - Нажали «➕ Положить» / «➖ Снять» — то же сообщение просит сумму и показывает кнопки
   «Всё» и «Половина» (с суммами), а также «↩️ Назад».
 - Сумму можно написать в чат: число («50», «50,5», «1 000», «50 Те»), «всё», «половина».
   Работает и «по привычке» — с глаголом: «положить 50», «снять всё», «положить половину»
   (глагол главнее выбранной кнопки). Ответ на сообщение с панелью тоже работает — это
   важно, если у бота включён режим приватности: тогда обычные сообщения в группе он не
   видит, а ответы на свои — видит всегда.
 - Результат операции показывается в той же панели, и кнопки возвращаются.
 - Панель принадлежит тому, кто вызвал /bank: чужие нажатия отклоняются.

Команды без панели-диалога по-прежнему работают (панель приходит уже с результатом):
 - /bank положить СУММА | всё | половина   (то же: «Банк положить 50»)
 - /bank снять СУММА | всё | половина      (то же: «Банк снять всё»)
 - «Банк 50» / «/bank 50» — голая сумма без глагола считается внесением.
 - Снять деньги можно в ЛЮБОЙ момент, без ожидания.

Кнопки не хранят состояние в памяти бота: в callback_data зашиты группа и владелец, поэтому
панели продолжают работать и после перезапуска. В памяти живёт только «ждём сумму от этого
человека» (после перезапуска достаточно снова нажать кнопку).
"""

import html
import math
import re
import time
from typing import Dict, Optional, Tuple

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from bot import players
from bot.chat_state import get_chat, resolve_chat_for_message
from bot.storage import Storage, now

router = Router(name="bank")

_ALL_WORDS = ("всё", "все", "всю", "all")
_HALF_WORDS = ("половина", "половину", "половины", "пополам", "half")
_DEPOSIT_VERBS = ("положить", "внести", "пополнить")
_WITHDRAW_VERBS = ("снять", "вывести")

_WAIT_TTL = 900  # сколько секунд после последнего действия бот «слушает» сумму от человека

# (id_чата_с_панелью, id_человека) -> {"mid": id_сообщения_панели, "game_chat_id": id_группы,
#                                       "mode": "dep" | "wd" | None, "ts": время}
# Живёт в памяти: после перезапуска бота достаточно нажать кнопку на панели ещё раз.
_WAIT: Dict[Tuple[int, int], dict] = {}


# ----------------------------------------------------------------- разбор ---

def _split_verb(raw: str) -> Tuple[str, str]:
    """«положить 50» -> ("положить", "50"); без глагола -> ("", raw)."""
    parts = (raw or "").strip().split(maxsplit=1)
    if parts and parts[0].lower() in _DEPOSIT_VERBS + _WITHDRAW_VERBS:
        return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
    return "", (raw or "").strip()


def _parse_token(raw: str) -> Optional[Tuple[str, float]]:
    """Разбирает сумму: ("all", 0) | ("half", 0) | ("num", число). None — это не сумма.
    Понимает «50», «50,5», «1 000», «50 Те», «всё», «половина»."""
    raw = (raw or "").replace(" ", " ").strip().lower()
    if not raw:
        return None
    if raw in _ALL_WORDS:
        return "all", 0.0
    if raw in _HALF_WORDS:
        return "half", 0.0
    cleaned = re.sub(r"\s*(?:те|te)\.?$", "", raw)                  # «50 Те» -> «50»
    cleaned = re.sub(r"(?<=\d)[ _](?=\d{3}(?:\D|$))", "", cleaned)  # «1 000» -> «1000»
    cleaned = cleaned.replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(amount):
        return None
    return "num", round(amount, 2)


def _resolve_amount(token: Tuple[str, float], available: float, deposit: bool) -> Tuple[float, str]:
    """(сумма, ошибка). «Всё» и «половина» считаются от доступного: Те на руках (внести)
    или текущий вклад с процентами (снять)."""
    kind, value = token
    if kind in ("all", "half") and available <= 0:
        return 0.0, "На руках нет Те — вносить нечего." if deposit else "На вкладе пусто — снимать нечего."
    if kind == "all":
        amount = round(available, 2)
    elif kind == "half":
        amount = round(available / 2, 2)
        if amount <= 0:
            return 0.0, "Половина получилась нулевой — попробуйте «всё»."
    else:
        amount = value
    if amount <= 0:
        return 0.0, "Сумма должна быть положительной."
    return amount, ""


# --------------------------------------------------------------- операции ---

def _apply_deposit(player: dict, amount: float) -> Tuple[bool, str]:
    cash = player.get("currency", 0.0)
    if cash + 0.005 < amount:
        return False, f"Недостаточно Те на руках (есть {cash:.2f})."
    amount = min(amount, cash)  # гасим расхождение в пол-копейки из-за округления
    players.bank_settle(player)  # фиксируем накопленные проценты, не теряем их
    bank = player["bank"]
    player["currency"] = round(cash - amount, 2)
    bank["balance"] = round(bank["balance"] + amount, 2)
    bank["deposited_at"] = now()
    return True, f"✅ Внесено {amount:.2f} Те."


def _apply_withdraw(player: dict, amount: float) -> Tuple[bool, str]:
    if amount > players.bank_current_balance(player) + 0.005:
        return False, f"На вкладе только {players.bank_current_balance(player):.2f} Те."
    players.bank_settle(player)  # фиксируем накопленные проценты, не теряем их
    bank = player["bank"]
    amount = min(amount, bank["balance"])
    remaining = round(bank["balance"] - amount, 2)
    bank["balance"] = remaining
    bank["deposited_at"] = now() if remaining > 0 else None
    player["currency"] = round(player.get("currency", 0.0) + amount, 2)
    return True, f"✅ Снято {amount:.2f} Те."


def _run(player: dict, deposit: bool, token: Tuple[str, float]) -> Tuple[bool, str]:
    """Выполняет операцию. Возвращает (получилось, строка_для_панели)."""
    available = player.get("currency", 0.0) if deposit else players.bank_current_balance(player)
    amount, err = _resolve_amount(token, available, deposit)
    if err:
        return False, "⚠️ " + err
    ok, text = (_apply_deposit if deposit else _apply_withdraw)(player, amount)
    return ok, text if ok else "⚠️ " + text


def _open_mode(player: dict, mode: str) -> Tuple[Optional[str], str]:
    """Можно ли открыть режим ввода суммы: (режим_или_None, сообщение_если_нельзя)."""
    if mode == "dep" and player.get("currency", 0.0) <= 0:
        return None, "⚠️ На руках нет Те — вносить нечего."
    if mode == "wd" and players.bank_current_balance(player) <= 0:
        return None, "⚠️ На вкладе пусто — снимать нечего."
    return mode, ""


# ------------------------------------------------------------------ панель ---

def _display_name(player: dict) -> str:
    return f"@{player['username']}" if player.get("username") else player.get("first_name", "Игрок")


def _panel_text(player: dict, notice: str = "", mode: Optional[str] = None) -> str:
    cash = player.get("currency", 0.0)
    balance = players.bank_current_balance(player)
    principal = player.get("bank", {}).get("balance", 0.0)
    accrued = round(max(balance - principal, 0.0), 2)   # накапало с последней операции
    total = players.bank_total_interest_earned(player)  # накапало за всё время

    lines = [f"🏦 <b>Банк</b> · {html.escape(_display_name(player))}"]
    if notice:
        lines += ["", html.escape(notice)]
    lines += [
        "",
        f"💹 {config.BANK_DAILY_RATE * 100:g}% в день, сложный процент · снять можно в любой момент",
        "",
        f"💰 На руках: {cash:.2f} Те",
        f"🏦 На вкладе: <b>{balance:.2f} Те</b> — с учётом уже накапавших процентов",
    ]
    if balance > 0 or total > 0:
        lines.append(f"📈 Накапало с последней операции: +{accrued:.2f} Те")
        if total > accrued + 0.004:
            lines.append(f"💹 Всего начислено за всё время: {total:.2f} Те")

    if mode == "dep":
        lines += ["", "➕ <b>Сколько положить?</b>",
                  "Напишите число, «всё» или «половина» (можно ответом на это сообщение) — или нажмите кнопку."]
    elif mode == "wd":
        lines += ["", "➖ <b>Сколько снять?</b>",
                  "Напишите число, «всё» или «половина» (можно ответом на это сообщение) — или нажмите кнопку."]
    else:
        lines += ["", "Выберите действие 👇", "<i>Или командой: «Банк положить 50», «Банк снять всё».</i>"]
    return "\n".join(lines)


def _cb(act: str, game_chat_id: int, owner_id: int) -> str:
    return f"bank:{act}:{game_chat_id}:{owner_id}"


def _panel_kb(player: dict, game_chat_id: int, owner_id: int, mode: Optional[str]) -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton
    if mode in ("dep", "wd"):
        deposit = mode == "dep"
        available = player.get("currency", 0.0) if deposit else players.bank_current_balance(player)
        pfx = "dep" if deposit else "wd"
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                btn(text=f"Всё · {available:.2f}", callback_data=_cb(pfx + "all", game_chat_id, owner_id)),
                btn(text=f"Половина · {round(available / 2, 2):.2f}", callback_data=_cb(pfx + "half", game_chat_id, owner_id)),
            ],
            [btn(text="↩️ Назад", callback_data=_cb("menu", game_chat_id, owner_id))],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[[
        btn(text="➕ Положить", callback_data=_cb("dep", game_chat_id, owner_id)),
        btn(text="➖ Снять", callback_data=_cb("wd", game_chat_id, owner_id)),
    ]])


def _remember(panel_chat_id: int, user_id: int, message_id: int, game_chat_id: int, mode: Optional[str]) -> None:
    limit = time.time() - _WAIT_TTL
    for key in [k for k, v in _WAIT.items() if v["ts"] < limit]:
        _WAIT.pop(key, None)
    _WAIT[(panel_chat_id, user_id)] = {
        "mid": message_id, "game_chat_id": game_chat_id, "mode": mode, "ts": time.time(),
    }


async def _edit_panel(bot: Bot, chat_id: int, message_id: int, text: str, kb: InlineKeyboardMarkup) -> bool:
    """True — панель обновлена (или текст не изменился). False — отредактировать нельзя
    (сообщение удалено и т.п.)."""
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML", reply_markup=kb
        )
        return True
    except Exception as e:
        return "not modified" in str(e).lower()


# ---------------------------------------------------- команда /bank, «Банк» ---

async def _handle_bank_request(message: Message, action_text: str) -> None:
    """action_text — всё, что идёт после слова 'банк'/'/bank' (может быть пустым).
    Всегда отвечает ОДНИМ сообщением-панелью (с результатом, если была операция)."""
    parts = action_text.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else ""
    amount_raw = parts[1] if len(parts) > 1 else ""
    user = message.from_user

    async with Storage() as db:
        game_chat_id, error = await resolve_chat_for_message(message, db)
        if error:
            await message.reply(error)
            return
        chat = get_chat(db, game_chat_id)
        player = players.get_or_create_player(chat, user.id, user.username or "", user.first_name or "Игрок")

        notice, mode = "", None
        if action in _DEPOSIT_VERBS + _WITHDRAW_VERBS:
            deposit = action in _DEPOSIT_VERBS
            wanted = "dep" if deposit else "wd"
            if not amount_raw:  # «/bank положить» — сразу спрашиваем сумму
                mode, notice = _open_mode(player, wanted)
            else:
                token = _parse_token(amount_raw)
                if token is None:
                    mode, notice = _open_mode(player, wanted)
                    notice = notice or "⚠️ Сумма должна быть числом, «всё» или «половина»."
                else:
                    ok, notice = _run(player, deposit, token)
                    mode = None if ok else wanted
        elif action_text.strip():
            token = _parse_token(action_text)  # «Банк 50» без глагола — внесение
            if token is not None:
                ok, notice = _run(player, True, token)
                mode = None if ok else "dep"
            else:
                notice = "⚠️ Не понял команду — выберите действие кнопкой ниже."

        text = _panel_text(player, notice, mode)
        kb = _panel_kb(player, game_chat_id, user.id, mode)

    sent = await message.reply(text, reply_markup=kb, parse_mode="HTML")
    if sent is not None and getattr(sent, "message_id", None):
        _remember(message.chat.id, user.id, sent.message_id, game_chat_id, mode)


@router.message(Command("bank"))
async def cmd_bank(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    await _handle_bank_request(message, args[1] if len(args) > 1 else "")


@router.message(F.text.regexp(r"(?i)^банк(\s|$)"))
async def text_bank_trigger(message: Message) -> None:
    await _handle_bank_request(message, message.text.strip()[4:].strip())  # всё, что после слова «банк»


# ----------------------------------------------------------------- кнопки ---

@router.callback_query(F.data.startswith("bank:"))
async def cb_bank(callback: CallbackQuery, bot: Bot) -> None:
    try:
        _, act, game_chat_s, owner_s = callback.data.split(":")
        game_chat_id, owner_id = int(game_chat_s), int(owner_s)
    except ValueError:
        await callback.answer()
        return
    if callback.message is None:
        await callback.answer()
        return
    user = callback.from_user
    if user.id != owner_id:
        await callback.answer("Это банк другого игрока — вызовите /bank сами.", show_alert=True)
        return

    toast: Optional[str] = None
    async with Storage() as db:
        chat = get_chat(db, game_chat_id)
        player = players.get_or_create_player(chat, user.id, user.username or "", user.first_name or "Игрок")
        notice, mode = "", None

        if act in ("dep", "wd"):
            mode, why = _open_mode(player, act)
            if mode is None:  # вносить/снимать нечего — остаёмся в обычном виде и объясняем всплывающим окном
                toast = why.replace("⚠️ ", "")
        elif act in ("depall", "dephalf", "wdall", "wdhalf"):
            deposit = act.startswith("dep")
            ok, notice = _run(player, deposit, ("all" if act.endswith("all") else "half", 0.0))
            mode = None if ok else ("dep" if deposit else "wd")
        elif act != "menu":
            await callback.answer()
            return

        text = _panel_text(player, notice, mode)
        kb = _panel_kb(player, game_chat_id, user.id, mode)

    panel_chat_id, panel_mid = callback.message.chat.id, callback.message.message_id
    if not await _edit_panel(bot, panel_chat_id, panel_mid, text, kb):
        await callback.answer("Это сообщение устарело — вызовите /bank заново.", show_alert=True)
        return
    _remember(panel_chat_id, user.id, panel_mid, game_chat_id, mode)
    await callback.answer(toast, show_alert=bool(toast))


# ------------------------------------------------- сумма текстом в чат ------

def _match_input(message: Message) -> Optional[dict]:
    """Это сумма для открытой панели банка? Возвращает разобранный ввод или None.
    Строго: болтовня в чате, ответы на чужие сообщения (например, на приглашение ставки)
    и голые числа без выбранной кнопки сюда не попадают."""
    if message.from_user is None or not message.text:
        return None
    wait = _WAIT.get((message.chat.id, message.from_user.id))
    if not wait or time.time() - wait["ts"] > _WAIT_TTL:
        return None
    reply = message.reply_to_message
    if reply is not None and getattr(reply, "forum_topic_created", None) is None and reply.message_id != wait["mid"]:
        return None  # ответ на чужое сообщение
    text = message.text.strip()
    if text.startswith("/"):
        return None
    verb, rest = _split_verb(text)
    token = _parse_token(rest)
    if token is None:
        return None
    if not verb and not wait["mode"]:
        return None  # голая сумма, а кнопка не нажата — это просто сообщение в чате
    return {"wait": wait, "verb": verb, "token": token}


async def _is_bank_input(message: Message) -> bool:
    return _match_input(message) is not None


@router.message(F.text, _is_bank_input)
async def on_bank_text(message: Message, bot: Bot) -> None:
    info = _match_input(message)
    if info is None:
        return
    wait, verb, token = info["wait"], info["verb"], info["token"]
    deposit = (verb in _DEPOSIT_VERBS) if verb else (wait["mode"] == "dep")  # глагол главнее кнопки
    wanted = "dep" if deposit else "wd"
    user = message.from_user

    async with Storage() as db:
        chat = get_chat(db, wait["game_chat_id"])
        player = players.get_or_create_player(chat, user.id, user.username or "", user.first_name or "Игрок")
        ok, notice = _run(player, deposit, token)
        mode = None if ok else wanted
        text = _panel_text(player, notice, mode)
        kb = _panel_kb(player, wait["game_chat_id"], user.id, mode)

    if await _edit_panel(bot, message.chat.id, wait["mid"], text, kb):
        _remember(message.chat.id, user.id, wait["mid"], wait["game_chat_id"], mode)
        return
    # панель удалили — присылаем новую
    sent = await message.reply(text, reply_markup=kb, parse_mode="HTML")
    if sent is not None and getattr(sent, "message_id", None):
        _remember(message.chat.id, user.id, sent.message_id, wait["game_chat_id"], mode)
