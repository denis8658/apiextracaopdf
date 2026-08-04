from app.core.config import Settings


def test_cors_environment_lists_use_csv(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://one.example, https://two.example")
    monkeypatch.setenv("CORS_ALLOWED_METHODS", "GET,POST,OPTIONS")
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins == ["https://one.example", "https://two.example"]
    assert settings.cors_allowed_methods == ["GET", "POST", "OPTIONS"]


def test_railway_postgresql_url_uses_async_driver():
    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:secret@postgres.railway.internal:5432/railway",
    )

    assert settings.database_url == (
        "postgresql+asyncpg://user:secret@postgres.railway.internal:5432/railway"
    )
