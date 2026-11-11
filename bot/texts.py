# -*- coding: utf-8 -*-
"""Тексты сообщений. HTML-разметка (parse_mode=HTML), <blockquote> — для "цитируемых" блоков."""

import html
from typing import Optional

import config
from config import LOSS_MULTIPLIER, TOTAL_CELLS


def fmt_num(x: float) -> str:
    """Число с разделением тысяч пробелом и запятой вместо точки для дробной
    части (русская типографика): 151728 -> '151 728', 3450.5 -> '3 450,5'."""
    x = round(float(x), 2)
    sign = "−" if x < 0 else ""
    x = abs(x)
    if x == int(x):
        int_part, frac = str(int(x)), ""
    else:
        s = f"{x:.2f}".rstrip("0").rstrip(".")
        if "." in s:
            int_part, frac_digits = s.split(".")
            frac = "," + frac_digits
        else:
            int_part, frac = s, ""
    grouped = f"{int(int_part):,}".replace(",", " ")
    return f"{sign}{grouped}{frac}"


def fmt_signed_num(x: float) -> str:
    """Как fmt_num, но всегда с явным знаком (+123 / −123 / 0)."""
    if x > 0:
        return f"+{fmt_num(x)}"
    return fmt_num(x)  # fmt_num уже подставляет "−" для отрицательных


def fmt_mult(x: float) -> str:
    """x2,30 — множитель в русской типографике."""
    return f"x{x:.2f}".replace(".", ",")


def _e(value) -> str:
    """Экранирование пользовательских строк (названия кланов, имена) для parse_mode=HTML."""
    return html.escape(str(value), quote=False)


def duel_invite_text(name_a: str, name_b: str) -> str:
    return (
        "⚔️ <b>Вызов на дуэль чести!</b>\n\n"
        f"{name_a} и {name_b} вызваны на дуэль за честь и славу своего клана!\n"
        "На кону — очки клана.\n\n"
        "Любому из вас двоих достаточно нажать /minduel — дуэль откроется "
        "сразу для обоих, второй раз жать команду не нужно."
    )


def duel_invite_text_silent(name_a: str, name_b: str) -> str:
    """Короткий вариант для тихого режима (/tixa) — без длинных пояснений."""
    return f"⚔️ {name_a} и {name_b} вызваны на дуэль. Напишите в ЛС боту «начать», чтобы вызвать поле."


def duel_rules_text(clan_a_name: str, clan_a_points: float, clan_b_name: str, clan_b_points: float) -> str:
    example_a = round(clan_a_points * LOSS_MULTIPLIER) if clan_a_points > 0 else "меньше, чем было"
    example_b = round(clan_b_points * LOSS_MULTIPLIER) if clan_b_points > 0 else "меньше, чем было"

    rules = (
        "<blockquote>"
        "📜 <b>Правила дуэли</b>\n"
        "Поле 5×5. Вы сами выбираете, сколько мин на нём спрятано (от 1 до 6) — "
        "чем больше мин, тем быстрее растёт множитель выигрыша.\n\n"
        f"💰 Ставка — это ВСЕ текущие очки вашего клана. Выигрыш = ставка × множитель.\n"
        f"«{clan_a_name}»: ставка {clan_a_points:g} Al.\n"
        f"«{clan_b_name}»: ставка {clan_b_points:g} Al.\n\n"
        f"💣 Если попадаете на мину — очки вашего клана умножаются на {LOSS_MULTIPLIER}, "
        "и продолжить дуэль нельзя.\n"
        f"Пример: у клана «{clan_a_name}» сейчас {clan_a_points:g} очков → после подрыва станет {example_a}.\n"
        f"У клана «{clan_b_name}» сейчас {clan_b_points:g} очков → после подрыва станет {example_b}.\n\n"
        "✅ В любой момент до подрыва можно нажать «Забрать очки» и зафиксировать текущий выигрыш."
        "</blockquote>"
    )
    return rules


def mines_progressions_block(progressions: dict) -> str:
    lines = ["<blockquote>", "📊 <b>Прогрессия множителей по количеству мин:</b>"]
    for mines, text in progressions.items():
        lines.append(f"{mines}️⃣ мин{'а' if mines == 1 else ('ы' if mines < 5 else '')}: {text}")
    lines.append("</blockquote>")
    return "\n".join(lines)


def choose_mines_prompt() -> str:
    return (
        "\n👇 Оба игрока выбирают количество мин здесь же, каждый — своё "
        "(нажатия видны только вашему полю, второй игрок не помешает):"
    )


