import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def example(tmp_path: Path) -> Path:
    """A fresh copy of the example sheet + config. Returns the config path."""
    import make_example

    make_example.build(tmp_path / "room-bookings.xlsx")
    shutil.copy(ROOT / "examples" / "config.yaml", tmp_path / "config.yaml")
    return tmp_path / "config.yaml"
