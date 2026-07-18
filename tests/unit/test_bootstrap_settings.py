from __future__ import annotations

from alpha_foundry.bootstrap import BootstrapSettings

_ENV_KEYS = (
    "ALPHA_FOUNDRY_HOME",
    "ALPHA_FOUNDRY_DB",
    "ALPHA_FOUNDRY_ARTIFACTS",
    "ALPHA_FOUNDRY_LLM_PROVIDER",
    "ALPHA_FOUNDRY_LLM_BASE_URL",
    "ALPHA_FOUNDRY_LLM_API_KEY",
    "ALPHA_FOUNDRY_LLM_TIMEOUT_SECONDS",
    "NVIDIA_API_KEY",
)


def _clear_env(monkeypatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_bootstrap_settings_load_dotenv_nvidia_defaults(tmp_path, monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "NVIDIA_API_KEY=from-dotenv\nALPHA_FOUNDRY_LLM_TIMEOUT_SECONDS=45\n",
        encoding="utf-8",
    )

    settings = BootstrapSettings.from_environment()

    assert settings.llm_provider == "nvidia"
    assert settings.llm_api_key == "from-dotenv"
    assert settings.llm_timeout_seconds == 45.0


def test_bootstrap_settings_environment_overrides_dotenv(tmp_path, monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "NVIDIA_API_KEY=from-dotenv\nALPHA_FOUNDRY_LLM_PROVIDER=nvidia\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALPHA_FOUNDRY_LLM_PROVIDER", "custom")
    monkeypatch.setenv("ALPHA_FOUNDRY_LLM_API_KEY", "from-env")
    monkeypatch.setenv("ALPHA_FOUNDRY_LLM_BASE_URL", "https://example.invalid/v1")

    settings = BootstrapSettings.from_environment()

    assert settings.llm_provider == "custom"
    assert settings.llm_api_key == "from-env"
    assert settings.llm_base_url == "https://example.invalid/v1"
