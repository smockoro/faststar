"""ASGI共通ミドルウェア。"""

from core_toolkit.middleware.access_log import AccessLogMiddleware
from core_toolkit.middleware.cache_control import CacheControlMiddleware
from core_toolkit.middleware.coep import COEPMiddleware
from core_toolkit.middleware.coop import COOPMiddleware
from core_toolkit.middleware.corp import CORPMiddleware
from core_toolkit.middleware.csp import CSPMiddleware
from core_toolkit.middleware.dns_prefetch_control import DNSPrefetchControlMiddleware
from core_toolkit.middleware.frame_guard import FrameGuardMiddleware
from core_toolkit.middleware.host_validation import HostValidationMiddleware
from core_toolkit.middleware.hsts import HSTSMiddleware
from core_toolkit.middleware.json_content_type import JsonContentTypeMiddleware
from core_toolkit.middleware.no_sniff import NoSniffMiddleware
from core_toolkit.middleware.origin_validation import OriginValidationMiddleware
from core_toolkit.middleware.permissions_policy import PermissionsPolicyMiddleware
from core_toolkit.middleware.referrer_policy import ReferrerPolicyMiddleware
from core_toolkit.middleware.robots_tag import RobotsTagMiddleware
from core_toolkit.middleware.server_header_strip import ServerHeaderStripMiddleware
from core_toolkit.middleware.sse_buffering import SSEBufferingMiddleware
from core_toolkit.middleware.xss_protection import XSSProtectionMiddleware

__all__ = [
    "AccessLogMiddleware",
    "CacheControlMiddleware",
    "COEPMiddleware",
    "COOPMiddleware",
    "CORPMiddleware",
    "CSPMiddleware",
    "DNSPrefetchControlMiddleware",
    "FrameGuardMiddleware",
    "HostValidationMiddleware",
    "HSTSMiddleware",
    "JsonContentTypeMiddleware",
    "NoSniffMiddleware",
    "OriginValidationMiddleware",
    "PermissionsPolicyMiddleware",
    "ReferrerPolicyMiddleware",
    "RobotsTagMiddleware",
    "ServerHeaderStripMiddleware",
    "SSEBufferingMiddleware",
    "XSSProtectionMiddleware",
]
