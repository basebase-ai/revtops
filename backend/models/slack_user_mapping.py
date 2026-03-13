"""
Backward-compatibility alias — use ``ExternalIdentityMapping`` directly.

This file will be removed once all imports are migrated.
"""
from models.external_identity_mapping import ExternalIdentityMapping

SlackUserMapping = ExternalIdentityMapping

__all__ = ["SlackUserMapping"]
