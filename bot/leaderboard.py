# -*- coding: utf-8 -*-
"""Построение текста таблицы лидеров — общее для /top и итогов сезона."""

from bot.clan_utils import ensure_clan_fields
from bot.leveling import clan_prefix

MEDALS = ["🥇", "🥈", "🥉"]


def build_top_text(db: dict, title: str, declare_winner: bool = False) -> str:
    clans = list(db["clans"].values())
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

    lines.append("🏰 <b>Топ кланов по очкам:</b>")
    for i, clan in enumerate(clans_sorted[:10]):
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
