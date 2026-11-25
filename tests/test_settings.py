from pathlib import Path

import pytest

from jobcollector.settings import load_env


def test_load_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".env"
    path.write_text("# c\n#X_A=no\nX_A=1=2\nX_B = 'a b'\nX_C=file\n\n", encoding="utf-8")
    monkeypatch.setenv("X_C", "process")
    env = load_env(path)
    assert (env["X_A"], env["X_B"], env["X_C"]) == ("1=2", "a b", "process")
