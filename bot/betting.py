# -*- coding: utf-8 -*-
"""
Тотализатор — ставки зрителей на исход дуэли.

КАК ЭТО РАБОТАЕТ
 * Пока вызов на дуэль висит в группе (до /minduel), любой участник группы, кроме
   двух вызванных игроков, ставит Те на «🔵 А» или «🔴 Б»: нажимает кнопку и пишет
   сумму ответом на сообщение бота. Сумма любая (от config.BET_MIN) и ограничена
   только балансом (опционально — config.BET_MAX). Как только кто-то из вызванных
   начинает дуэль, ставки закрываются: поля игроков в группе видны всем, поздние
   ставки были бы нечестными.

 * МНОЖИТЕЛЬ — ФИКСИРОВАННЫЙ и зависит от процента побед игроков в дуэлях
   (duels_won / duels_played). Считается ОДИН РАЗ при объявлении дуэли и не меняется
   по мере ставок — сколько показано в момент ставки, столько и заплатят:

       r_x = (побед_x + K/2) / (дуэлей_x + K)         — сглаженная доля побед (K = BET_RATE_PRIOR)
       p_a = r_a / (r_a + r_b),   p_b = 1 − p_a       — вероятность победы стороны
       икс_x = (1 − маржа) / p_x                      — в пределах [BET_MIN_ODDS, BET_MAX_ODDS]

   Чем реже игрок выигрывает, тем выше икс на него. Сглаживание нужно новичкам:
   без него «1 победа из 1» дала бы 100%, а «0 из 1» — 0%.

   Маржа (8%) — как у букмекера: сумма «обратных иксов» всегда > 1, поэтому нельзя
   поставить на обе стороны (двумя аккаунтами) и заработать наверняка.

 * Выплата: ставка выиграла → игрок получает ставка × икс (ставка возвращается
   внутри выплаты). Проиграла → ставка сгорает. Ставки не делятся между зрителями:
   противоположной стороной служит сам тотализатор. Поэтому Те здесь не «замкнуты»:
   выигрыши создаются, проигрыши сгорают, а маржа делает баланс в среднем отрицательным.

 * Бонус чемпиона: игрок, выигравший дуэль, получает 5% от НАЛИЧНЫХ ставок на него.

 * Ничья (оба подорвались / равный множитель) и отмена (вызов истёк, клан удалён,
   сезон сброшен) — все ставки возвращаются полностью.

 * Бесплатная ставка («купон», раз в сутки, config.BET_COUPON_AMOUNT): с баланса
   не списывается и при выигрыше не возвращается — платится только прибыль.

 * Защита от сговора: сами играющие не ставят; участники двух дерущихся кланов
   ставят только на СВОЙ клан (нельзя поставить против своих и подстроить подрыв);
   один человек — одна сторона на дуэль.

 РИСК (осознанный): при отсутствии верхнего предела (BET_MAX = None) знакомый вне
 дерущихся кланов может поставить много на соперника игрока, который сознательно
 проиграет. Клан такого игрока теряет очки за подрыв, но Те «печатаются». Если такое
 начнётся — задайте BET_MAX в config.py.

ХРАНЕНИЕ
 chat["pending_invite"]["bets"] → после старта дуэли chat["active_duels"][id]["bets"]:
   {"open": bool, "settled": bool,
    "odds": {"a": float, "b": float},                     # зафиксированные иксы
    "info": {"a": {"name", "won", "played"}, "b": {...}},  # для показа «60% побед (12/20)»
    "entries": {"<user_id>": {"side", "cash", "coupon", "coupon_day", "odds", "name"}}}
 Модуль не зависит от aiogram — только от словарей состояния (легко тестировать).
"""

import math
import re
import time
from typing import Optional, Tuple

import config
from bot import players

SIDES = ("a", "b")
SIDE_EMOJI = {"a": "🔵", "b": "🔴"}

_ALL_WORDS = ("всё", "все", "всю", "all")


def other_side(side: str) -> str:
    return "b" if side == "a" else "a"


