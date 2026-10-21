import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from studiovideo.db.database import open_database  # noqa: E402
from studiovideo.paths import app_paths  # noqa: E402


@pytest.fixture
def paths(tmp_path):
    return app_paths(tmp_path / "data")


@pytest.fixture
def db(paths):
    d = open_database(paths.database, paths.backups)
    yield d
    d.close()
