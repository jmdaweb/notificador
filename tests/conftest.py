import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notificador import paths  # noqa: E402


@pytest.fixture(autouse=True)
def data_dir(tmp_path):
	paths.set_data_dir(tmp_path / "data")
	yield tmp_path / "data"
	paths.set_data_dir(None)
