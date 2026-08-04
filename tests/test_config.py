from app.core.config import Settings


def test_cors_environment_lists_use_csv(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://one.example, https://two.example")
    monkeypatch.setenv("CORS_ALLOWED_METHODS", "GET,POST,OPTIONS")
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins == ["https://one.example", "https://two.example"]
    assert settings.cors_allowed_methods == ["GET", "POST", "OPTIONS"]
