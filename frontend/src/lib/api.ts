/**
 * Centralized API configuration.
 * 
 * Single source of truth for API URLs and common request helpers.
 */

// Backend URL for production
const PRODUCTION_BACKEND = 'https://api.revtops.com';

// Determine if we're in production (Railway)
export const isProduction: boolean = typeof window !== 'undefined' && 
  (window.location.hostname.includes('railway.app') || 
   window.location.hostname.includes('revtops'));

// API base URL
export const API_BASE: string = isProduction 
  ? `${PRODUCTION_BACKEND}/api`
  : '/api';

// WebSocket base URL
export const WS_BASE: string = isProduction
  ? PRODUCTION_BACKEND.replace(/^http/, 'ws')
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;

/**
 * Standard API response wrapper
 */
export interface ApiResponse<T> {
  data: T | null;
  error: string | null;
}

/**
 * Make an API request with standard error handling
 */
export async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({})) as { detail?: string };
      return {
        data: null,
        error: errorData.detail ?? `HTTP ${response.status}`,
      };
    }

    const data = await response.json() as T;
    return { data, error: null };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : 'Network error',
    };
  }
}

// Debug log (can be removed after confirming it works)
if (typeof window !== 'undefined') {
  console.log('[API Config] isProduction:', isProduction, 'API_BASE:', API_BASE);
}
