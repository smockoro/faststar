"""ApplicationConfigProvider のテスト。"""

import pytest

from fastmcp_toolkit.config.application_config import (
    ApplicationConfig,
    ApplicationConfigProvider,
    ConfigType,
)


class TestApplicationConfigProvider:
    def test_env_returns_application_config(self):
        config = ApplicationConfigProvider.get_config(ConfigType.ENV)
        assert isinstance(config, ApplicationConfig)

    def test_yaml_raises_not_implemented(self):
        with pytest.raises(ValueError, match="unimplemented"):
            ApplicationConfigProvider.get_config(ConfigType.YAML)

    def test_unknown_value_returns_default(self):
        config = ApplicationConfigProvider.get_config("unknown")
        assert isinstance(config, ApplicationConfig)
