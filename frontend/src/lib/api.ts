/**
 * Centralized API configuration.
 *
 * Single source of truth for API URLs and common request helpers.
 * Includes JWT authentication and masquerade support for admin impersonation.
 *
 * SECURITY: JWT authenticates the user. Active org is sent as X-Organization-Id;
 * the backend validates membership (org_members) and must not trust org id from
 * query params alone for authorization.
 */

import { getAdminUserId, getMasqueradeUserId } from "../store";
import { supabase } from "./supabase";

// Backend URL for production (override with VITE_API_URL at build time)
const PRODUCTION_BACKEND = "https://api.basebase.com";
const DEV_API_BASE = "http://localhost:8000/api";

const envApiUrl: string | undefined = import.meta.env.VITE_API_URL;

export const isProduction: boolean =
  typeof window !== "undefined" &&
  (window.location.hostname.includes("basebase.com") ||
    window.location.hostname.includes("railway.app"));

// API base URL (prefer VITE_API_URL when set at build)
export const API_BASE: string =
  envApiUrl
    ? `${envApiUrl.replace(/\/$/, "")}/api`
    : isProduction
      ? `${PRODUCTION_BACKEND}/api`
      : DEV_API_BASE;

export const WS_BASE: string =
  envApiUrl
    ? envApiUrl.replace(/^http/, "ws").replace(/\/$/, "")
    : isProduction
      ? PRODUCTION_BACKEND.replace(/^http/, "ws")
      : "ws://localhost:8000";

/**
 * Standard API response wrapper
 */
export interface ApiResponse<T> {
  data: T | null;
  error: string | null;
}

/**
 * Get the current Supabase access token.
 * Returns null if not authenticated.
 */
async function getAccessToken(): Promise<string | null> {
  const { data: { session } } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}

/** Active org for API scope; dynamic import avoids authStore ↔ api circular dependency. */
async function getActiveOrganizationIdForRequest(): Promise<string | null> {
  const { useAuthStore } = await import("../store/authStore");
  const orgId: string | undefined = useAuthStore.getState().organization?.id;
  return orgId ?? null;
}

/**
 * Headers for authenticated fetch() calls that bypass apiRequest.
 * Includes JWT, optional X-Organization-Id (membership-validated server-side), masquerade.
 */
export async function getAuthenticatedRequestHeaders(): Promise<
  Record<string, string>
> {
  const headers: Record<string, string> = {};
  const accessToken: string | null = await getAccessToken();
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const activeOrgId: string | null = await getActiveOrganizationIdForRequest();
  if (activeOrgId) {
    headers["X-Organization-Id"] = activeOrgId;
  }
  const adminUserId: string | null = getAdminUserId();
  if (adminUserId) {
    headers["X-Admin-User-Id"] = adminUserId;
  }
  const masqueradeUserId: string | null = getMasqueradeUserId();
  if (masqueradeUserId) {
    headers["X-Masquerade-User-Id"] = masqueradeUserId;
  }
  return headers;
}

/**
 * Build WebSocket URL with authentication token.
 * Use this instead of manually constructing WS URLs.
 *
 * @param path - WebSocket endpoint path (e.g., "/ws/chat")
 * @returns Full WebSocket URL with token query parameter, or null if not authenticated
 */
export async function getAuthenticatedWsUrl(path: string): Promise<string | null> {
  const token = await getAccessToken();
  if (!token) {
    return null;
  }
  const orgId: string | null = await getActiveOrganizationIdForRequest();
  const baseUrl = `${WS_BASE}${path}`;
  const params = new URLSearchParams({ token });
  if (orgId) {
    params.set("org_id", orgId);
  }
  return `${baseUrl}?${params.toString()}`;
}

/**
 * Make an API request with standard error handling.
 * Automatically includes:
 * - Authorization header with Supabase JWT token
 * - X-Admin-User-Id header when masquerading
 * - X-Masquerade-User-Id header when masquerading
 * - X-Organization-Id when the user has a selected organization (validated server-side)
 */
export async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const authHeaders: Record<string, string> = await getAuthenticatedRequestHeaders();
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...authHeaders,
    ...options.headers,
  };

  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorData = (await response.json().catch(() => ({}))) as {
        detail?: string;
      };
      return {
        data: null,
        error: errorData.detail ?? `HTTP ${response.status}`,
      };
    }

    const data = (await response.json()) as T;
    return { data, error: null };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : "Network error",
    };
  }
}
