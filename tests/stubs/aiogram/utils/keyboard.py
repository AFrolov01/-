from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

class InlineKeyboardBuilder:
    """Минимальная копия поведения aiogram: button() + adjust() + row()."""
    def __init__(self):
        self._rows = []      # уже готовые строки
        self._pending = []   # кнопки, ждущие adjust()

    def button(self, text, callback_data=None, **kw):
        self._pending.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        return self

    def _flush(self, sizes=None):
        if not self._pending:
            return
        sizes = list(sizes or [8])
        i, k = 0, 0
        while i < len(self._pending):
            size = sizes[k] if k < len(sizes) else sizes[-1]
            self._rows.append(self._pending[i:i + size])
            i += size; k += 1
        self._pending = []

    def adjust(self, *sizes, repeat=False):
        self._flush(sizes)
        return self

    def row(self, *buttons):
        self._flush()
        self._rows.append(list(buttons))
        return self

    def as_markup(self):
        self._flush()
        return InlineKeyboardMarkup(inline_keyboard=self._rows)
