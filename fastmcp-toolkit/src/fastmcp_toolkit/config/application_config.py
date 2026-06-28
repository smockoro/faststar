"""アプリケーション設定モデルとプロバイダ。"""

from enum import StrEnum

from pydantic import BaseModel

__all__ = ["ApplicationConfig", "ApplicationConfigProvider", "ConfigType"]


class ConfigType(StrEnum):
    """設定の読み込み元を表す列挙型。"""

    ENV = "env"
    YAML = "yaml"


class ApplicationConfig(BaseModel):
    """アプリケーション全体の設定を保持するモデル。

    Attributes:
        invisible_target_prefix: 非表示ツールのプレフィックス群。
        invisible_target_suffix: 非表示ツールのサフィックス群。
        metrics_endpoint: Prometheusメトリクスエンドポイントのパス。
        metrics_enabled: メトリクスを有効にするか。
        health_check_enabled: ヘルスチェックを有効にするか。
        health_check_liveness_endpoint: Livenessエンドポイントのパス。
        health_check_readiness_endpoint: Readinessエンドポイントのパス。
        transport_security_enabled: DNS Rebinding防御を有効にするか。
        allowed_hosts: 許可するHostヘッダー値のリスト。
        allowed_origins: 許可するOriginヘッダー値のリスト。
    """

    invisible_target_prefix: tuple[str, ...] = ()
    invisible_target_suffix: tuple[str, ...] = ()
    metrics_endpoint: str = "/metrics"
    metrics_enabled: bool = True
    health_check_enabled: bool = True
    health_check_liveness_endpoint: str = "/readyz"
    health_check_readiness_endpoint: str = "/livez"
    security_headers_enabled: bool = True
    security_headers_hsts: bool = True
    transport_security_enabled: bool = False
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()


class ApplicationConfigProvider:
    """設定の読み込み元に応じたApplicationConfigを生成するファクトリ。

    Example::

        config = ApplicationConfigProvider.get_config("env")
    """

    @staticmethod
    def get_config(config_type: str) -> ApplicationConfig:
        """指定された読み込み元からApplicationConfigを生成する。

        Args:
            config_type: 設定読み込み元の種別（"env" or "yaml"）。

        Returns:
            読み込まれたApplicationConfigインスタンス。

        Raises:
            ValueError: 未実装の読み込み元が指定された場合。
        """
        from fastmcp_toolkit.config.config_loader import (
            EnvVarConfigLoader,
        )

        match config_type:
            case ConfigType.ENV:
                return EnvVarConfigLoader().load_config()
            case ConfigType.YAML:
                raise ValueError("unimplemented provide config from yaml")
            case _:
                return EnvVarConfigLoader().load_config()
