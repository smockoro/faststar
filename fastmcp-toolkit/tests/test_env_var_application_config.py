"""EnvVarConfigLoader のテスト。"""

from fastmcp_toolkit.config.config_loader import EnvVarConfigLoader


class TestEnvVarConfigLoader:
    def test_prefix_from_env(self, monkeypatch):
        monkeypatch.setenv("INVISIBLE_TARGET_PREFIX", "_internal,_hidden")
        config = EnvVarConfigLoader().load_config()
        assert config.invisible_target_prefix == ("_internal", "_hidden")

    def test_suffix_from_env(self, monkeypatch):
        monkeypatch.setenv("INVISIBLE_TARGET_SUFFIX", "_debug,_test")
        config = EnvVarConfigLoader().load_config()
        assert config.invisible_target_suffix == ("_debug", "_test")

    def test_prefix_default_empty(self, monkeypatch):
        monkeypatch.delenv("INVISIBLE_TARGET_PREFIX", raising=False)
        config = EnvVarConfigLoader().load_config()
        assert config.invisible_target_prefix == ()

    def test_suffix_default_empty(self, monkeypatch):
        monkeypatch.delenv("INVISIBLE_TARGET_SUFFIX", raising=False)
        config = EnvVarConfigLoader().load_config()
        assert config.invisible_target_suffix == ()

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("INVISIBLE_TARGET_PREFIX", " foo , bar ")
        config = EnvVarConfigLoader().load_config()
        assert config.invisible_target_prefix == ("foo", "bar")

    def test_single_value(self, monkeypatch):
        monkeypatch.setenv("INVISIBLE_TARGET_SUFFIX", "only")
        config = EnvVarConfigLoader().load_config()
        assert config.invisible_target_suffix == ("only",)