def board_header(mines: int, opened: int, current_multiplier: float, next_progression: str, stake_al: int, boost: float = 1.0) -> str:
    """Шапка поля. Главное — число, которое сейчас «на кону» (выигрыш), и риск
    мины на следующем клике: именно они создают напряжение, остальное — мелким шрифтом."""
    remaining = TOTAL_CELLS - opened
    risk = round(mines / remaining * 100) if remaining > 0 else 100
    if opened == 0:
        hero = f"🎯 Откройте первую клетку — риск мины <b>{risk}%</b>"
    else:
        win_al = round(stake_al * current_multiplier)
        hero = (
            f"💰 <b>{fmt_mult(current_multiplier)}</b> · <b>{fmt_num(win_al)} Al</b>\n"
            f"⚠️ Риск мины на следующем клике: <b>{risk}%</b>"
        )
    info = f"💣 Мин: {mines} · 💸 Ставка: {fmt_num(stake_al)} Al"
    if boost != 1.0:
        info += f" · 🌀 {fmt_mult(boost)}"
    return f"{hero}\n{info}\n\n<blockquote>🧮 Дальше: {next_progression}</blockquote>"


def portal_text(old_multiplier: float, new_boost: float) -> str:
    return (
        f"🌀 <b>Портал!</b> Множитель x{old_multiplier:.2f}".replace(".", ",") + " полностью сгорает.\n"
        f"Новое поле! Усиление поля теперь: x{new_boost:.2f}".replace(".", ",")
    )


def lose_text(clan_name: str, old_points: float, new_points: float, possible_multiplier: float, possible_al: int, applied_mult: float) -> str:
    deducted = round(old_points - new_points, 2)
    return (
        "💥 <b>Бум! Вы подорвались на мине.</b>\n"
        f"Если бы забрали сейчас: x{possible_multiplier:.2f}".replace(".", ",") + f" ({possible_al} Al)\n"
        f"Очки клана «{clan_name}» до мины: <b>{old_points:g}</b>\n"
        f"Очки умножены на {applied_mult:g} (списано {deducted:g} очков).\n"
        f"Новые очки клана: <b>{new_points:g}</b>"
    )


def breakdown_base_line(multiplier: float, stake: int, base_al: int) -> str:
    mult_s = f"x{multiplier:.2f}".replace(".", ",")
    return f"🎯 Поле: {mult_s} на {fmt_num(stake)} = {fmt_num(base_al)} очков"


def breakdown_bonus_line(label: str, emoji: str, amount: float) -> str:
    sign = "+" if amount >= 0 else ""
    return f"{emoji} {label} приносит {sign}{fmt_num(amount)} очков"


def breakdown_item_line(label: str, emoji: str, amount: float, success: bool) -> str:
    if success:
        return f"{emoji} {label} сработали! Бонус к очкам: +{fmt_num(amount)} очков"
    return f"{emoji} {label} не выпали — дебаф к очкам: {fmt_num(amount)} очков"


def cashout_text(clan_name: str, breakdown_lines: list, won_al: int, new_points: float) -> str:
    """`breakdown_lines` — уже готовые строки по шагам (база, тактика,
    привилегии, недельные срезы, личные предметы) в фиксированном порядке
    применения бонусов — см. bot/handlers/duel.py::_apply_cashout."""
    text = "✅ <b>Вы забрали выигрыш!</b>\n\n"
    text += "\n".join(breakdown_lines)
    text += f"\n\n💰 Итого очков: <b>{fmt_num(won_al)}</b>"
    text += f"\n🏳️ Очки клана «{clan_name}» обновлены: <b>{fmt_num(new_points)}</b>"
    return text


def attempts_remaining_text(remaining: int) -> str:
    return f"‼️У вас осталось {remaining} попыт{'ка' if remaining == 1 else ('ки' if remaining < 5 else 'ок')}‼️"


def afk_autocashout_text(username: str) -> str:
    return f"⏳ Игрок {username} долго не отвечал — выигрыш автоматически зафиксирован."


def new_achievements_text(keys: list) -> str:
    import config
    lines = ["🏅 <b>Новое достижение!</b>" if len(keys) == 1 else "🏅 <b>Новые достижения!</b>"]
    for key in keys:
        info = config.PLAYER_ACHIEVEMENTS.get(key)
        if info:
            lines.append(f'{info["emoji"]} <b>{info["name"]}</b> — {info["desc"]}')
    return "\n".join(lines)


# ===================================================================== ДУЭЛЬ: АНОНС, ПРАВИЛА, ФИНАЛ ====

SIDE_EMOJI = {"a": "🔵", "b": "🔴"}


