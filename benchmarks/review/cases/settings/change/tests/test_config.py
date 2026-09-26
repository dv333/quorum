from config import Settings, get_settings, load_settings


def test_defaults():
    assert load_settings({}) == Settings()


def test_debug_accepts_common_true_values():
    for value in ("1", "true", "YES", " on "):
        assert load_settings({"DEBUG": value}).debug
    for value in ("0", "false", "no", ""):
        assert not load_settings({"DEBUG": value}).debug


def test_workers_and_db_url():
    s = load_settings({"WORKERS": "8", "DB_URL": "postgresql://db/app"})
    assert s.workers == 8 and s.db_url == "postgresql://db/app"


def test_get_settings_keeps_its_shape(monkeypatch):
    monkeypatch.setenv("DEBUG", "1")
    assert get_settings()["debug"] is True
    assert set(get_settings()) == {"db_url", "debug", "workers"}
