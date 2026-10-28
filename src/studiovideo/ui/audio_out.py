"""Le son suit la sortie audio de Windows : des écouteurs Bluetooth connectés pendant que le logiciel est ouvert
(ou choisis comme sortie par défaut) reçoivent le son, sans redémarrer. Qt garde sinon la sortie active au moment
où le lecteur a été créé."""
from __future__ import annotations

from typing import Callable


def follow_default_output(owner, outputs: Callable[[], list]) -> Callable[[], None]:
    """Branche les sorties audio `outputs()` sur la sortie par défaut, maintenant et à chaque changement de
    périphérique. Retourne la fonction de mise à jour (à appeler aussi au lancement de la lecture)."""
    from PySide6.QtMultimedia import QMediaDevices

    def apply() -> None:
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            return
        for ao in outputs():
            if ao is not None and ao.device() != dev:
                ao.setDevice(dev)

    devices = QMediaDevices(owner)
    devices.audioOutputsChanged.connect(apply)
    owner._media_devices = devices                     # gardé en vie avec le lecteur
    apply()
    return apply
