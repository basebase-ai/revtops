/**
 * UI store — sidebar state, current view, pinned chats, UI preferences.
 *
 * Split from the monolithic AppState store for performance: only components
 * that read UI-related fields re-render when UI state changes.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { AdminPanelTab, View } from "./types";
import { useChatStore } from "./chatStore";

/** User-selected color theme; `system` follows OS preference. */
export type UITheme = "light" | "dark" | "system";

/** Shown when the URL names an org the user cannot switch into (e.g. deep link to another tenant). */
export interface OrgAccessErrorState {
  handle: string;
  orgName: string;
}

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

export interface UIState {
  // State
  /** Persisted appearance preference. */
  theme: UITheme;
  sidebarCollapsed: boolean;
  sidebarWidth: number;
  currentView: View;
  currentAppId: string | null;
  currentArtifactId: string | null;
  pinnedChatIds: string[];
  /** Last artifact id that was updated (from stream). Cleared after consumed. */
  lastArtifactUpdateId: string | null;
  /** Active section in Global Admin (sidebar + main panel). */
  adminPanelTab: AdminPanelTab;
  /** Set when org-prefixed URL targets an org the user does not belong to. */
  orgAccessError: OrgAccessErrorState | null;

  // Actions
  setSidebarCollapsed: (collapsed: boolean) => void;
  notifyArtifactUpdated: (artifactId: string) => void;
  consumeArtifactUpdate: () => string | null;
  setSidebarWidth: (width: number) => void;
  setCurrentView: (view: View) => void;
  setCurrentAppId: (id: string | null) => void;
  setCurrentArtifactId: (id: string | null) => void;
  documentSearchTerm: string | null;
  openArtifact: (artifactId: string, searchTerm?: string) => void;
  openApp: (appId: string) => void;
  startNewChat: () => void;
  togglePinChat: (id: string) => void;
  setTheme: (theme: UITheme) => void;
  setAdminPanelTab: (tab: AdminPanelTab) => void;
  clearOrgAccessError: () => void;
}

// ---------------------------------------------------------------------------
// Store implementation
// ---------------------------------------------------------------------------

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      // Initial state
      theme: "system",
      sidebarCollapsed: false,
      sidebarWidth: 256,
      currentView: "home",
      currentAppId: null,
      currentArtifactId: null,
      pinnedChatIds: [],
      lastArtifactUpdateId: null,
      documentSearchTerm: null,
      adminPanelTab: "dashboard",
      orgAccessError: null,

      // Actions
      notifyArtifactUpdated: (artifactId) => set({ lastArtifactUpdateId: artifactId }),
      consumeArtifactUpdate: () => {
        const id = get().lastArtifactUpdateId;
        if (id) set({ lastArtifactUpdateId: null });
        return id ?? null;
      },
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
      setSidebarWidth: (sidebarWidth) => set({ sidebarWidth }),
      setCurrentView: (currentView) => {
        set({
          currentView,
          ...(currentView !== "artifact-view"
            ? { currentArtifactId: null }
            : {}),
        });
        // Clear chat selection when navigating away from chat view
        if (currentView !== "chat") {
          useChatStore.setState({ currentChatId: null });
        }
      },
      setCurrentAppId: (currentAppId) => set({ currentAppId }),
      setCurrentArtifactId: (currentArtifactId) =>
        set({ currentArtifactId }),
      openArtifact: (artifactId, searchTerm) =>
        set({
          currentArtifactId: artifactId,
          documentSearchTerm: searchTerm ?? null,
          currentView: "artifact-view" as View,
        }),
      openApp: (appId) =>
        set({ currentAppId: appId, currentView: "app-view" as View }),
      startNewChat: () => {
        set({ currentView: "chat" });
        useChatStore.setState({ currentChatId: null, chatSearchTerm: null, chatSearchMatchCount: 0 });
      },
      togglePinChat: (id) => {
        const { pinnedChatIds } = get();
        const isPinned = pinnedChatIds.includes(id);
        const updated = isPinned
          ? pinnedChatIds.filter((chatId) => chatId !== id)
          : [id, ...pinnedChatIds];
        set({ pinnedChatIds: updated });
      },
      setTheme: (theme) => set({ theme }),
      setAdminPanelTab: (adminPanelTab) => set({ adminPanelTab }),
      clearOrgAccessError: () => set({ orgAccessError: null }),
    }),
    {
      name: "revtops-ui-store",
      partialize: (state) => ({
        theme: state.theme,
        sidebarCollapsed: state.sidebarCollapsed,
        sidebarWidth: state.sidebarWidth,
        pinnedChatIds: state.pinnedChatIds,
      }),
    },
  ),
);
