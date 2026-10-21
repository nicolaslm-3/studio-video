"""Configuration des mises à jour.

PUBLIC_KEY et BASE_URL sont injectés à la compilation par le workflow GitHub
Actions (tools/stamp_build.py). Ils ne contiennent aucun secret : seule la clé
PUBLIQUE est dans le programme ; la clé privée reste dans les secrets GitHub.
"""

# Remplacé à la compilation. Vide = mises à jour désactivées (build de développement).
PUBLIC_KEY = ""
# Ex. : https://<utilisateur>.github.io/studio-video
BASE_URL = ""

CHANNELS = {"stable": "appcast.xml", "beta": "appcast-beta.xml"}


def appcast_url(channel: str = "stable") -> str:
    return f"{BASE_URL.rstrip('/')}/{CHANNELS.get(channel, CHANNELS['stable'])}"
