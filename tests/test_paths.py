from pathlib import Path

from atlassinate import paths


def test_atlassinate_home_defaults_to_dot_atlassinate(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLASSINATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.atlassinate_home() == tmp_path / ".atlassinate"


def test_atlassinate_home_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLASSINATE_HOME", str(tmp_path / "custom"))
    assert paths.atlassinate_home() == tmp_path / "custom"


def test_mirror_path_under_gonfluence(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLASSINATE_HOME", str(tmp_path))
    assert paths.mirror_path("AR") == tmp_path / "gonfluence" / "AR"


def test_edits_path_under_gonfluence_edits(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLASSINATE_HOME", str(tmp_path))
    assert paths.edits_path("12345") == tmp_path / "gonfluence" / ".edits" / "12345"
    assert paths.edits_archive() == tmp_path / "gonfluence" / ".edits" / ".archive"


def test_ensure_dir_creates(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    result = paths.ensure_dir(target)
    assert result == target
    assert target.is_dir()
