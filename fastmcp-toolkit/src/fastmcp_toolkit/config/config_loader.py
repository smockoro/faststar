"""環境変数からアプリケーション設定を読み込む実装。"""

import abc
import os

from fastmcp_toolkit.config.application_config import ApplicationConfig
from fastmcp_toolkit.util import parse_str_tuple


class ConfigLoader(abc.ABC):
    """設定の読み込み元を表す抽象基底クラス。

    サブクラスは ``load_config()`` を実装し、具体的な読み込み元から
    ApplicationConfigを構築する。
    """

    @abc.abstractmethod
    def load_config(self) -> ApplicationConfig:
        """設定を読み込みApplicationConfigを返す。

        Returns:
            読み込まれたApplicationConfigインスタンス。
        """


class EnvVarConfigLoader(ConfigLoader):
    """環境変数からアプリケーション設定を読み込むConfigLoaderの実装。

    各設定項目は対応する環境変数から読み込まれ、未設定の場合は
    デフォルト値が使用される。
    """

    def load_config(self) -> ApplicationConfig:
        """環境変数からApplicationConfigを生成する。

        Returns:
            環境変数の値を反映したApplicationConfigインスタンス。
        """
        return ApplicationConfig(
            invisible_target_prefix=parse_str_tuple(
                os.getenv("INVISIBLE_TARGET_PREFIX", "")
            ),
            invisible_target_suffix=parse_str_tuple(
                os.getenv("INVISIBLE_TARGET_SUFFIX", "")
            ),
            metrics_endpoint=os.getenv("METRICS_ENDPOINT", "/metrics"),
            metrics_enabled=(os.getenv("METRICS_ENABLED", "true").lower() == "true"),
            health_check_enabled=(
                os.getenv("HEALTH_CHECK_ENABLED", "true").lower() == "true"
            ),
            health_check_liveness_endpoint=os.getenv(
                "HEALTH_CHECK_LIVENESS_ENDPOINT", "/readyz"
            ),
            health_check_readiness_endpoint=os.getenv(
                "HEALTH_CHECK_READINESS_ENDPOINT", "/livez"
            ),
            transport_security_enabled=(
                os.getenv("TRANSPORT_SECURITY_ENABLED", "false").lower() == "true"
            ),
            allowed_hosts=parse_str_tuple(os.getenv("ALLOWED_HOSTS", "")),
            allowed_origins=parse_str_tuple(os.getenv("ALLOWED_ORIGINS", "")),
        )
