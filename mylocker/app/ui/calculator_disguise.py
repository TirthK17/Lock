"""
calculator_disguise.py — Working calculator disguise window.

When stealth mode is enabled, the app launches as a normal calculator.
Entering the secret PIN followed by '=' transitions seamlessly into the Login screen.

Security note: arithmetic evaluation uses a hand-written recursive-descent parser
that only allows digits, '.', +, -, *, / and parentheses. ``eval()`` is NOT used.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mylocker.app.ui.theme import Colors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safe arithmetic parser
# Grammar:
#   expr   = term (('+' | '-') term)*
#   term   = factor (('*' | '/') factor)*
#   factor = number | '(' expr ')' | '-' factor
# ---------------------------------------------------------------------------

class _Parser:
    """Recursive-descent parser for safe arithmetic expressions."""

    _MAX_DIGITS = 20   # guard against absurdly long numbers

    def __init__(self, text: str) -> None:
        # Strip all whitespace; accept × and ÷ as aliases
        self._s = text.replace("×", "*").replace("÷", "/").replace(" ", "")
        self._pos = 0

    def parse(self) -> float:
        result = self._expr()
        if self._pos != len(self._s):
            raise ValueError("Unexpected character")
        return result

    def _peek(self) -> Optional[str]:
        return self._s[self._pos] if self._pos < len(self._s) else None

    def _consume(self, ch: str) -> None:
        if self._peek() != ch:
            raise ValueError(f"Expected '{ch}'")
        self._pos += 1

    def _expr(self) -> float:
        left = self._term()
        while self._peek() in ("+", "-"):
            op = self._peek()
            self._pos += 1
            right = self._term()
            left = left + right if op == "+" else left - right
        return left

    def _term(self) -> float:
        left = self._factor()
        while self._peek() in ("*", "/"):
            op = self._peek()
            self._pos += 1
            right = self._factor()
            if op == "/":
                if right == 0:
                    raise ZeroDivisionError("Division by zero")
                left = left / right
            else:
                left = left * right
        return left

    def _factor(self) -> float:
        ch = self._peek()
        if ch == "-":
            self._pos += 1
            return -self._factor()
        if ch == "(":
            self._pos += 1
            val = self._expr()
            self._consume(")")
            return val
        return self._number()

    def _number(self) -> float:
        start = self._pos
        while self._peek() is not None and (self._peek().isdigit() or self._peek() == "."):
            self._pos += 1
            if self._pos - start > self._MAX_DIGITS:
                raise ValueError("Number too long")
        token = self._s[start:self._pos]
        if not token:
            raise ValueError("Expected number")
        return float(token)


def _safe_eval(expr: str) -> float:
    """Evaluate a numeric expression safely. Raises ValueError / ZeroDivisionError."""
    return _Parser(expr).parse()


class CalculatorDisguise(QWidget):
    """Working calculator widget serving as a stealth camouflage layer."""

    secret_entered = Signal()

    def __init__(self, secret_code: str = "1337", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.secret_code = secret_code
        self.current_expr = ""
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background-color: {Colors.BG_DARKEST};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        # Centered frame
        card = QFrame()
        card.setObjectName("CalcCard")
        card.setFixedSize(360, 480)
        card.setStyleSheet(f"""
            #CalcCard {{
                background-color: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 16px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(12)

        # Title bar in card
        title = QLabel("Calculator")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {Colors.TEXT_MUTED}; background: transparent;")
        card_layout.addWidget(title)

        # Display LCD
        self.display = QLineEdit("0")
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.display.setFont(QFont("Segoe UI", 26))
        self.display.setFixedHeight(64)
        self.display.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Colors.BG_INPUT};
                border: 1px solid {Colors.BORDER};
                border-radius: 10px;
                color: {Colors.TEXT_PRIMARY};
                padding-right: 12px;
            }}
        """)
        card_layout.addWidget(self.display)

        # Button Grid
        grid = QGridLayout()
        grid.setSpacing(8)

        buttons = [
            ("C", 0, 0), ("±", 0, 1), ("%", 0, 2), ("÷", 0, 3),
            ("7", 1, 0), ("8", 1, 1), ("9", 1, 2), ("×", 1, 3),
            ("4", 2, 0), ("5", 2, 1), ("6", 2, 2), ("-", 2, 3),
            ("1", 3, 0), ("2", 3, 1), ("3", 3, 2), ("+", 3, 3),
            ("0", 4, 0), (".", 4, 1), ("=", 4, 2),
        ]

        for text, r, c in buttons:
            btn = QPushButton(text)
            btn.setFont(QFont("Segoe UI", 14, QFont.Weight.Medium))
            btn.setFixedSize(68 if text != "0" else 68, 52)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

            if text in {"÷", "×", "-", "+", "="}:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {Colors.ACCENT};
                        color: {Colors.WHITE};
                        border-radius: 10px;
                    }}
                    QPushButton:hover {{
                        background-color: {Colors.ACCENT_HOVER};
                    }}
                """)
            elif text in {"C", "±", "%"}:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {Colors.BG_INPUT};
                        color: {Colors.TEXT_SECONDARY};
                        border: 1px solid {Colors.BORDER};
                        border-radius: 10px;
                    }}
                    QPushButton:hover {{
                        background-color: {Colors.BG_CARD_HOVER};
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {Colors.BG_DARK};
                        color: {Colors.TEXT_PRIMARY};
                        border: 1px solid {Colors.BORDER};
                        border-radius: 10px;
                    }}
                    QPushButton:hover {{
                        background-color: {Colors.BG_CARD_HOVER};
                    }}
                """)

            btn.clicked.connect(lambda _, t=text: self._on_btn_click(t))
            if text == "=":
                grid.addWidget(btn, r, c, 1, 2)
                btn.setFixedSize(144, 52)
            else:
                grid.addWidget(btn, r, c)

        card_layout.addLayout(grid)

        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(card)
        h.addStretch(1)
        layout.addStretch(1)
        layout.addLayout(h)
        layout.addStretch(1)

    def _on_btn_click(self, text: str) -> None:
        if text == "C":
            self.current_expr = ""
            self.display.setText("0")
        elif text == "=":
            # Check secret code first (before arithmetic, so the PIN is never evaluated)
            if self.current_expr.strip() == self.secret_code:
                logger.info("Secret disguise code accepted — revealing vault login.")
                self.secret_entered.emit()
                self.current_expr = ""
                self.display.setText("0")
                return

            # Normal evaluation — safe parser only
            try:
                result = _safe_eval(self.current_expr)
                # Format: drop unnecessary trailing zeros
                display_val = f"{result:g}" if result == result else "Error"
                self.display.setText(display_val)
                self.current_expr = display_val
            except (ValueError, ZeroDivisionError):
                self.display.setText("Error")
                self.current_expr = ""
        elif text == "±":
            if self.current_expr.startswith("-"):
                self.current_expr = self.current_expr[1:]
            else:
                self.current_expr = "-" + self.current_expr
            self.display.setText(self.current_expr or "0")
        elif text == "%":
            try:
                val = float(self.current_expr) / 100.0
                self.current_expr = str(val)
                self.display.setText(self.current_expr)
            except (ValueError, ZeroDivisionError):
                pass
        else:
            if self.display.text() == "0" and text not in {"+", "-", "×", "÷", "."}:
                self.current_expr = text
            else:
                self.current_expr += text
            self.display.setText(self.current_expr)


__all__ = ["CalculatorDisguise"]
