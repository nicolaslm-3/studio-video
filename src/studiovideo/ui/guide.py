"""Barre de parcours guidé (§4) : étapes d'un type de projet, état réel de chacune, action suivante."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from .widgets import button, label


@dataclass
class Step:
    key: str
    title: str
    done: bool
    action: Callable[[], None] | None = None
    hint: str = ""
    optional: bool = False


class GuideBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(12, 8, 12, 8)
        self.lay.setSpacing(6)
        self.steps: list[Step] = []
        self.next_btn = None

    def set_steps(self, title: str, steps: list[Step], extras: list[tuple[str, Callable]] | None = None) -> None:
        self.steps = steps
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.lay.addWidget(label(title, "h2"))
        nxt = self.next_step()
        for i, s in enumerate(steps):
            mark = "✓" if s.done else ("○" if s is not nxt else "●")
            lb = QLabel(f"{mark} {i + 1}. {s.title}" + (" (facultatif)" if s.optional and not s.done else ""))
            lb.setProperty("role", "muted" if s.done else (None if s is nxt else "faint"))
            lb.setToolTip(s.hint)
            self.lay.addWidget(lb)
            if s.optional and not s.done and s.action:
                self.lay.addWidget(button("Faire", "ghost", s.hint, s.action))
            if i < len(steps) - 1:
                self.lay.addWidget(label("›", "faint"))
        self.lay.addStretch(1)
        for text, fn in extras or []:
            self.lay.addWidget(button(text, "ghost", on_click=fn))
        if nxt is None:
            self.next_btn = None
            self.lay.addWidget(label("Parcours terminé ✓", "muted"))
        else:
            self.next_btn = button(f"Étape suivante : {nxt.title}", "primary", nxt.hint, nxt.action)
            self.next_btn.setEnabled(nxt.action is not None)
            self.lay.addWidget(self.next_btn)

    def next_step(self) -> Step | None:
        """Première étape obligatoire non faite (les étapes facultatives ont leur propre bouton)."""
        return next((s for s in self.steps if not s.done and not s.optional), None)
