/**
 * Zustand store facade — backward-compatible re-export of split stores.
 *
 * The store has been split into three focused stores for performance:
 *   - authStore  — user, session, organizations, masquerade
 *   - chatStore  — conversations, messages, streaming, integrations
 *   - uiStore    — sidebar, current view, pinned chats, UI preferences
 *
 * This module re-exports everything so that existing `import { useAppStore }
 * from '../store'` calls continue to work without modification. Components
 * that want optimal re-render behavior can import from the sub-stores
 * directly (e.g., `useAuthStore(s => s.user)`).
 */

import { useShallow } from "zustand/react/shallow";
import { useAuthStore, type AuthState } from "./authStore";
import { useChatStore, type ChatState } from "./chatStore";
import { useUIStore, type UIState } from "./uiStore";

// =============================================================================
// Re-export all types from the shared types module
// =============================================================================

export type {
  UserProfile,
  MasqueradeState,
  OrganizationInfo,
  UserOrganization,
  TeamConnection,
  SyncStats,
  Integration,
  Participant,
  ChatSummary,
  TextBlock,
  ToolUseBlock,
  ErrorBlock,
  ArtifactBlock,
  AppBlock,
  ThinkingBlock,
  AttachmentBlock,
  ContentBlock,
  ToolCallData,
  ChatMessage,
  PendingChunk,
  ConversationState,
  ConversationSummaryText,
  TypingUserEntry,
  ActiveTask,
  View,
  AdminPanelTab,
} from "./types";

// Re-export sub-stores for direct use by performance-conscious components
export { useAuthStore } from "./authStore";
export { useChatStore } from "./chatStore";
export { useUIStore } from "./uiStore";

// =============================================================================
// Combined AppState type (union of all three sub-stores)
// =============================================================================

type AppState = AuthState & ChatState & UIState;

// =============================================================================
// useAppStore — backward-compatible facade
// =============================================================================

/**
 * Merges current state from all three sub-stores into a single object.
 * Used by `useAppStore.getState()` and `useAppStore.setState()`.
 */
function getMergedState(): AppState {
  return {
    ...useAuthStore.getState(),
    ...useChatStore.getState(),
    ...useUIStore.getState(),
  } as AppState;
}

/**
 * Routes a partial state update to the correct sub-store(s).
 */
function setMergedState(partial: Partial<AppState>): void {
  const authKeys = new Set<string>(Object.keys(useAuthStore.getState()));
  const chatKeys = new Set<string>(Object.keys(useChatStore.getState()));
  const uiKeys = new Set<string>(Object.keys(useUIStore.getState()));

  const authPatch: Record<string, unknown> = {};
  const chatPatch: Record<string, unknown> = {};
  const uiPatch: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(partial)) {
    if (authKeys.has(key)) {
      authPatch[key] = value;
    }
    if (chatKeys.has(key)) {
      chatPatch[key] = value;
    }
    if (uiKeys.has(key)) {
      uiPatch[key] = value;
    }
  }

  if (Object.keys(authPatch).length > 0) {
    useAuthStore.setState(authPatch as Partial<AuthState>);
  }
  if (Object.keys(chatPatch).length > 0) {
    useChatStore.setState(chatPatch as Partial<ChatState>);
  }
  if (Object.keys(uiPatch).length > 0) {
    useUIStore.setState(uiPatch as Partial<UIState>);
  }
}

/**
 * `useAppStore` hook — backward-compatible with all existing usage patterns:
 *
 *   useAppStore((s) => s.user)              // selector
 *   useAppStore(useShallow((s) => ({...}))) // shallow selector
 *   useAppStore()                           // entire state (discouraged)
 *   useAppStore.getState()                  // non-reactive read
 *   useAppStore.setState({...})             // direct mutation
 */
function useAppStoreHook(): AppState;
function useAppStoreHook<T>(selector: (state: AppState) => T): T;
function useAppStoreHook<T>(selector?: (state: AppState) => T): T | AppState {
  const auth = useAuthStore();
  const chat = useChatStore();
  const ui = useUIStore();

  const merged = { ...auth, ...chat, ...ui } as AppState;

  if (selector) {
    return selector(merged);
  }
  return merged;
}

/**
 * The public `useAppStore` object with `.getState()` and `.setState()`.
 */
export const useAppStore = Object.assign(useAppStoreHook, {
  getState: getMergedState,
  setState: setMergedState,
});

// NOTE: The store implementation lives in authStore.ts, chatStore.ts, uiStore.ts

// =============================================================================
// Selector Hooks (for convenience)
// =============================================================================

export const useUser = () => useAuthStore((state) => state.user);
export const useOrganization = () =>
  useAuthStore((state) => state.organization);
export const useOrganizations = () =>
  useAuthStore((state) => state.organizations);
export const useIsAuthenticated = () =>
  useAuthStore((state) => state.isAuthenticated);
export const useSidebarCollapsed = () =>
  useUIStore((state) => state.sidebarCollapsed);
export const useCurrentView = () => useUIStore((state) => state.currentView);
/** True if the signed-in user or the pre-masquerade admin is global_admin (UI access while impersonating). */
export const useIsGlobalAdmin = (): boolean =>
  useAuthStore((state) => {
    if (state.user?.roles?.includes("global_admin")) return true;
    if (state.masquerade?.originalUser.roles?.includes("global_admin"))
      return true;
    return false;
  });
