# -*- coding: utf-8 -*-
"""Тексты сообщений. HTML-разметка (parse_mode=HTML), <blockquote> — для "цитируемых" блоков."""

from config import LOSS_MULTIPLIER


def duel_invite_text(name_a: str, name_b: str) -> str:
    return (
        "⚔️ <b>Вызов на дуэль чести!</b>\n\n"
        f"{name_a} и {name_b} вызваны на дуэль за честь и славу своего клана!\n"
        "На кону — очки клана.\n\n"
        "Любому из вас двоих достаточно нажать /minduel — дуэль откроется "
        "сразу для обоих, второй раз жать команду не нужно."
    )


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


def board_header(mines: int, opened: int, current_multiplier: float, next_progression: str, stake_al: int) -> str:
    win_al = round(stake_al * current_multiplier)
    header = (
        f"💣 Мин: {mines}\n"
        f"💸 Ставка: {stake_al} Al\n"
    )
    if opened == 0:
        header += "📊 Откройте первую клетку!\n\n"
    else:
        header += (
            f"📊 Выигрыш: x{current_multiplier:.2f}".replace(".", ",") + f" / {win_al} Al\n\n"
        )
    header += "<blockquote>🧮 Следующий множитель:\n" + next_progression + "</blockquote>"
    return header


def lose_text(clan_name: str, old_points: float, new_points: float, possible_multiplier: float, possible_al: int, applied_mult: float) -> str:
    deducted = round(old_points - new_points, 2)
    return (
        "💥 <b>Бум! Вы подорвались на мине.</b>\n"
        f"Если бы забрали сейчас: x{possible_multiplier:.2f}".replace(".", ",") + f" ({possible_al} Al)\n"
        f"Очки клана «{clan_name}» до мины: <b>{old_points:g}</b>\n"
        f"Очки умножены на {applied_mult:g} (списано {deducted:g} очков).\n"
        f"Новые очки клана: <b>{new_points:g}</b>"
    )


def cashout_text(clan_name: str, multiplier: float, won_al: int, new_points: float, weekly_pct: int = 0, base_al: int = None) -> str:
    text = (
        "✅ <b>Вы забрали выигрыш!</b>\n"
        f"Множитель: x{multiplier:.2f}".replace(".", ",") + f" ({won_al} Al)\n"
    )
    if weekly_pct and base_al is not None and base_al != won_al:
        sign = "+" if weekly_pct > 0 else ""
        diff = won_al - base_al
        diff_sign = "+" if diff >= 0 else ""
        text += (
            f"📉 Недельный {'бафф' if weekly_pct > 0 else 'дебафф'} клана: {sign}{weekly_pct}% "
            f"→ без него было бы {base_al} Al ({diff_sign}{diff} Al)\n"
        )
    text += f"Очки клана «{clan_name}» обновлены: <b>{new_points:g}</b>"
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