def duel_invite_clans_text(name_a: str, name_b: str, clan_a: str, points_a: float,
                           clan_b: str, points_b: float, betting_block: str = "", prefix: str = "") -> str:
    """Анонс дуэли: видно, ЗА КОГО играют (клан + очки), а не только ники."""
    text = (
        "⚔️ <b>Дуэль чести!</b>\n\n"
        f"🔵 <b>«{_e(clan_a)}»</b> · {fmt_num(points_a)} Al\n"
        f"👤 {_e(name_a)}\n\n"
        f"🔴 <b>«{_e(clan_b)}»</b> · {fmt_num(points_b)} Al\n"
        f"👤 {_e(name_b)}\n\n"
        "На кону — очки обоих кланов. Любому из вызванных достаточно нажать /minduel — "
        "дуэль откроется сразу для обоих."
    )
    if betting_block:
        text += "\n\n" + betting_block
    if prefix:
        text = prefix + "\n\n" + text
    return text


def _record_text(info: dict) -> str:
    """«60% побед (12/20)» — на чём основан множитель."""
    played = info.get("played", 0)
    if not played:
        return "ещё без дуэлей"
    won = info.get("won", 0)
    return f"{round(won / played * 100)}% побед ({won}/{played})"


def _bet_side_line(side: str, bets: dict) -> str:
    info, x = bets["info"][side], bets["odds"][side]
    return f"{SIDE_EMOJI[side]} {_e(info['name'])} · {_record_text(info)} → <b>{fmt_mult(x)}</b>"


def betting_block_open(bets: dict) -> str:
    from bot import betting
    lines = [
        "🎰 <b>Тотализатор</b> — ставки открыты до старта дуэли",
        _bet_side_line("a", bets),
        _bet_side_line("b", bets),
    ]
    if betting.has_bets(bets):
        pool = betting.pools(bets)
        lines.append(f"💰 Поставлено: {SIDE_EMOJI['a']} {fmt_num(pool['a'])} Те · {SIDE_EMOJI['b']} {fmt_num(pool['b'])} Те")
    lines.append(
        "<i>Нажмите «Ставка» и ответьте на сообщение бота любой суммой в Те. Множитель зафиксирован: "
        "сколько показано сейчас, столько и получите (ставка × икс). Чем реже игрок выигрывает, "
        "тем выше икс на него.</i>"
    )
    lines.append(
        f"🎟 <b>Бесплатная ставка — {config.BET_COUPON_AMOUNT:g} Те</b>, раз в сутки: с баланса не списывается, "
        "при выигрыше платится прибыль, при проигрыше ничего не теряете."
    )
    return "\n".join(lines)


def betting_block_closed(bets: Optional[dict]) -> str:
    from bot import betting
    if not betting.has_bets(bets):
        return "🎰 Ставок на эту дуэль не было."
    pool, count = betting.pools(bets), betting.counts(bets)
    lines = [f"🔒 <b>Ставки закрыты</b> · поставлено {fmt_num(betting.total_pool(bets))} Те"]
    for side in ("a", "b"):
        info, x = bets["info"][side], bets["odds"][side]
        stake = f"{fmt_num(pool[side])} Те ({count[side]} чел.)" if pool[side] > 0 else "нет ставок"
        lines.append(f"{SIDE_EMOJI[side]} {_e(info['name'])} · {fmt_mult(x)} · {stake}")
    return "\n".join(lines)


def bets_help_text() -> str:
    cut = int(round(config.BET_CHAMPION_CUT * 100))
    margin = int(round(config.BET_MARGIN * 100))
    limit = "Ставка — любая, от {0:g} Те и сколько есть на балансе.".format(config.BET_MIN) if config.BET_MAX is None \
        else f"Ставка — от {config.BET_MIN:g} до {config.BET_MAX:g} Те на одну дуэль."
    return (
        "🎰 <b>Тотализатор</b>\n\n"
        "Когда бот объявляет дуэль, под анонсом можно поставить Те на 🔵 или 🔴 — пока вызванные "
        "игроки не начали дуэль. Нажмите «Ставка» и ответьте на сообщение бота суммой.\n"
        f"{limit} Можно написать «всё».\n\n"
        "📈 <b>Множитель зависит от процента побед игрока в дуэлях</b>: чем реже он выигрывает, "
        "тем выше икс на него. Победа в дуэли — это когда твой множитель выше, чем у соперника "
        "(или соперник подорвался). Икс считается один раз при объявлении дуэли и "
        "<b>не меняется</b>: сколько показано в момент ставки, столько и получите. "
        "Выигрыш = ставка × икс (ставка возвращается внутри выплаты), проигрыш — ставка сгорает.\n"
        f"Новички начинают с 50% побед. В икс заложена маржа {margin}% (как у букмекера), границы — "
        f"x{config.BET_MIN_ODDS:g}…x{config.BET_MAX_ODDS:g}.\n\n"
        f"👑 Игрок, выигравший дуэль, получает {cut}% от наличных ставок на себя.\n"
        f"🎟 Раз в сутки — <b>бесплатная ставка {config.BET_COUPON_AMOUNT:g} Те</b>: с баланса не "
        "списывается, при выигрыше платится только прибыль, при проигрыше ничего не теряете.\n\n"
        "🤝 Ничья или отмена дуэли — все ставки возвращаются.\n"
        "🚫 Играющие ставить не могут; участники дерущихся кланов ставят только на свой клан; "
        "на обе стороны сразу — нельзя."
    )


