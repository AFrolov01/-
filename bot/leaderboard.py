# -*- coding: utf-8 -*-
"""Построение текста таблицы лидеров — общее для /top (локальный) и /globaltop (по всем группам)."""

from bot.clan_utils import ensure_clan_fields
from bot.leveling import clan_prefix

MEDALS = ["🥇", "🥈", "🥉"]
TOP_SIZE = 25


def build_top_text(chat: dict, title: str, declare_winner: bool = False) -> str:
    """`chat` — состояние ОДНОЙ группы (см. bot/chat_state.py)."""
    clans = list(chat["clans"].values())
    if not clans:
        return f"{title}\n\nПока нет ни одного клана."
    for clan in clans:
        ensure_clan_fields(clan)

    clans_sorted = sorted(clans, key=lambda c: c.get("points", 0), reverse=True)
    lines = [title, ""]

    if declare_winner:
        winner = clans_sorted[0]
        lines.append(
            f"👑 <b>Победитель сезона: {clan_prefix(winner)} «{winner['name']}»</b> "
            f"({winner.get('points', 0):g} очков)"
        )
        lines.append("")

    lines.append(f"🏰 <b>Топ кланов группы (до {TOP_SIZE}):</b>")
    for i, clan in enumerate(clans_sorted[:TOP_SIZE]):
        medal = MEDALS[i] if i < 3 else f"{i + 1}."
        lines.append(
            f"{medal} {clan_prefix(clan)} «{clan['name']}» — {clan.get('points', 0):g} очков "
            f"(побед: {clan.get('wars_won', 0)}, серия: {clan.get('current_win_streak', 0)})"
        )

    best_records = []
    for clan in clans:
        best = clan.get("best_single_multiplier")
        if best:
            best_records.append((clan["name"], best))
    best_records.sort(key=lambda x: x[1].get("value", 0), reverse=True)

    if best_records:
        lines.append("")
        lines.append("💎 <b>Топ игроков по множителю за один бой:</b>")
        for i, (clan_name, rec) in enumerate(best_records[:10]):
            medal = MEDALS[i] if i < 3 else f"{i + 1}."
            player = f'@{rec["username"]}' if rec.get("username") else "игрок"
            mult = f'x{rec["value"]:.2f}'.replace(".", ",")
            lines.append(f"{medal} {player} ({clan_name}) — {mult}")

    return "\n".join(lines)


def build_global_top_text(db: dict) -> str:
    """Топ-25 кланов среди ВСЕХ групп, где используется бот."""
    entries = []  # (clan, group_title)
    for chat_id_str, chat in db.get("chats", {}).items():
        group_title = chat.get("title") or f"группа {chat_id_str}"
        for clan in chat.get("clans", {}).values():
            ensure_clan_fields(clan)
            entries.append((clan, group_title))

    if not entries:
        return "🌍 <b>Общемировой топ кланов</b>\n\nПока нет ни одного клана ни в одной группе."

    entries.sort(key=lambda e: e[0].get("points", 0), reverse=True)
    lines = ["🌍 <b>Общемировой топ кланов (среди всех групп)</b>", ""]
    for i, (clan, group_title) in enumerate(entries[:TOP_SIZE]):
        medal = MEDALS[i] if i < 3 else f"{i + 1}."
        lines.append(
            f"{medal} {clan_prefix(clan)} «{clan['name']}» ({group_title}) — "
            f"{clan.get('points', 0):g} очков"
        )
    return "\n".join(lines)
