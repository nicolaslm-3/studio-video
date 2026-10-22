"""Point d'entrée de l'application."""
from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv)
    if "--self-test" in args:
        from .selftest import run
        return run(args)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import __app_name__, __version__
    from .core import build_context
    from .db.database import MigrationError
    from .logging_setup import setup_logging
    from .paths import app_paths
    from .ui import theme
    from .ui.main_window import MainWindow
    from .update.updater import UpdateJournal

    paths = app_paths()
    setup_logging(paths.logs)
    log = logging.getLogger("studiovideo")
    log.info("Démarrage %s %s", __app_name__, __version__)

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(argv if argv is not None else sys.argv)
    qapp.setApplicationName(__app_name__)
    qapp.setApplicationDisplayName("Studio Vidéo")
    qapp.setApplicationVersion(__version__)
    theme.apply(qapp, "dark")

    journal = UpdateJournal(paths.root)
    message = journal.on_startup()
    try:
        ctx = build_context()
    except MigrationError as exc:
        log.error("Migration impossible : %s", exc)
        QMessageBox.critical(None, "Studio Vidéo",
                             f"{exc}\n\nVos données n'ont pas été modifiées. Des sauvegardes se trouvent dans :\n"
                             f"{paths.backups}")
        return 2
    except Exception as exc:
        log.exception("Démarrage impossible")
        QMessageBox.critical(None, "Studio Vidéo", f"Démarrage impossible : {exc}\n\nJournal : {paths.logs}")
        return 1

    try:
        ctx.db.daily_backup()                      # au plus une sauvegarde automatique par jour
    except Exception:                              # une sauvegarde ratée ne doit pas empêcher de travailler
        log.exception("Sauvegarde quotidienne impossible")
    try:
        from .media.fonts import load_user_fonts
        load_user_fonts(ctx.paths.root)
    except Exception:
        log.exception("Polices ajoutées non chargées")
    theme.apply(qapp, ctx.settings.get("theme"))
    win = MainWindow(ctx, message)
    win.show()
    rc = qapp.exec()
    ctx.close()
    log.info("Arrêt (code %s)", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
