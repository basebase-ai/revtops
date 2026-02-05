/**
 * Centralized API configuration.
 *
 * Single source of truth for API URLs and common request helpers.
 * Includes JWT authentication and masquerade support for admin impersonation.
 *
 * SECURITY: All API requests include the Supabase JWT token in the
 * Authorization header. The backend verifies this token to authenticate
 * the user - never trust user_id or organization_id from query parameters.
 */

import { getAdminUserId } from "../store";
import { supabase } from "./supabase";

// Backend URL for production
const PRODUCTION_BACKEND = "https://api.revtops.com";

// Determine if we're in production (Railway)
export const isProduction: boolean =
  typeof window !== "undefined" &&
  (window.location.hostname.includes("railway.app") ||
    window.location.hostname.includes("revtops"));

// API base URL
export const API_BASE: string = isProduction
  ? `${PRODUCTION_BACKEND}/api`
  : "/api";

// WebSocket base URL - in dev, connect directly to backend (Vite doesn't proxy WebSocket)
export const WS_BASE: string = isProduction
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
  const baseUrl = `${WS_BASE}${path}`;
  return `${baseUrl}?token=${encodeURIComponent(token)}`;
}

/**
 * Make an API request with standard error handling.
 * Automatically includes:
 * - Authorization header with Supabase JWT token
 * - X-Admin-User-Id header when masquerading
 */
export async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  // Add Authorization header with Supabase JWT token
  const accessToken = await getAccessToken();
  if (accessToken) {
    (headers as Record<string, string>)["Authorization"] = `Bearer ${accessToken}`;
  }

  // Add admin user ID header when masquerading
  const adminUserId = getAdminUserId();
  if (adminUserId) {
    (headers as Record<string, string>)["X-Admin-User-Id"] = adminUserId;
  }

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
