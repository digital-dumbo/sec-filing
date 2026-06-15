from k10fetcher.config import PROJECT_ROOT, settings


def test_default_app_dir_is_project_local():
    assert settings.app_dir == PROJECT_ROOT / ".k10fetcher"
    assert settings.db_path == PROJECT_ROOT / ".k10fetcher" / "k10fetcher.db"
    assert settings.data_dir == PROJECT_ROOT / ".k10fetcher" / "data"
    assert settings.log_path == PROJECT_ROOT / ".k10fetcher" / "logs" / "k10fetcher.log"