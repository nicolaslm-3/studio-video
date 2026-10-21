"""Chaîne de mise à jour : signature, flux appcast, préparation à l'arrêt, journal."""
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from make_appcast import S, build, markdown_to_html  # noqa: E402
from update_keys import generate, public_from_private, sign_file, verify_file  # noqa: E402

from studiovideo import __version__  # noqa: E402
from studiovideo.core import build_context  # noqa: E402
from studiovideo.update import config  # noqa: E402
from studiovideo.update.updater import ShutdownCoordinator, UpdateJournal, create_updater  # noqa: E402


def test_sign_and_verify(tmp_path):
    priv, pub = generate()
    assert public_from_private(priv) == pub
    f = tmp_path / "Setup.exe"
    f.write_bytes(b"MZ" + b"\x00" * 5000)
    sig, n = sign_file(f, priv)
    assert n == 5002 and verify_file(f, pub, sig)
    f.write_bytes(b"MZ" + b"\x01" * 5000)  # fichier altéré
    assert not verify_file(f, pub, sig)
    _, other_pub = generate()
    f.write_bytes(b"MZ" + b"\x00" * 5000)
    assert not verify_file(f, other_pub, sig)


def test_appcast_build_keeps_history_and_replaces_same_version(tmp_path):
    feed = tmp_path / "appcast.xml"
    for v in ("0.1.0", "0.2.0", "0.10.0"):
        build(feed, v, f"https://example.org/{v}.exe", "c2ln", 123, "<p>notes</p>")
    build(feed, "0.2.0", "https://example.org/0.2.0-fix.exe", "bmV3", 456, "<p>corrigé</p>")
    root = ET.parse(feed).getroot()
    encs = [i.find("enclosure") for i in root.find("channel").findall("item")]
    assert [e.get(f"{S}version") for e in encs] == ["0.10.0", "0.2.0", "0.1.0"]
    fixed = encs[1]
    assert fixed.get("url").endswith("0.2.0-fix.exe") and fixed.get(f"{S}edSignature") == "bmV3"
    assert fixed.get("length") == "456" and "/SILENT" in fixed.get(f"{S}installerArguments")


def test_markdown_notes():
    h = markdown_to_html("# Nouveautés\n- Import <URL>\n- Lecteur\n\nMerci")
    assert "<h2>Nouveautés</h2>" in h and "<li>Import &lt;URL&gt;</li>" in h and "<p>Merci</p>" in h


def test_updater_unavailable_outside_installed_windows():
    u = create_updater("stable", lambda: True, lambda: None)
    assert not u.available and u.reason
    assert config.appcast_url("beta").endswith("appcast-beta.xml")


def test_shutdown_coordinator_waits_for_critical_jobs(tmp_path):
    app = build_context(tmp_path / "d")
    journal = UpdateJournal(app.paths.root)
    coord = ShutdownCoordinator(app, journal)
    gate = threading.Event()
    app.jobs.register("export", lambda ctx, p: gate.wait(5) and {})
    app.jobs.submit("export", "Export final", critical=True)
    time.sleep(0.1)
    assert coord.can_shutdown() is False
    assert "tâche critique" in journal.log_file.read_text(encoding="utf-8")
    gate.set()
    app.jobs.wait_idle(5)
    assert coord.can_shutdown() is True
    coord.prepare()
    st = journal.read_state()
    assert st["pending"] and st["from_version"] == __version__ and Path(st["backup"]).exists()
    app.close()


def test_journal_reports_update_result(tmp_path, monkeypatch):
    j = UpdateJournal(tmp_path)
    assert j.on_startup() is None
    j.write_state(pending=True, from_version="0.0.9")
    msg = j.on_startup()
    assert "0.0.9" in msg and __version__ in msg
    assert j.read_state()["ok"] is True and j.on_startup() is None
    j.write_state(pending=True, from_version=__version__)
    assert "n'a pas été installée" in j.on_startup()
