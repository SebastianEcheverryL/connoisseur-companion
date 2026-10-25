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