def duel_rules_compact(clan_a_name: str, clan_a_points: float, clan_b_name: str, clan_b_points: float) -> str:
    """Короткая версия правил — то, что видят в группе по умолчанию. Полные правила — по кнопке."""
    return (
        f"⚔️ <b>«{_e(clan_a_name)}» vs «{_e(clan_b_name)}»</b>\n"
        f"💰 Ставка — все очки клана: {fmt_num(clan_a_points)} Al · {fmt_num(clan_b_points)} Al\n"
        f"💣 Мина: очки × {str(LOSS_MULTIPLIER).replace('.', ',')} · ✅ «Забрать» — в любой момент до подрыва\n"
        "👇 Каждый выбирает число мин (1–6): больше мин — быстрее растёт множитель"
    )


def _side_result_text(side: dict) -> str:
    result, mult = side.get("result"), side.get("mult", 0) or 0
    if result == "win":
        return f"✅ забрал {fmt_mult(mult)}" if mult > 0 else "🏳️ завершил досрочно"
    if result == "loss":
        return "💥 подорвался"
    return "⏳ не пришёл(а)"


def finale_bets_block(s: dict, champion_name: str = "") -> str:
    emo = SIDE_EMOJI
    pa, pb = s["pool"]["a"], s["pool"]["b"]
    head = (
        f"🎰 <b>Тотализатор</b> · поставлено {fmt_num(pa + pb)} Те "
        f"({emo['a']} {fmt_num(pa)} / {emo['b']} {fmt_num(pb)})"
    )
    if s["refunded"]:
        return head + "\nничья — ставки возвращены"

    lines = [head, f"{emo[s['winner_side']]} победа — выплата <b>{fmt_mult(s['mult'])}</b> на каждый поставленный Те"]
    winners = s["winners"]
    if winners:
        shown = [f"{_e(name)} {fmt_signed_num(profit)}" for name, profit in winners[:5]]
        more = f" · …и ещё {len(winners) - 5}" if len(winners) > 5 else ""
        lines.append("🏅 Угадали: " + " · ".join(shown) + more)
    if s["losers"]:
        lost = sum(c for _, c in s["losers"])
        tail = f" (−{fmt_num(lost)} Те)" if lost > 0 else ""
        lines.append(f"😬 Мимо: {len(s['losers'])} чел.{tail}")
    if s["bounty"] > 0 and champion_name:
        lines.append(f"👑 Бонус чемпиона {_e(champion_name)}: +{fmt_num(s['bounty'])} Те")
    return "\n".join(lines)


def duel_finale_text(a: dict, b: dict, winner: Optional[str], streak: int,
                     bet_summary: Optional[dict] = None, champion_name: str = "") -> str:
    """Итоговая карточка дуэли. a/b: {"clan","player","result","mult","points"}.
    В заголовке — названия кланов, а отправляется она ОТВЕТОМ на сообщение этой дуэли,
    поэтому не перепутается с соседними дуэлями."""
    lines = [
        f"🏁 <b>Итог дуэли «{_e(a['clan'])}» vs «{_e(b['clan'])}»</b>",
        "",
        f"🔵 «{_e(a['clan'])}» — {_e(a['player'])}: {_side_result_text(a)}",
        f"🔴 «{_e(b['clan'])}» — {_e(b['player'])}: {_side_result_text(b)}",
        "",
    ]
    if winner in ("a", "b"):
        side = a if winner == "a" else b
        win_line = f"🏆 Победа: <b>«{_e(side['clan'])}»</b>"
        if streak >= 2:
            win_line += f" · серия побед: {streak} 🔥"
        lines.append(win_line)
    else:
        lines.append("🤝 Ничья — победителя нет")
    lines.append(f"Очки кланов: 🔵 {fmt_num(a['points'])} · 🔴 {fmt_num(b['points'])}")
    if bet_summary:
        lines.append("")
        lines.append(finale_bets_block(bet_summary, champion_name))
    return "\n".join(lines)
