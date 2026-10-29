"""Choix de la date et de l'heure : un calendrier épuré (le jour choisi dans un rond), l'heure et les minutes par 5."""
from __future__ import annotations

from PySide6.QtCore import QDate, QDateTime, QLocale, QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget, QComboBox, QHBoxLayout, QMenu, QToolButton, QWidget, QWidgetAction

from . import theme


class RoundCalendar(QCalendarWidget):
    """Calendrier dessiné jour par jour : le jour choisi dans un rond plein, les jours passés ou hors du mois
    grisés."""

    def paintCell(self, painter, rect, date) -> None:     # noqa: N802
        from PySide6.QtGui import QPainter
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(rect, QColor(theme.color("raised")))
        off = (date < self.minimumDate() or date > self.maximumDate() or date.month() != self.monthShown()
               or date.year() != self.yearShown())
        if date == self.selectedDate():
            d = min(rect.width(), rect.height()) - 6
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.color("accent")))
            painter.drawEllipse(QRectF(rect.center().x() - d / 2 + 1, rect.center().y() - d / 2 + 1, d, d))
            painter.setPen(QColor(theme.color("accent_text")))
            f = painter.font()
            f.setBold(True)
            painter.setFont(f)
        else:
            painter.setPen(QColor(theme.color("faint" if off else "text")))
        painter.drawText(rect, Qt.AlignCenter, str(date.day()))
        painter.restore()


class WhenPicker(QWidget):
    """Date et heure de publication : un calendrier épuré (dans un menu), l'heure et les minutes (par 5)."""
    dateTimeChanged = Signal(QDateTime)

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.date_btn = QToolButton()
        self.date_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.date_btn.setPopupMode(QToolButton.InstantPopup)
        self.date_btn.setIcon(theme.icon("calendar-days", theme.color("muted"), 16))
        self.date_btn.setProperty("role", "field")
        self.date_btn.setCursor(Qt.PointingHandCursor)
        self.date_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.cal = RoundCalendar()
        self.cal.setProperty("role", "cal")
        self.cal.setLocale(QLocale(QLocale.French))
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.cal.setHorizontalHeaderFormat(QCalendarWidget.SingleLetterDayNames)
        self.cal.setFirstDayOfWeek(Qt.Monday)
        self.cal.setMinimumDate(QDate.currentDate())
        self.cal.setFixedSize(300, 250)
        self.cal.clicked.connect(self._date_picked)
        m = QMenu(self.date_btn)
        m.aboutToShow.connect(self._style_cal)             # couleurs du thème affiché
        act = QWidgetAction(m)
        act.setDefaultWidget(self.cal)
        m.addAction(act)
        self.date_btn.setMenu(m)
        self._style_cal()
        h.addWidget(self.date_btn)
        self.hour, self.minute = QComboBox(), QComboBox()
        for k in range(24):
            self.hour.addItem(f"{k:02d} h", k)
        for k in range(0, 60, 5):
            self.minute.addItem(f"{k:02d}", k)
        self.hour.setToolTip("Heure")
        self.minute.setToolTip("Minutes")
        for c in (self.hour, self.minute):
            c.setMaxVisibleItems(12)
            c.currentIndexChanged.connect(lambda _i: self.dateTimeChanged.emit(self.dateTime()))
            h.addWidget(c)
        h.addStretch(1)
        self.setDateTime(QDateTime.currentDateTime().addSecs(3600))

    def _style_cal(self) -> None:
        plain = QTextCharFormat()
        plain.setForeground(QColor(theme.color("text")))
        for d in (Qt.Saturday, Qt.Sunday):                 # week-end sans rouge : calendrier sobre
            self.cal.setWeekdayTextFormat(d, plain)
        head = QTextCharFormat()
        head.setForeground(QColor(theme.color("faint")))
        self.cal.setHeaderTextFormat(head)

    def _date_picked(self, d: QDate) -> None:
        self.date_btn.menu().close()
        self._show_date(d)
        self.dateTimeChanged.emit(self.dateTime())

    def _show_date(self, d: QDate) -> None:
        self.cal.setSelectedDate(d)
        self.date_btn.setText(QLocale(QLocale.French).toString(d, "ddd d MMM yyyy"))

    def dateTime(self) -> QDateTime:                     # noqa: N802 — comme QDateTimeEdit
        return QDateTime(self.cal.selectedDate(), QTime(self.hour.currentData() or 0, self.minute.currentData() or 0))

    def setDateTime(self, dt: QDateTime) -> None:        # noqa: N802
        m = dt.time().hour() * 60 + dt.time().minute()
        k = -(-m // 5)                                    # arrondi aux 5 minutes suivantes
        d = dt.date().addDays(1) if k >= 288 else dt.date()
        k %= 288
        self.blockSignals(True)
        self._show_date(d)
        self.hour.setCurrentIndex(k // 12)
        self.minute.setCurrentIndex(k % 12)
        self.blockSignals(False)
