"""
Цветной текст в кодовых блоках Discord через ANSI-коды.

Discord (десктоп и веб, современный мобильный клиент) раскрашивает кодовые
блоки с меткой ```ansi``` по управляющим последовательностям ANSI. Клиенты
без поддержки просто показывают текст как обычный код — ничего не ломается.

Формат: `\x1b[{стиль};{цвет}m` ... `\x1b[0m`. Фона в Discord нет смысла
использовать — под тёмную и светлую тему одновременно не подобрать, поэтому
здесь только цвет текста и жирность.
"""

from __future__ import annotations

ESC = "\x1b"
RESET = f"{ESC}[0m"

GRAY = 30
RED = 31
GREEN = 32
YELLOW = 33
BLUE = 34
PINK = 35
CYAN = 36
WHITE = 37


def paint(text: str, color: int, *, bold: bool = False) -> str:
    """Красит одну строку текста. Пустой текст остаётся пустым."""
    if not text:
        return text
    style = 1 if bold else 0
    return f"{ESC}[{style};{color}m{text}{RESET}"


def bracket(text: str, color: int) -> str:
    """Слово в квадратных скобках: скобки нейтральные, текст — цветной и жирный.
    Ровно то, что видно в `[Рассмотрен]`, `[Отклонён]`, `[Принято]` и т.п."""
    return f"[{paint(text, color, bold=True)}]"


def block(lines: list[str]) -> str:
    """Оборачивает готовые (уже покрашенные) строки в кодовый блок ```ansi```."""
    return "```ansi\n" + "\n".join(lines) + "\n```"
