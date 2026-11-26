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
        # Neutral native Windows-like dark calculator surface — deliberately no Vault branding.
        self.setStyleSheet("background-color: #202020;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        card = QFrame(self)
        card.setObjectName("CalcCard")
        card.setFixedSize(336, 444)
        card.setStyleSheet("""
            #CalcCard {
                background-color: #202020;
                border: 1px solid #303030;
                border-radius: 8px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        title = QLabel("Standard", card)
        title.setFont(QFont("Segoe UI Variable", 12, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #EAEAEA; background: transparent; padding: 0 4px;")
        card_layout.addWidget(title)

        # Display
        self.display = QLineEdit("0", card)
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.display.setFont(QFont("Segoe UI Variable", 28, QFont.Weight.DemiBold))
        self.display.setFixedHeight(72)
        self.display.setStyleSheet("""
            QLineEdit {
                background-color: #202020;
                border: none;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 4px 8px;
            }
        """)
        card_layout.addWidget(self.display)

        # Crisp 4-column native key grid
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        buttons = [
            ("C", 0, 0), ("±", 0, 1), ("%", 0, 2), ("÷", 0, 3),
            ("7", 1, 0), ("8", 1, 1), ("9", 1, 2), ("×", 1, 3),
            ("4", 2, 0), ("5", 2, 1), ("6", 2, 2), ("-", 2, 3),
            ("1", 3, 0), ("2", 3, 1), ("3", 3, 2), ("+", 3, 3),
            ("0", 4, 0), (".", 4, 1), ("=", 4, 2),
        ]

        for text, r, c in buttons:
            btn = QPushButton(text, card)
            btn.setFont(QFont("Segoe UI Variable", 14, QFont.Weight.Normal))
            btn.setFixedSize(75, 54)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

            if text == "=":
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #76B9ED;
                        color: #111111;
                        border: none;
                        border-radius: 4px;
                        font-weight: 600;
                    }
                    QPushButton:hover { background-color: #6BAEE2; }
                    QPushButton:pressed { background-color: #5C9DCE; }
                """)
            elif text in {"÷", "×", "-", "+", "C", "±", "%"}:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #323232;
                        color: #F3F3F3;
                        border: 1px solid #3A3A3A;
                        border-radius: 4px;
                    }
                    QPushButton:hover { background-color: #3B3B3B; }
                    QPushButton:pressed { background-color: #2B2B2B; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #3B3B3B;
                        color: #FFFFFF;
                        border: 1px solid #434343;
                        border-radius: 4px;
                    }
                    QPushButton:hover { background-color: #454545; }
                    QPushButton:pressed { background-color: #303030; }
                """)

            btn.clicked.connect(lambda _=False, t=text: self._on_btn_click(t))
            if text == "=":
                grid.addWidget(btn, r, c, 1, 2)
                btn.setFixedSize(154, 54)
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
