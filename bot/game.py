# -*- coding: utf-8 -*-
"""
Игровая математика "мин-дуэли":
 - расчёт множителя выигрыша в зависимости от количества мин и числа
   уже открытых безопасных клеток (та самая прогрессия x1.05 -> x1.15 -> ...)
 - генерация позиций мин на поле 5х5
"""

import random
from typing import List

from config import TOTAL_CELLS, HOUSE_EDGE


def multiplier_for(mines: int, opened_safe_cells: int) -> float:
    """
    Множитель после того, как открыто `opened_safe_cells` безопасных клеток подряд,
    при заданном количестве мин на поле (25 клеток всего).

    Формула — классическая для игр типа "Mines": произведение обратных вероятностей
    вытянуть безопасную клетку на каждом шаге, домноженное на HOUSE_EDGE.
    """
    if opened_safe_cells <= 0:
        return 1.0
    result = 1.0
    for i in range(opened_safe_cells):
        remaining_total = TOTAL_CELLS - i
        remaining_safe = TOTAL_CELLS - mines - i
        if remaining_safe <= 0:
            # физически невозможно (все безопасные клетки уже открыты) — вернём последнее валидное значение
            break
        result *= remaining_total / remaining_safe
    return round(result * HOUSE_EDGE, 2)


def progression_list(mines: int, steps: int = 5, start_from: int = 0) -> List[float]:
    """
    Список следующих `steps` множителей начиная с (start_from+1)-й открытой клетки.
    Используется для отображения "следующий множитель: x1.70 -> x2.30 -> ...".
    """
    max_possible = TOTAL_CELLS - mines
    result = []
    for k in range(start_from + 1, start_from + 1 + steps):
        if k > max_possible:
            break
        result.append(multiplier_for(mines, k))
    return result


def format_progression(values: List[float]) -> str:
    return " ➡️ ".join(f"x{v:.2f}".replace(".", ",") for v in values) + " ➡️ ..."


def generate_mines(mines_count: int) -> List[int]:
    """Возвращает список индексов клеток (0..24), где спрятаны мины."""
    return random.sample(range(TOTAL_CELLS), mines_count)


def cell_to_rc(index: int) -> str:
    row, col = divmod(index, 5)
    return f"{row},{col}"
