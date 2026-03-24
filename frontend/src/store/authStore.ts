/**
 * Auth store — user, session, organizations, masquerade state, auth actions.
 *
 * Split from the monolithic AppState store for performance: only components
 * that read auth-related fields re-render when auth state changes.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { API_BASE, apiRequest } from "../lib/api";
import type {
  UserProfile,
  OrganizationInfo,
  UserOrganization,
  MasqueradeState,
} from "./types";
import { useChatStore } from "./chatStore";
import { useUIStore } from "./uiStore";

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

export interface AuthState {
  // State
  user: UserProfile | null;
  organization: OrganizationInfo | null;
  organizations: UserOrganization[];
  isAuthenticated: boolean;
  masquerade: MasqueradeState | null;
  isSwitchingOrg: boolean;

  // Actions
  setUser: (user: UserProfile | null) => void;
  setOrganization: (org: OrganizationInfo | null) => void;
  setOrganizations: (orgs: UserOrganization[]) => void;
  fetchUserOrganizations: () => Promise<void>;
  switchActiveOrganization: (orgId: string) => Promise<void>;
  logout: () => void;
  startMasquerade: (
    targetUser: UserProfile,
    targetOrg: OrganizationInfo | null,
  ) => void;
  exitMasquerade: () => void;
  syncUserToBackend: () => Promise<string | null>;
}

// ---------------------------------------------------------------------------
// Store implementation
// ---------------------------------------------------------------------------

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      // Initial state
      user: null,
      organization: null,
      organizations: [],
      isAuthenticated: false,
      masquerade: null,
      isSwitchingOrg: false,

      // Actions
      setUser: (user) =>
        set({
          user,
          isAuthenticated: user !== null,
        }),

      setOrganization: (organization) => set({ organization }),

      setOrganizations: (organizations) => set({ organizations }),

      fetchUserOrganizations: async () => {
        const { user } = get();
        if (!user) return;

        interface OrgApiResponse {
          id: string;
          name: string;
          logo_url: string | null;
          handle?: string | null;
          role: string;
          is_active: boolean;
        }

        const { data, error } = await apiRequest<{
          organizations: OrgApiResponse[];
        }>("/auth/users/me/organizations");

        if (error || !data) {
          console.error(
            "[Store] Failed to fetch user organizations:",
            error ?? "unknown",
          );
          return;
        }

        const organizations: UserOrganization[] = data.organizations.map(
          (o) => ({
            id: o.id,
            name: o.name,
            logoUrl: o.logo_url,
            handle: o.handle ?? null,
            role: o.role,
            isActive: o.is_active,
          }),
        );

        console.log(
          "[Store] Fetched",
          organizations.length,
          "user organizations",
        );
        set({ organizations });
      },

      switchActiveOrganization: async (orgId: string) => {
        const { user, organizations } = get();
        if (!user) return;

        const { data, error } = await apiRequest<{
          organization: {
            id: string;
            name: string;
            logo_url: string | null;
            handle?: string | null;
          } | null;
        }>("/auth/users/me/active-organization", {
          method: "PATCH",
          body: JSON.stringify({ organization_id: orgId }),
        });

        if (error) {
          console.error("[Store] Failed to switch org:", error);
          alert(error);
          return;
        }

        if (data?.organization) {
          const orgHandle: string | null =
            data.organization.handle ??
            organizations.find((o) => o.id === orgId)?.handle ??
            null;
          const orgInList = organizations.find((o) => o.id === orgId);
          const updatedOrgs: UserOrganization[] = orgInList
            ? organizations.map((o) => ({ ...o, isActive: o.id === orgId }))
            : [
                ...organizations.map((o) => ({ ...o, isActive: false })),
                {
                  id: data.organization.id,
                  name: data.organization.name,
                  logoUrl: data.organization.logo_url,
                  handle: orgHandle,
                  role: "admin",
                  isActive: true,
                },
              ];

          set({
            organization: {
              id: data.organization.id,
              name: data.organization.name,
              logoUrl: data.organization.logo_url,
              handle: orgHandle,
            },
            organizations: updatedOrgs,
          });

          // Clear org-scoped state in other stores
          useChatStore.setState({
            currentChatId: null,
            recentChats: [],
            conversations: {},
            activeTasksByConversation: {},
            integrations: [],
          });
          useUIStore.setState({
            currentView: "home",
            currentAppId: null,
            currentArtifactId: null,
          });
        }

        console.log("[Store] Switched active organization to:", orgId);
      },

      logout: () => {
        set({
          user: null,
          organization: null,
          organizations: [],
          isAuthenticated: false,
          masquerade: null,
        });

        // Clear other stores on logout
        useChatStore.setState({
          currentChatId: null,
          recentChats: [],
          conversations: {},
          activeTasksByConversation: {},
          integrations: [],
          integrationsLoading: false,
          integrationsError: null,
          pendingChatInput: null,
          pendingChatAutoSend: false,
          messages: [],
          chatTitle: "New Chat",
          isThinking: false,
          streamingMessageId: null,
          conversationId: null,
        });
        useUIStore.setState({
          pinnedChatIds: [],
        });
      },

      startMasquerade: (targetUser, targetOrg) => {
        const { user, organization } = get();
        if (!user) return;

        console.log("[Store] Starting masquerade as:", targetUser.email);
        set({
          masquerade: {
            originalUser: user,
            originalOrganization: organization,
            masqueradingAs: targetUser,
            masqueradeOrganization: targetOrg,
          },
          user: targetUser,
          organization: targetOrg,
        });

        // Clear chat state when switching users
        useChatStore.setState({
          currentChatId: null,
          recentChats: [],
          conversations: {},
          activeTasksByConversation: {},
        });
        useUIStore.setState({
          pinnedChatIds: [],
        });
      },

      exitMasquerade: () => {
        const { masquerade } = get();
        if (!masquerade) return;

        console.log(
          "[Store] Exiting masquerade, returning to:",
          masquerade.originalUser.email,
        );
        set({
          user: masquerade.originalUser,
          organization: masquerade.originalOrganization,
          masquerade: null,
        });

        // Clear chat state when switching back
        useChatStore.setState({
          currentChatId: null,
          recentChats: [],
          conversations: {},
          activeTasksByConversation: {},
        });
        useUIStore.setState({
          pinnedChatIds: [],
        });
      },

      syncUserToBackend: async (): Promise<string | null> => {
        const { user, organization, setUser } = get();
        if (!user) return null;

        try {
          console.log(
            "[Store] Syncing user to backend:",
            user.id,
            user.email,
            organization?.id,
          );
          const response = await fetch(`${API_BASE}/auth/users/sync`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id: user.id,
              email: user.email,
              name: user.name,
              avatar_url: user.avatarUrl,
              organization_id: organization?.id,
            }),
          });

          if (!response.ok) {
            if (response.status === 403) {
              console.log("[Store] User not on waitlist");
              return "not_registered";
            }
            const errorData = (await response.json().catch(() => ({}))) as {
              detail?: string;
            };
            throw new Error(errorData.detail ?? `HTTP ${response.status}`);
          }

          const data = (await response.json()) as {
            id: string;
            status: string;
            avatar_url: string | null;
            name: string | null;
            phone_number: string | null;
            job_title: string | null;
            roles: string[];
            sms_consent?: boolean;
            whatsapp_consent?: boolean;
            phone_number_verified?: boolean;
            organization: {
              id: string;
              name: string;
              logo_url: string | null;
              handle?: string | null;
            } | null;
          };
          console.log(
            "[Store] User synced successfully, status:",
            data.status,
          );

          const newRoles = data.roles ?? [];
          const newSmsConsent = data.sms_consent ?? user.smsConsent;
          const newWhatsappConsent = data.whatsapp_consent ?? user.whatsappConsent;
          const newPhoneVerified = data.phone_number_verified ?? user.phoneNumberVerified;
          if (
            data.id !== user.id ||
            data.avatar_url !== user.avatarUrl ||
            data.name !== user.name ||
            data.phone_number !== user.phoneNumber ||
            data.job_title !== user.jobTitle ||
            JSON.stringify(newRoles) !== JSON.stringify(user.roles) ||
            newSmsConsent !== user.smsConsent ||
            newWhatsappConsent !== user.whatsappConsent ||
            newPhoneVerified !== user.phoneNumberVerified
          ) {
            setUser({
              ...user,
              id: data.id,
              name: data.name ?? user.name,
              avatarUrl: data.avatar_url ?? user.avatarUrl,
              phoneNumber: data.phone_number,
              jobTitle: data.job_title ?? user.jobTitle,
              roles: newRoles,
              smsConsent: newSmsConsent,
              whatsappConsent: newWhatsappConsent,
              phoneNumberVerified: newPhoneVerified,
            });
          }

          if (data.organization) {
            const { setOrganization } = get();
            setOrganization({
              id: data.organization.id,
              name: data.organization.name,
              logoUrl: data.organization.logo_url,
              handle: data.organization.handle ?? null,
            });
          }

          return data.status;
        } catch (error) {
          console.error("[Store] Failed to sync user to backend:", error);
          return null;
        }
      },
    }),
    {
      name: "revtops-auth-store",
      partialize: (state) => ({
        user: state.user,
        organization: state.organization,
        organizations: state.organizations,
        isAuthenticated: state.isAuthenticated,
        masquerade: state.masquerade,
      }),
    },
  ),
);
