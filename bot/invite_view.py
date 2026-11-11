# -*- coding: utf-8 -*-
"""Единое место, где собирается сообщение-анонс дуэли (текст + кнопки ставок).
Им пользуются: объявление дуэли (matchmaking), обработчик ставок
(handlers/betting.py) и старт дуэли (handlers/duel.py, закрытие ставок)."""

from typing import Optional, Tuple

from bot import betting, texts
from bot.keyboards import bet_kb


def invite_view(chat: dict, invite: dict, closed: bool = False) -> Optional[Tuple[str, object]]:
    """Возвращает (текст, клавиатура_или_None). closed=True — ставки закрыты:
    блок показывает итоговые банки, кнопок нет. None — если один из кланов уже удалён."""
    clan_a = chat.get("clans", {}).get(str(invite.get("clan_a_id")))
    clan_b = chat.get("clans", {}).get(str(invite.get("clan_b_id")))
    if not clan_a or not clan_b:
        return None

    bets = invite.get("bets")
    block, markup = "", None
    if bets is not None:
        betting.ensure_odds(chat, invite)  # иксы фиксируются один раз; для старых вызовов — при первом показе
        if closed or not bets.get("open"):
            block = texts.betting_block_closed(bets)
        else:
            block = texts.betting_block_open(bets)
            markup = bet_kb(bets["odds"])

    text = texts.duel_invite_clans_text(
        invite.get("name_a", "Игрок"), invite.get("name_b", "Игрок"),
        clan_a["name"], clan_a.get("points", 0), clan_b["name"], clan_b.get("points", 0),
        betting_block=block, prefix=invite.get("prefix", ""),
    )
    return text, markup
