from k10fetcher.config import PROJECT_ROOT, settings

# Add comments to explain what this test is doing

def test_default_app_dir_is_project_local():
    # Test that the default application directory is set to a subdirectory of the project root
    assert settings.app_dir == PROJECT_ROOT / ".k10fetcher"
    # Test that the default database path is set correctly
    assert settings.db_path == PROJECT_ROOT / ".k10fetcher" / "k10fetcher.db"
    # Test that the default data directory is set correctly
    assert settings.data_dir == PROJECT_ROOT / ".k10fetcher" / "data"
    # Test that the default log path is set correctly
    assert settings.log_path == PROJECT_ROOT / ".k10fetcher" / "logs" / "k10fetcher.log"
