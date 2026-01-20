"""
Nango client for OAuth and credential management.

Nango handles:
- OAuth flows for all integrations
- Token storage and encryption
- Automatic token refresh
- Connection management
"""

from typing import Any, Optional

import httpx

from config import settings

NANGO_API_BASE = "https://api.nango.dev"


class NangoClient:
    """Client for interacting with Nango API."""

    def __init__(self, secret_key: Optional[str] = None) -> None:
        """
        Initialize Nango client.

        Args:
            secret_key: Nango secret key. Falls back to settings if not provided.
        """
        self.secret_key = secret_key or settings.NANGO_SECRET_KEY
        if not self.secret_key:
            raise ValueError("NANGO_SECRET_KEY is required")

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Nango API."""
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def get_connection(
        self,
        integration_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        """
        Get a connection's details from Nango.

        Args:
            integration_id: The Nango integration ID (e.g., 'hubspot', 'slack')
            connection_id: The unique connection identifier (e.g., customer_id)

        Returns:
            Connection details including credentials
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{NANGO_API_BASE}/connection/{connection_id}",
                headers=self._get_headers(),
                params={"provider_config_key": integration_id},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def get_token(
        self,
        integration_id: str,
        connection_id: str,
    ) -> str:
        """
        Get an access token for a connection.

        Nango automatically handles token refresh.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier

        Returns:
            Valid access token
        """
        connection = await self.get_connection(integration_id, connection_id)
        credentials = connection.get("credentials", {})

        # Handle different credential types
        if "access_token" in credentials:
            return credentials["access_token"]
        elif "api_key" in credentials:
            return credentials["api_key"]
        else:
            raise ValueError(f"No token found for {integration_id}:{connection_id}")

    async def get_credentials(
        self,
        integration_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        """
        Get full credentials for a connection.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier

        Returns:
            Full credentials dict (may include access_token, refresh_token, etc.)
        """
        connection = await self.get_connection(integration_id, connection_id)
        return connection.get("credentials", {})

    async def list_connections(
        self,
        connection_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        List all connections, optionally filtered by connection_id.

        Args:
            connection_id: Optional filter by connection ID

        Returns:
            List of connections
        """
        async with httpx.AsyncClient() as client:
            params: dict[str, str] = {}
            if connection_id:
                params["connectionId"] = connection_id

            response = await client.get(
                f"{NANGO_API_BASE}/connections",
                headers=self._get_headers(),
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("connections", [])

    async def delete_connection(
        self,
        integration_id: str,
        connection_id: str,
    ) -> bool:
        """
        Delete a connection from Nango.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier

        Returns:
            True if deleted successfully
        """
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{NANGO_API_BASE}/connection/{connection_id}",
                headers=self._get_headers(),
                params={"provider_config_key": integration_id},
                timeout=30.0,
            )
            return response.status_code == 204

    async def get_integration_metadata(
        self,
        integration_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        """
        Get metadata stored with a connection.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier

        Returns:
            Connection metadata
        """
        connection = await self.get_connection(integration_id, connection_id)
        return connection.get("metadata", {})

    async def set_integration_metadata(
        self,
        integration_id: str,
        connection_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        """
        Set metadata for a connection.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier
            metadata: Metadata to store

        Returns:
            True if updated successfully
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{NANGO_API_BASE}/connection/{connection_id}/metadata",
                headers=self._get_headers(),
                params={"provider_config_key": integration_id},
                json=metadata,
                timeout=30.0,
            )
            return response.status_code == 200

    def get_connect_url(
        self,
        integration_id: str,
        connection_id: str,
        redirect_url: Optional[str] = None,
    ) -> str:
        """
        Generate a Nango Connect URL for OAuth.

        This URL redirects users to Nango's hosted OAuth flow.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier
            redirect_url: URL to redirect to after OAuth completes

        Returns:
            Nango Connect URL
        """
        # Use public key for connect URL (derived from secret key format)
        # In production, you'd use the actual public key from Nango dashboard
        base_url = f"https://api.nango.dev/oauth/connect/{integration_id}"
        params = [f"connection_id={connection_id}"]

        if redirect_url:
            params.append(f"redirect_url={redirect_url}")

        return f"{base_url}?{'&'.join(params)}"


# Singleton instance
nango_client = NangoClient() if settings.NANGO_SECRET_KEY else None


def get_nango_client() -> NangoClient:
    """Get the Nango client instance."""
    if nango_client is None:
        raise ValueError("Nango is not configured. Set NANGO_SECRET_KEY.")
    return nango_client
