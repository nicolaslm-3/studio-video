"""Ajoute une version au flux de mises à jour appcast (format Sparkle, lu par WinSparkle).

Usage :
  python tools/make_appcast.py --feed site/appcast.xml --version 0.2.0 \
      --url https://github.com/<u>/<r>/releases/download/v0.2.0/StudioVideo-0.2.0-Setup.exe \
      --installer dist/StudioVideo-0.2.0-Setup.exe --notes CHANGELOG-0.2.0.md [--key private.key]

Le fichier est créé s'il n'existe pas ; les versions précédentes sont conservées
(les 15 plus récentes). Une version déjà présente est remplacée.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from update_keys import sign_file  # noqa: E402

SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", SPARKLE)
S = f"{{{SPARKLE}}}"
INSTALLER_ARGS = "/SILENT /SP- /SUPPRESSMSGBOXES /NOCANCEL"
KEEP = 15


def markdown_to_html(md: str) -> str:
    """Conversion minimale (titres, listes, paragraphes) suffisante pour des notes de version."""
    out, in_list = [], False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(s[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if s.startswith("#"):
            level = min(4, len(s) - len(s.lstrip("#")) + 1)
            out.append(f"<h{level}>{html.escape(s.lstrip('#').strip())}</h{level}>")
        elif s:
            out.append(f"<p>{html.escape(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _version_key(v: str) -> tuple:
    parts = []
    for p in v.replace("-", ".").split("."):
        parts.append((0, int(p)) if p.isdigit() else (-1, p))
    return tuple(parts)


def build(feed: Path, version: str, url: str, signature: str, length: int, notes_html: str,
          title: str = "Studio Vidéo", min_os: str = "10.0") -> None:
    if feed.exists():
        tree = ET.parse(feed)
        channel = tree.getroot().find("channel")
    else:
        root = ET.Element("rss", {"version": "2.0"})
        channel = ET.SubElement(root, "channel")
        ET.SubElement(channel, "title").text = f"{title} — mises à jour"
        ET.SubElement(channel, "language").text = "fr"
        tree = ET.ElementTree(root)
    for item in list(channel.findall("item")):
        enc = item.find("enclosure")
        if enc is not None and enc.get(f"{S}version") == version:
            channel.remove(item)
    item = ET.Element("item")
    ET.SubElement(item, "title").text = f"Version {version}"
    ET.SubElement(item, "pubDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(item, "description").text = notes_html
    ET.SubElement(item, f"{S}minimumSystemVersion").text = min_os
    ET.SubElement(item, "enclosure", {
        "url": url, f"{S}version": version, f"{S}shortVersionString": version,
        f"{S}os": "windows-x64", "length": str(length), "type": "application/octet-stream",
        f"{S}edSignature": signature, f"{S}installerArguments": INSTALLER_ARGS,
    })
    channel.append(item)
    items = sorted(channel.findall("item"),
                   key=lambda i: _version_key(i.find("enclosure").get(f"{S}version")), reverse=True)
    for i in channel.findall("item"):
        channel.remove(i)
    for i in items[:KEEP]:
        channel.append(i)
    ET.indent(tree, space="  ")
    feed.parent.mkdir(parents=True, exist_ok=True)
    tree.write(feed, encoding="utf-8", xml_declaration=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", required=True, type=Path)
    ap.add_argument("--version", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--installer", required=True, type=Path)
    ap.add_argument("--notes", type=Path)
    ap.add_argument("--key", help="fichier de clé privée (sinon UPDATE_PRIVATE_KEY)")
    a = ap.parse_args()
    key = Path(a.key).read_text() if a.key else os.environ.get("UPDATE_PRIVATE_KEY")
    if not key:
        sys.exit("Clé privée absente : impossible de signer l'installateur.")
    if not a.url.startswith("https://"):
        sys.exit("L'URL de téléchargement doit être en HTTPS.")
    sig, n = sign_file(a.installer, key)
    notes = markdown_to_html(a.notes.read_text(encoding="utf-8")) if a.notes and a.notes.exists() \
        else f"<p>Version {a.version}</p>"
    build(a.feed, a.version, a.url, sig, n, notes)
    print(f"{a.feed} mis à jour : version {a.version} ({n} octets, signée).")


if __name__ == "__main__":
    main()
