from pathlib import Path

import pytest

KB = Path(__file__).resolve().parents[1] / "data" / "kb.zip"


@pytest.fixture(scope="session")
def kb_path() -> Path:
    if not KB.exists():
        pytest.skip("нет data/kb.zip: материалы кейса не входят в репозиторий, см. data/README.md")
    return KB
