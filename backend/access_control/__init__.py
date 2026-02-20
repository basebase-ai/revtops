"""
Access control layer: Rights Management and Data Protection.

All SQL/external API and connector flows pass through these stubs so future
modules can inject secrets or block operations per user/context.
"""

from access_control.rights import (
    RightsContext,
    RightsResult,
    check_sql,
    check_external_api,
)
from access_control.data_protection import (
    ConnectorContext,
    DataProtectionResult,
    check_connector_call,
)

__all__ = [
    "RightsContext",
    "RightsResult",
    "check_sql",
    "check_external_api",
    "ConnectorContext",
    "DataProtectionResult",
    "check_connector_call",
]
