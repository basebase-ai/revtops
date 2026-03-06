/**
 * UI store — sidebar state, current view, pinned chats, UI preferences.
 *
 * Split from the monolithic AppState store for performance: only components
 * that read UI-related fields re-render when UI state changes.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { View } from "./types";
import { useChatStore } from "./chatStore";

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

export interface UIState {
  // State
  sidebarCollapsed: boolean;
  sidebarWidth: number;
  currentView: View;
  currentAppId: string | null;
  currentArtifactId: string | null;
  pinnedChatIds: string[];

  // Actions
  setSidebarCollapsed: (collapsed: boolean) => void;
  setSidebarWidth: (width: number) => void;
  setCurrentView: (view: View) => void;
  setCurrentAppId: (id: string | null) => void;
  setCurrentArtifactId: (id: string | null) => void;
  openArtifact: (artifactId: string) => void;
  openApp: (appId: string) => void;
  startNewChat: () => void;
  togglePinChat: (id: string) => void;
}

// ---------------------------------------------------------------------------
// Store implementation
// ---------------------------------------------------------------------------

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      // Initial state
      sidebarCollapsed: false,
      sidebarWidth: 256,
      currentView: "home",
      currentAppId: null,
      currentArtifactId: null,
      pinnedChatIds: [],

      // Actions
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
      openArtifact: (artifactId) =>
        set({
          currentArtifactId: artifactId,
          currentView: "artifact-view" as View,
        }),
      openApp: (appId) =>
        set({ currentAppId: appId, currentView: "app-view" as View }),
      startNewChat: () => {
        set({ currentView: "chat" });
        useChatStore.setState({ currentChatId: null });
      },
      togglePinChat: (id) => {
        const { pinnedChatIds } = get();
        const isPinned = pinnedChatIds.includes(id);
        const updated = isPinned
          ? pinnedChatIds.filter((chatId) => chatId !== id)
          : [id, ...pinnedChatIds];
        console.log(
          "[Store] Toggling chat pin:",
          id,
          "Pinned:",
          !isPinned,
        );
        set({ pinnedChatIds: updated });
      },
    }),
    {
      name: "revtops-ui-store",
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        sidebarWidth: state.sidebarWidth,
        pinnedChatIds: state.pinnedChatIds,
      }),
    },
  ),
);