def today_key() -> int:
    return int(time.time() // 86400)


# ------------------------------------------------------------ множители -----

def smoothed_rate(won: int, played: int) -> float:
    """Сглаженная доля побед игрока в дуэлях (0..1): новичок стартует с 50%."""
    k = config.BET_RATE_PRIOR
    return (won + k / 2) / (played + k)


def odds_from_rates(rate_a: float, rate_b: float) -> Tuple[float, float]:
    """Множители выплаты (икс_a, икс_b) по долям побед двух игроков."""
    p_a = rate_a / (rate_a + rate_b)
    p_b = 1.0 - p_a

    def x(p: float) -> float:
        raw = (1.0 - config.BET_MARGIN) / p
        return round(min(config.BET_MAX_ODDS, max(config.BET_MIN_ODDS, raw)), 2)

    return x(p_a), x(p_b)


def _duelist_record(chat: dict, user_id: int) -> Tuple[int, int]:
    pl = players.find_player(chat, user_id)
    if not pl:
        return 0, 0
    return int(pl.get("duels_won", 0)), int(pl.get("duels_played", 0))


def new_bets(chat: dict, invite: dict) -> dict:
    """Создаёт тотализатор для нового вызова и СРАЗУ фиксирует множители."""
    bets = {"open": True, "settled": False, "entries": {}}
    ensure_odds(chat, invite, bets)
    return bets


def ensure_odds(chat: dict, invite: dict, bets: Optional[dict] = None) -> dict:
    """Гарантирует, что в bets есть зафиксированные odds/info (нужно и для вызовов,
    созданных до этого обновления). Уже посчитанные иксы НИКОГДА не пересчитываются."""
    bets = bets if bets is not None else invite["bets"]
    if bets.get("odds") and bets.get("info"):
        return bets
    won_a, played_a = _duelist_record(chat, invite["player_a_id"])
    won_b, played_b = _duelist_record(chat, invite["player_b_id"])
    x_a, x_b = odds_from_rates(smoothed_rate(won_a, played_a), smoothed_rate(won_b, played_b))
    bets["odds"] = {"a": x_a, "b": x_b}
    bets["info"] = {
        "a": {"name": invite.get("name_a", "Игрок"), "won": won_a, "played": played_a},
        "b": {"name": invite.get("name_b", "Игрок"), "won": won_b, "played": played_b},
    }
    return bets


# ------------------------------------------------------------ подсчёты ------

def pools(bets: Optional[dict]) -> dict:
    """Сколько Те (наличные + купоны) поставлено на каждую сторону."""
    result = {"a": 0.0, "b": 0.0}
    for e in (bets or {}).get("entries", {}).values():
        result[e["side"]] += e.get("cash", 0.0) + e.get("coupon", 0.0)
    return {k: round(v, 2) for k, v in result.items()}


def counts(bets: Optional[dict]) -> dict:
    result = {"a": 0, "b": 0}
    for e in (bets or {}).get("entries", {}).values():
        result[e["side"]] += 1
    return result


def total_pool(bets: Optional[dict]) -> float:
    p = pools(bets)
    return round(p["a"] + p["b"], 2)


def has_bets(bets: Optional[dict]) -> bool:
    return bool(bets and bets.get("entries"))


# ----------------------------------------------------------- ввод суммы -----

def parse_amount(raw: str, balance: float) -> Tuple[Optional[float], str]:
    """Разбирает сумму, которую человек написал ответом: «25», «2,5», «1 000», «25 Те»,
    «ставлю 25», «всё». Возвращает (сумма, ошибка); ошибка пустая — сумма годится."""
    text = (raw or "").replace("\u00a0", " ").strip().lower()
    text = re.sub(r"^(?:ставлю|ставка|поставить|ставь)\s+", "", text)
    if text in _ALL_WORDS:
        if balance <= 0:
            return None, "На балансе нет Те."
        return round(balance, 2), ""
    text = re.sub(r"\s*(?:те|te)\.?$", "", text)               # «25 Те» -> «25»
    text = re.sub(r"(?<=\d)[ _](?=\d{3}(?:\D|$))", "", text)   # «1 000» -> «1000»
    text = text.replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        return None, "Не понял сумму."
    if not math.isfinite(amount):
        return None, "Не понял сумму."
    amount = round(amount, 2)
    if amount <= 0:
        return None, "Сумма должна быть больше нуля."
    return amount, ""


# ---------------------------------------------------------------- ставка ----

def _member_side(chat: dict, invite: dict, user_id: int) -> Optional[str]:
    """За какую из двух дерущихся сторон болеет человек по клану (или None)."""
    uid = str(user_id)
    for side, clan_key in (("a", "clan_a_id"), ("b", "clan_b_id")):
        clan = chat.get("clans", {}).get(str(invite.get(clan_key)))
        if clan and uid in clan.get("members", {}):
            return side
    return None


def _get_player(chat: dict, user_id: int, name: str = "Игрок") -> dict:
    """Находит профиль игрока, при отсутствии создаёт (имя трогаем только при создании)."""
    pl = players.find_player(chat, user_id)
    if pl is None:
        pl = players.get_or_create_player(chat, user_id, "", name or "Игрок")
    return pl


def check_can_bet(chat: dict, invite: dict, user, side: str) -> Tuple[bool, str]:
    """Можно ли этому человеку ставить на эту сторону (без учёта суммы). Не меняет состояние."""
    bets = invite.get("bets")
    if not bets or not bets.get("open") or bets.get("settled"):
        return False, "Ставки на эту дуэль уже закрыты."
    if side not in SIDES:
        return False, "Неизвестная сторона."
    if user.id in (invite.get("player_a_id"), invite.get("player_b_id")):
        return False, "Вы играете в этой дуэли — ставить нельзя 🙂"
    own_side = _member_side(chat, invite, user.id)
    if own_side and own_side != side:
        return False, "Участники дерущихся кланов ставят только на свой клан."
    entry = bets["entries"].get(str(user.id))
    if entry and entry["side"] != side:
        return False, f"Вы уже поставили на {SIDE_EMOJI[entry['side']]} — на двух сразу нельзя."
    return True, ""


def place_bet(chat: dict, invite: dict, user, side: str, amount) -> Tuple[bool, str]:
    """Принимает ставку. `amount` — число (Те) или "c" (бесплатная ставка-купон).
    Возвращает (успех, короткий текст ответа). Меняет chat (баланс) и invite["bets"].
    Вызывать внутри Storage-лока."""
    ok, why = check_can_bet(chat, invite, user, side)
    if not ok:
        return False, why

    bets = ensure_odds(chat, invite)
    x = bets["odds"][side]
    uid = str(user.id)
    entry = bets["entries"].get(uid)
    player = players.get_or_create_player(chat, user.id, user.username or "", user.first_name or "Игрок")
    new_entry = entry or {"side": side, "cash": 0.0, "coupon": 0.0, "coupon_day": None, "odds": x, "name": ""}

    if amount == "c":
        today = today_key()
        if new_entry.get("coupon"):
            return False, "Бесплатная ставка на эту дуэль уже использована."
        if player.get("free_bet_day") == today:
            return False, "Бесплатная ставка на сегодня уже потрачена — приходите завтра 🎟"
        player["free_bet_day"] = today
        new_entry["coupon"] = float(config.BET_COUPON_AMOUNT)
        new_entry["coupon_day"] = today
        stake = float(config.BET_COUPON_AMOUNT)
        what = f"🎟 Бесплатная ставка {stake:g} Те"
        win = round(stake * (x - 1), 2)           # купон: выплачивается только прибыль
        win_text = f"выигрыш {win:g} Те"
    else:
        stake = round(float(amount), 2)
        if stake < config.BET_MIN:
            return False, f"Минимальная ставка — {config.BET_MIN:g} Те."
        if config.BET_MAX is not None and new_entry.get("cash", 0.0) + stake > config.BET_MAX:
            return False, f"Лимит — {config.BET_MAX:g} Те на одну дуэль."
        balance = player.get("currency", 0.0)
        if balance + 1e-9 < stake:
            return False, (
                f"Не хватает Те: ставка {stake:g}, на балансе {balance:.2f}. "
                "Можно меньше — или взять бесплатную ставку 🎟 (раз в сутки)."
            )
        player["currency"] = round(max(0.0, balance - stake), 2)
        new_entry["cash"] = round(new_entry.get("cash", 0.0) + stake, 2)
        what = f"💸 {stake:g} Те"
        win_text = f"выплата {round(stake * x, 2):g} Те"

    new_entry["name"] = players.display_name(player)
    bets["entries"][uid] = new_entry
    return True, f"Принято: {what} на {SIDE_EMOJI[side]} при x{x:.2f} — при победе {win_text}".replace(".", ",")


# ---------------------------------------------------------------- возврат ---

def refund(chat: dict, bets: Optional[dict]) -> int:
    """Возвращает все ставки полностью (наличные — на баланс, купон — обратно в
    «сегодняшний» лимит). Идемпотентно. Возвращает число вернувшихся ставок."""
    if not bets or bets.get("settled"):
        return 0
    bets["settled"] = True
    bets["open"] = False
    n = 0
    for uid, e in bets.get("entries", {}).items():
        pl = _get_player(chat, int(uid), e.get("name", "Игрок"))
        if e.get("cash"):
            pl["currency"] = round(pl.get("currency", 0.0) + e["cash"], 2)
        if e.get("coupon") and pl.get("free_bet_day") == e.get("coupon_day"):
            pl["free_bet_day"] = None
        n += 1
    return n


def refund_chat_bets(chat: dict) -> int:
    """Возвращает ставки по ВСЕМ незавершённым дуэлям группы (вызов + идущие дуэли).
    Нужно перед любым сбросом состояния: конец сезона, /resetwar."""
    n = 0
    invite = chat.get("pending_invite")
    if invite:
        n += refund(chat, invite.get("bets"))
    for duel in chat.get("active_duels", {}).values():
        n += refund(chat, duel.get("bets"))
    return n


# ---------------------------------------------------------------- расчёт ----

def settle(chat: dict, bets: Optional[dict], winner_side: Optional[str],
           winner_player_id: Optional[int] = None) -> Optional[dict]:
    """Рассчитывает ставки по итогам дуэли. winner_side — "a"/"b" или None (ничья).
    Возвращает сводку для итоговой карточки либо None, если ставок не было / уже
    рассчитано. Вызывать внутри Storage-лока."""
    if not bets or bets.get("settled"):
        return None
    entries = bets.get("entries", {})
    if not entries:
        bets["settled"] = True
        bets["open"] = False
        return None

    summary = {
        "pool": pools(bets), "count": counts(bets), "winner_side": winner_side,
        "refunded": False, "reason": None, "mult": None, "bounty": 0.0,
        "winners": [], "losers": [],
    }

    # ничья — или (редкий переходный случай) вызов создан старой версией без иксов
    if winner_side not in SIDES or not bets.get("odds"):
        refund(chat, bets)
        summary.update(refunded=True, reason="draw")
        return summary

    x = bets["odds"][winner_side]
    summary["mult"] = x
    cash_on_winner = 0.0

    for uid, e in entries.items():
        pl = _get_player(chat, int(uid), e.get("name", "Игрок"))
        name = e.get("name") or "Игрок"
        cash, coupon = e.get("cash", 0.0), e.get("coupon", 0.0)
        if e["side"] == winner_side:
            ex = e.get("odds") or x
            payout = round(cash * ex + coupon * (ex - 1), 2)
            pl["currency"] = round(pl.get("currency", 0.0) + payout, 2)
            profit = round(payout - cash, 2)
            pl["bets_won"] = pl.get("bets_won", 0) + 1
            pl["bet_profit_total"] = round(pl.get("bet_profit_total", 0.0) + profit, 2)
            summary["winners"].append((name, profit))
            cash_on_winner += cash
        else:
            pl["bets_lost"] = pl.get("bets_lost", 0) + 1
            pl["bet_profit_total"] = round(pl.get("bet_profit_total", 0.0) - cash, 2)
            summary["losers"].append((name, cash))

    bounty = round(config.BET_CHAMPION_CUT * cash_on_winner, 2)
    if bounty > 0 and winner_player_id is not None:
        champ = _get_player(chat, int(winner_player_id))
        champ["currency"] = round(champ.get("currency", 0.0) + bounty, 2)
        summary["bounty"] = bounty

    summary["winners"].sort(key=lambda t: -t[1])
    bets["settled"] = True
    bets["open"] = False
    return summary
