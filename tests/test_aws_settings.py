"""Loading settings with SSM_PARAMETER_PATH set must not recurse into the settings being built."""

from connoisseur import aws, settings


def test_settings_load_ssm_secrets_first(monkeypatch):
    calls = []
    monkeypatch.setenv("SSM_PARAMETER_PATH", "/connoisseur/test/")
    monkeypatch.setattr(aws, "load_ssm_parameters", lambda path: calls.append(path) or [])
    settings.get_settings.cache_clear()
    try:
        assert settings.get_settings().environment == "test"
        assert calls == ["/connoisseur/test/"]
    finally:
        monkeypatch.delenv("SSM_PARAMETER_PATH")
        settings.get_settings.cache_clear()


def test_langfuse_host_also_reads_the_sdk_variable_name(monkeypatch):
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    assert settings.Settings(_env_file=None).langfuse_host == "https://us.cloud.langfuse.com"
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")  # the app's own name wins
    assert settings.Settings(_env_file=None).langfuse_host == "http://localhost:3000"
