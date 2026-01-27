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

    def __init__(
        self,
        secret_key: Optional[str] = None,
        public_key: Optional[str] = None,
    ) -> None:
        """
        Initialize Nango client.

        Args:
            secret_key: Nango secret key. Falls back to settings if not provided.
            public_key: Nango public key for connect URLs. Falls back to settings.
        """
        self.secret_key = secret_key or settings.NANGO_SECRET_KEY
        self.public_key = public_key or settings.NANGO_PUBLIC_KEY
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
            connection_id: The unique connection identifier (e.g., organization_id)

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

        # Debug: log what credentials Nango returned
        print(f"[Nango] Credentials for {integration_id}:{connection_id}: {list(credentials.keys())}")

        # Handle different credential types
        if "access_token" in credentials:
            return credentials["access_token"]
        elif "api_key" in credentials:
            return credentials["api_key"]
        elif "apiKey" in credentials:
            return credentials["apiKey"]
        elif "token" in credentials:
            return credentials["token"]
        else:
            print(f"[Nango] Full credentials object: {credentials}")
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
        end_user_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        List all connections, optionally filtered by end_user_id.

        Args:
            end_user_id: Optional filter by end user ID (organization ID)

        Returns:
            List of connections
        """
        async with httpx.AsyncClient() as client:
            params: dict[str, str] = {}
            # Nango uses endUserId to filter connections by the end_user.id we set during session creation
            if end_user_id:
                params["endUserId"] = end_user_id

            response = await client.get(
                f"{NANGO_API_BASE}/connections",
                headers=self._get_headers(),
                params=params,
                timeout=30.0,
            )
            
            if response.status_code != 200:
                print(f"Nango list connections failed ({response.status_code}): {response.text}")
                return []
            
            data = response.json()
            connections = data.get("connections", [])
            
            # If filtering didn't work via API, filter locally
            if end_user_id and connections:
                filtered = [
                    c for c in connections
                    if c.get("end_user", {}).get("id") == end_user_id
                ]
                if filtered:
                    return filtered
            
            return connections

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

    async def create_connect_session(
        self,
        integration_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        """
        Create a Nango Connect session for OAuth.

        Returns the session token for use with the frontend SDK.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier (e.g., "{org_id}:user:{user_id}")

        Returns:
            Dict with token and other session info
        """
        async with httpx.AsyncClient() as client:
            payload: dict[str, Any] = {
                "end_user": {
                    "id": connection_id,
                },
                "allowed_integrations": [integration_id],
            }

            response = await client.post(
                f"{NANGO_API_BASE}/connect/sessions",
                headers=self._get_headers(),
                json=payload,
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                raise ValueError(f"Failed to create Nango session: {response.text}")

            response_data = response.json()
            # Token is nested inside 'data' object
            data = response_data.get("data", response_data)
            return {
                "token": data.get("token"),
                "expires_at": data.get("expires_at"),
            }

    async def get_connect_url(
        self,
        integration_id: str,
        connection_id: str,
        redirect_url: Optional[str] = None,
    ) -> str:
        """
        Generate a Nango Connect URL for OAuth.

        Creates a session token and returns the connect URL.

        Args:
            integration_id: The Nango integration ID
            connection_id: The unique connection identifier
            redirect_url: URL to redirect to after OAuth completes

        Returns:
            Nango Connect URL with session token
        """
        from urllib.parse import quote

        # Try to create a connect session via API
        async with httpx.AsyncClient() as client:
            payload: dict[str, Any] = {
                "end_user": {
                    "id": connection_id,
                },
                "allowed_integrations": [integration_id],
            }

            try:
                response = await client.post(
                    f"{NANGO_API_BASE}/connect/sessions",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=30.0,
                )
                
                # Log non-success responses for debugging
                if response.status_code not in (200, 201):
                    print(f"Nango session creation failed ({response.status_code}): {response.text}")

                if response.status_code in (200, 201):
                    response_data = response.json()
                    # Token is nested inside 'data' object
                    data = response_data.get("data", response_data)
                    
                    # Nango provides a connect_link - use it directly
                    # The session already knows allowed integrations
                    connect_link = data.get("connect_link")
                    if connect_link:
                        # Append redirect_url if provided
                        if redirect_url:
                            separator = "&" if "?" in connect_link else "?"
                            connect_link = f"{connect_link}{separator}redirect_url={quote(redirect_url)}"
                        
                        return connect_link
                    
                    # Fallback: build URL from token
                    token = data.get("token")
                    if token:
                        base_url = "https://connect.nango.dev"
                        if redirect_url:
                            return f"{base_url}?session_token={token}&redirect_url={quote(redirect_url)}"
                        return f"{base_url}?session_token={token}"
                    
                    print(f"Nango session response (unexpected format): {response_data}")
            except Exception as e:
                print(f"Nango session creation failed: {e}")

        # Fallback: Use direct OAuth URL (works with some Nango setups)
        # This initiates OAuth flow directly through Nango's backend
        base_url = f"https://api.nango.dev/oauth/connect/{integration_id}"
        params = [f"connection_id={connection_id}"]
        if redirect_url:
            params.append(f"redirect_url={quote(redirect_url)}")
        
        return f"{base_url}?{'&'.join(params)}"


# Singleton instance
nango_client = NangoClient() if settings.NANGO_SECRET_KEY else None


def get_nango_client() -> NangoClient:
    """Get the Nango client instance."""
    if nango_client is None:
        raise ValueError("Nango is not configured. Set NANGO_SECRET_KEY.")
    return nango_client