/** True if the signed-in user is an admin of the current organization. */
export const useIsOrgAdmin = (): boolean =>
  useAuthStore((state) => {
    const orgId = state.organization?.id;
    if (!orgId) return false;
    const membership = state.organizations.find((o) => o.id === orgId);
    if (membership?.role === 'admin') return true;
    // Global admins can always see org-admin features
    if (state.user?.roles?.includes("global_admin")) return true;
    if (state.masquerade?.originalUser.roles?.includes("global_admin")) return true;
    return false;
  });
export const useMasquerade = () => useAuthStore((state) => state.masquerade);
export const useIsMasquerading = () =>
  useAuthStore((state) => state.masquerade !== null);
export const useIsSwitchingOrg = () =>
  useAuthStore((state) => state.isSwitchingOrg);

// Get the real admin user ID when masquerading (for API headers)
export const getAdminUserId = (): string | null => {
  const state = useAuthStore.getState();
  return state.masquerade?.originalUser.id ?? null;
};

// Get the target user ID when masquerading (for API impersonation headers)
export const getMasqueradeUserId = (): string | null => {
  const state = useAuthStore.getState();
  return state.masquerade?.masqueradingAs.id ?? null;
};

// Legacy chat selectors (for backwards compatibility)
export const useMessages = () => useChatStore((state) => state.messages);
export const useChatTitle = () => useChatStore((state) => state.chatTitle);
export const useIsThinking = () => useChatStore((state) => state.isThinking);
export const useConversationId = () =>
  useChatStore((state) => state.conversationId);

// Per-conversation selectors
export const useConversationState = (conversationId: string | null) =>
  useChatStore((state) =>
    conversationId
      ? (state.conversations[conversationId] ?? null)
      : null,
  );
export const useConversationMessages = (conversationId: string | null) =>
  useChatStore((state) =>
    conversationId
      ? (state.conversations[conversationId]?.messages ?? [])
      : [],
  );
export const useActiveTasksByConversation = () =>
  useChatStore((state) => state.activeTasksByConversation);
export const useHasActiveTask = (conversationId: string | null) =>
  useChatStore((state) =>
    conversationId
      ? conversationId in state.activeTasksByConversation
      : false,
  );

// Integration selectors
export const useIntegrations = () =>
  useChatStore((state) => state.integrations);
export const useIntegrationsLoading = () =>
  useChatStore((state) => state.integrationsLoading);
export const useIntegrationsError = () =>
  useChatStore((state) => state.integrationsError);
export const useIntegration = (provider: string) =>
  useChatStore(
    (state) =>
      state.integrations.find((i) => i.provider === provider) ?? null,
  );
export const useConnectedIntegrations = () =>
  useChatStore(
    useShallow((state) => state.integrations.filter((i) => i.isActive)),
  );

// =============================================================================
// Migration: read legacy "revtops-store" localStorage and seed new stores
// =============================================================================

(function migrateLegacyStore() {
  if (typeof window === "undefined") return;

  const LEGACY_KEY = "revtops-store";
  const raw = localStorage.getItem(LEGACY_KEY);
  if (!raw) return;

  try {
    const parsed = JSON.parse(raw) as {
      state?: Record<string, unknown>;
    };
    const legacy = parsed?.state;
    if (!legacy) return;

    // Only migrate if the new auth store is empty (first load after upgrade)
    const authState = useAuthStore.getState();
    if (authState.user !== null) return; // already populated

    // Seed auth store
    interface LegacyUser {
      id: string;
      email: string;
      name: string | null;
      avatarUrl: string | null;
      phoneNumber: string | null;
      jobTitle: string | null;
      roles: string[];
      smsConsent: boolean;
      whatsappConsent: boolean;
      phoneNumberVerified: boolean;
    }
    interface LegacyOrg {
      id: string;
      name: string;
      logoUrl: string | null;
    }
    interface LegacyUserOrg {
      id: string;
      name: string;
      logoUrl: string | null;
      role: string;
      isActive: boolean;
    }
    const rawUser = legacy.user as Partial<LegacyUser> | null;
    const user: LegacyUser | null = rawUser
      ? {
          ...rawUser,
          smsConsent: rawUser.smsConsent ?? false,
          whatsappConsent: rawUser.whatsappConsent ?? false,
          phoneNumberVerified: rawUser.phoneNumberVerified ?? false,
        } as LegacyUser
      : null;
    if (user) {
      useAuthStore.setState({
        user,
        organization: (legacy.organization as LegacyOrg | null) ?? null,
        organizations: (legacy.organizations as LegacyUserOrg[]) ?? [],
        isAuthenticated: (legacy.isAuthenticated as boolean) ?? false,
        masquerade:
          (legacy.masquerade as AuthState["masquerade"]) ?? null,
      });
    }

    // Seed UI store
    const uiPatch: Partial<UIState> = {};
    if (typeof legacy.sidebarCollapsed === "boolean")
      uiPatch.sidebarCollapsed = legacy.sidebarCollapsed;
    if (typeof legacy.sidebarWidth === "number")
      uiPatch.sidebarWidth = legacy.sidebarWidth;
    if (Array.isArray(legacy.pinnedChatIds))
      uiPatch.pinnedChatIds = legacy.pinnedChatIds as string[];
    if (Object.keys(uiPatch).length > 0) {
      useUIStore.setState(uiPatch);
    }

    // Remove legacy key so migration only runs once
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    // Ignore parse errors — legacy key may be corrupted
  }
})();
