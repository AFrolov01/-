# -*- coding: utf-8 -*-
"""Тексты сообщений. HTML-разметка (parse_mode=HTML), <blockquote> — для "цитируемых" блоков."""

from config import LOSS_MULTIPLIER


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
    win_al = round(stake_al * current_multiplier)
    header = (
        f"💣 Мин: {mines}\n"
        f"💸 Ставка: {stake_al} Al\n"
    )
    if boost != 1.0:
        header += f"🌀 Усиление поля: x{boost:.2f}".replace(".", ",") + "\n"
    if opened == 0:
        header += "📊 Откройте первую клетку!\n\n"
    else:
        header += (
            f"📊 Выигрыш: x{current_multiplier:.2f}".replace(".", ",") + f" / {win_al} Al\n\n"
        )
    header += "<blockquote>🧮 Следующий множитель:\n" + next_progression + "</blockquote>"
    return header


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
