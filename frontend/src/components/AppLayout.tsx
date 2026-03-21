/**
 * Main application layout with collapsible sidebar.
 * 
 * Modeled after Claude's UX with:
 * - Collapsible left sidebar (icons when collapsed)
 * - Slide-out drawer on mobile
 * - New Chat button
 * - Connectors tab with badge
 * - Chats tab with recent conversations
 * - Organization & Profile sections at bottom
 * 
 * Also manages global WebSocket connection for background task updates.
 * Tasks continue running server-side even when browser tabs are closed.
 */

import { useState, useEffect, useCallback, lazy, Suspense, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Nango from '@nangohq/frontend';
import { API_BASE } from '../lib/api';
import { crossTab, subscribeCrossTab } from '../lib/crossTab';
import { useIsMobile } from '../hooks';
import { useShallow } from 'zustand/react/shallow';
import { Sidebar } from './Sidebar';
import { Home } from './Home';
import { DataSources } from './DataSources';
import { Data } from './Data';
import { Chat } from './Chat';
import { ChatsList } from './ChatsList';
import { Workflows } from './Workflows';
import { Memories } from './Memories';
import { AdminPanel } from './AdminPanel';
import { PendingChangesPage } from './PendingChangesPage';
import { OrganizationPanel } from './OrganizationPanel';

// Lazy-load app components (heavy due to Sandpack/Plotly deps)
const AppsGallery = lazy(() => import('./apps/AppsGallery').then(m => ({ default: m.AppsGallery })));
const AppFullView = lazy(() => import('./apps/AppFullView').then(m => ({ default: m.AppFullView })));
const ArtifactFullView = lazy(() => import('./ArtifactFullView').then(m => ({ default: m.ArtifactFullView })));
const DocumentsGallery = lazy(() => import('./documents/DocumentsGallery').then(m => ({ default: m.DocumentsGallery })));
import { APP_NAME, LOGO_PATH, RELEASE_STAGE } from '../lib/brand';
import { ProfilePanel } from './ProfilePanel';
import { useAppStore, useChatStore, useUIStore, useMasquerade, useIntegrations, type ActiveTask, type ToolCallData, type ChatMessage, type ContentBlock } from '../store';
import { useTeamMembers, useWebSocket } from '../hooks';
import { apiRequest } from '../lib/api';

// Re-export types from store for backwards compatibility
export type { UserProfile, OrganizationInfo, ChatSummary, View } from '../store';

// WebSocket message types
interface WsActiveTasks {
  type: 'active_tasks';
  tasks: ActiveTask[];
}

interface WsTaskStarted {
  type: 'task_started';
  task_id: string;
  conversation_id: string;
}

interface WsTaskChunk {
  type: 'task_chunk';
  task_id: string;
  conversation_id: string;
  chunk: {
    index: number;
    type: string;
    data: unknown;
    timestamp: string;
  };
}

interface WsTaskComplete {
  type: 'task_complete';
  task_id: string;
  conversation_id: string;
  status: string;
  error?: string;
}

interface WsConversationCreated {
  type: 'conversation_created';
  conversation_id: string;
  title?: string;
}

interface WsCatchup {
  type: 'catchup';
  task_id: string;
  conversation_id?: string | null;
  chunks: Array<{ index: number; type: string; data: unknown; timestamp: string }>;
  task_status: string;
}

interface WsCrmApprovalResult {
  type: 'crm_approval_result';
  operation_id: string;
  status: string;
  [key: string]: unknown;
}

interface WsToolApprovalResult {
  type: 'tool_approval_result';
  operation_id: string;
  status: string;
  [key: string]: unknown;
}

interface WsToolProgress {
  type: 'tool_progress';
  conversation_id: string;
  tool_id: string;
  tool_name: string;
  result: Record<string, unknown>;
  status: string;
}

interface WsError {
  type: 'error';
  error: string;
  code?: string;
}

interface WsNewMessage {
  type: 'new_message';
  conversation_id: string;
  message: {
    id: string;
    role: 'user' | 'assistant';
    content_blocks: Array<{ type: string; text?: string; [key: string]: unknown }>;
    created_at: string;
    user_id?: string | null;
    sender_name?: string | null;
    sender_email?: string | null;
  };
  sender_user_id: string;
}

interface WsSummaryUpdated {
  type: 'summary_updated';
  conversation_id: string;
  summary: { overall: string; recent: string; message_count_at_generation: number; updated_at: string };
}

interface WsWorkstreamsStale {
  type: 'workstreams_stale';
}

interface WsNotification {
  type: 'notification';
  notification?: { conversation_id?: string };
}

interface WsMessageSent {
  type: 'message_sent';
  conversation_id?: string;
  agent_responding?: boolean;
}

type WsMessage = WsActiveTasks | WsTaskStarted | WsTaskChunk | WsTaskComplete | WsConversationCreated | WsCatchup | WsCrmApprovalResult | WsToolApprovalResult | WsToolProgress | WsError | WsNewMessage | WsSummaryUpdated | WsWorkstreamsStale | WsNotification | WsMessageSent;

// Props
interface AppLayoutProps {
  onLogout: () => void;
  onCreateNewOrg: () => void;
}

export function AppLayout({ onLogout, onCreateNewOrg }: AppLayoutProps): JSX.Element {
  const queryClient = useQueryClient();
  
  // Get state from Zustand store using shallow comparison to prevent unnecessary re-renders
  const {
    user,
    organization,
    organizations,
    sidebarCollapsed,
    currentView,
    currentChatId,
    currentAppId,
    currentArtifactId,
    recentChats,
  } = useAppStore(
    useShallow((state) => ({
      user: state.user,
      organization: state.organization,
      organizations: state.organizations,
      sidebarCollapsed: state.sidebarCollapsed,
      currentView: state.currentView,
      currentChatId: state.currentChatId,
      currentAppId: state.currentAppId,
      currentArtifactId: state.currentArtifactId,
      recentChats: state.recentChats,
    }))
  );

  // Zustand: Get integrations for connected count badge
  const integrations = useIntegrations();
  const fetchIntegrations = useAppStore((state) => state.fetchIntegrations);
  const connectedIntegrationsCount = integrations.filter((i) => i.isActive).length;
  
  // Fetch integrations on mount and when org changes
  useEffect(() => {
    if (organization?.id && user?.id) {
      void fetchIntegrations();
    }
  }, [organization?.id, user?.id, fetchIntegrations]);

  // React Query: Get workflows for count badge
  const { data: workflows = [] } = useQuery({
    queryKey: ['workflows', organization?.id],
    queryFn: async () => {
      if (!organization?.id) return [];
      const response = await fetch(`${API_BASE}/workflows/${organization.id}`);
      if (!response.ok) return [];
      const data = await response.json() as { workflows: Array<{ is_enabled: boolean }> };
      return data.workflows ?? [];
    },
    enabled: !!organization?.id,
  });
  const workflowCount = workflows.length;

  // Billing status for credits display in sidebar
  const { data: billingStatus } = useQuery({
    queryKey: ['billing', organization?.id],
    queryFn: async () => {
      const { data } = await apiRequest<{ credits_balance: number; credits_included: number }>('/billing/status');
      return data;
    },
    enabled: !!organization?.id,
  });

  // Pending changes count (for sidebar badge)
  const [pendingChangesCount, setPendingChangesCount] = useState<number>(0);

  const fetchPendingCount = useCallback(async () => {
    if (!user?.id) return;
    try {
      const { data: res } = await apiRequest<{ pending_count: number; sessions: unknown[] }>(
        `/change-sessions/pending?user_id=${user.id}`,
      );
      if (res) {
        setPendingChangesCount(res.pending_count);
      }
    } catch {
      // swallow – badge just stays at current value
    }
  }, [user?.id]);

  // Fetch on mount, on org switch, + listen for updates
  const orgId = organization?.id;
  useEffect(() => {
    setPendingChangesCount(0);
    void fetchPendingCount();
    const handle = (): void => { void fetchPendingCount(); };
    window.addEventListener('pending-changes-updated', handle);
    return () => window.removeEventListener('pending-changes-updated', handle);
  }, [fetchPendingCount, orgId]);

  // React Query: Get team members for member count (single source of truth)
  const { data: teamData } = useTeamMembers(
    organization?.id ?? null,
    user?.id ?? null
  );

  // Get actions separately (they're stable and don't need shallow comparison)
  const setSidebarCollapsed = useAppStore((state) => state.setSidebarCollapsed);
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const setCurrentChatId = useAppStore((state) => state.setCurrentChatId);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const fetchConversations = useAppStore((state) => state.fetchConversations);
  const deleteConversation = useAppStore((state) => state.deleteConversation);
  const setUser = useAppStore((state) => state.setUser);
  const setActiveTasks = useAppStore((state) => state.setActiveTasks);
  const setConversationActiveTask = useAppStore((state) => state.setConversationActiveTask);
  const exitMasquerade = useAppStore((state) => state.exitMasquerade);
  
  // Masquerade state
  const masquerade = useMasquerade();
  const addConversation = useAppStore((state) => state.addConversation);
  const addConversationMessage = useAppStore((state) => state.addConversationMessage);
  const appendToConversationStreaming = useAppStore((state) => state.appendToConversationStreaming);
  const startConversationStreaming = useAppStore((state) => state.startConversationStreaming);
  const markConversationMessageComplete = useAppStore((state) => state.markConversationMessageComplete);
  const advanceConversationChunkIndex = useAppStore((state) => state.advanceConversationChunkIndex);
  const setConversationThinking = useAppStore((state) => state.setConversationThinking);
  const updateConversationToolMessage = useAppStore((state) => state.updateConversationToolMessage);
  const addConversationArtifactBlock = useAppStore((state) => state.addConversationArtifactBlock);
  const addConversationAppBlock = useAppStore((state) => state.addConversationAppBlock);
  const setConversationContextTokens = useAppStore((state) => state.setConversationContextTokens);

  // Ref for sendJson so active_tasks handler can request catchup (handler runs before useWebSocket)
  const sendJsonRef = useRef<((data: Record<string, unknown>) => void) | null>(null);

  // Mobile responsive state
  const isMobile = useIsMobile();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  // Release stage banner dismissal (stored in localStorage)
  const [showReleaseBanner, setShowReleaseBanner] = useState(() => {
    if (!RELEASE_STAGE.stage) return false;
    const dismissed = localStorage.getItem('release-banner-dismissed');
    return dismissed !== 'true';
  });

  const dismissReleaseBanner = useCallback(() => {
    localStorage.setItem('release-banner-dismissed', 'true');
    setShowReleaseBanner(false);
  }, []);

  // Sidebar resize drag
  const sidebarWidth = useAppStore((state) => state.sidebarWidth);
  const setSidebarWidth = useAppStore((state) => state.setSidebarWidth);
  const isDraggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const handleDividerMouseDown = useCallback((e: React.MouseEvent): void => {
    e.preventDefault();
    isDraggingRef.current = true;
    startXRef.current = e.clientX;
    startWidthRef.current = sidebarWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (ev: MouseEvent): void => {
      if (!isDraggingRef.current) return;
      const newWidth = Math.min(400, Math.max(200, startWidthRef.current + ev.clientX - startXRef.current));
      setSidebarWidth(newWidth);
    };
    const onMouseUp = (): void => {
      isDraggingRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, [sidebarWidth, setSidebarWidth]);
  
  // Close mobile sidebar when view changes
  useEffect(() => {
    if (isMobile) {
      setMobileSidebarOpen(false);
    }
  }, [currentView, currentChatId, isMobile]);

  // Track if initial URL sync is done (prevent URL update effect from running first)
  const [urlInitialized, setUrlInitialized] = useState(false);
  // Prevent URL effect from overwriting URL while we're syncing state from URL (e.g. /:handle/artifacts/:id)
  const isSyncingFromUrlRef = useRef(false);

  // Parse URL and update state
  const setCurrentAppId = useAppStore((state) => state.setCurrentAppId);
  const openArtifact = useAppStore((state) => state.openArtifact);
  const fetchUserOrganizations = useAppStore((state) => state.fetchUserOrganizations);
  const switchActiveOrganization = useAppStore((state) => state.switchActiveOrganization);

  const syncStateFromUrl = useCallback(async (): Promise<void> => {
    isSyncingFromUrlRef.current = true;
    try {
      const path = window.location.pathname;

      const orgPrefixMatch = path.match(/^\/([a-z0-9-]+)(?:\/(.*))?$/);
    const orgHandleFromPath: string | null =
      orgPrefixMatch && orgPrefixMatch[1] && !/^(auth|admin|embed|chat|apps|documents|artifact|artifacts|sources|data|workflows|memory|changes)$/i.test(orgPrefixMatch[1])
        ? orgPrefixMatch[1]
        : null;

    if (orgHandleFromPath) {
      let orgs = useAppStore.getState().organizations;
      if (orgs.length === 0) {
        await fetchUserOrganizations();
        orgs = useAppStore.getState().organizations;
      }
      let targetOrg = orgs.find((o) => (o.handle ?? "").toLowerCase() === orgHandleFromPath.toLowerCase());
      if (!targetOrg) {
        const { data: orgByHandle } = await apiRequest<{ id: string; name: string; logo_url: string | null; handle: string | null }>(
          `/auth/organizations/by-handle/${encodeURIComponent(orgHandleFromPath)}`,
          { method: "GET" },
        );
        if (orgByHandle) {
          targetOrg = { id: orgByHandle.id, name: orgByHandle.name, logoUrl: orgByHandle.logo_url, handle: orgByHandle.handle, role: "member", isActive: false };
          await fetchUserOrganizations();
        }
      }
      if (!targetOrg) {
        return;
      }
      const currentOrg = useAppStore.getState().organization;
      if (!currentOrg || currentOrg.id !== targetOrg.id) {
        await switchActiveOrganization(targetOrg.id);
      }

      const subPath: string = orgPrefixMatch?.[2] ?? "";
      if (subPath === "" || subPath === "chat") {
        setCurrentChatId(null);
        setCurrentView("chat");
        return;
      }
      const chatIdMatch = subPath.match(/^chat\/([a-f0-9-]+)$/i);
      if (chatIdMatch && chatIdMatch[1]) {
        setCurrentChatId(chatIdMatch[1]);
        setCurrentView("chat");
        return;
      }
      const artifactMatch = subPath.match(/^artifacts?\/([a-f0-9-]+)$/i);
      if (artifactMatch && artifactMatch[1]) {
        openArtifact(artifactMatch[1]);
        return;
      }
      const appMatch = subPath.match(/^apps\/([a-f0-9-]+)$/i);
      if (appMatch && appMatch[1]) {
        setCurrentAppId(appMatch[1]);
        setCurrentView("app-view");
        return;
      }
      const viewMap: Record<string, typeof currentView> = {
        chats: "chats",
        sources: "data-sources",
        data: "data",
        workflows: "workflows",
        memory: "memory",
        apps: "apps",
        documents: "documents",
        admin: "admin",
        changes: "pending-changes",
      };
      const view = viewMap[subPath];
      if (view) {
        setCurrentChatId(null);
        setCurrentView(view);
      }
      return;
    }

    // Legacy paths (no org handle)
    const chatMatch = path.match(/^\/chat\/([a-f0-9-]+)$/i);
    if (chatMatch && chatMatch[1]) {
      setCurrentChatId(chatMatch[1]);
      setCurrentView("chat");
      return;
    }
    const appMatch = path.match(/^\/apps\/([a-f0-9-]+)$/i);
    if (appMatch && appMatch[1]) {
      setCurrentAppId(appMatch[1]);
      setCurrentView("app-view");
      return;
    }
    const artifactMatch = path.match(/^\/artifacts?\/([a-f0-9-]+)$/i);
    if (artifactMatch && artifactMatch[1]) {
      openArtifact(artifactMatch[1]);
      return;
    }

    const viewPaths: Record<string, typeof currentView> = {
      "/": "home",
      "/chat": "chat",
      "/chats": "chats",
      "/sources": "data-sources",
      "/data": "data",
      "/workflows": "workflows",
      "/memory": "memory",
      "/apps": "apps",
      "/documents": "documents",
      "/admin": "admin",
      "/changes": "pending-changes",
    };
    const matchedView = viewPaths[path];
    if (matchedView) {
      if (matchedView !== "chat") setCurrentChatId(null);
      setCurrentView(matchedView);
    }
    } finally {
      isSyncingFromUrlRef.current = false;
    }
  }, [
    setCurrentChatId,
    setCurrentAppId,
    setCurrentView,
    openArtifact,
    fetchUserOrganizations,
    switchActiveOrganization,
  ]);

  // Sync URL with app state - restore state on page load (runs FIRST)
  useEffect(() => {
    let cancelled = false;
    void syncStateFromUrl().then(() => {
      if (!cancelled) setUrlInitialized(true);
    });
    return () => { cancelled = true; };
  }, [syncStateFromUrl]);

  // Handle browser back/forward buttons
  useEffect(() => {
    const handlePopState = (): void => {
      void syncStateFromUrl();
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [syncStateFromUrl]);

  // Update URL when app state changes (only after initial sync)
  // Always use org handle when available (from org or organizations list) so copied URLs are shareable
  const orgHandle: string | null =
    organization?.handle ??
    (organization?.id ? organizations.find((o) => o.id === organization.id)?.handle ?? null : null) ??
    null;
  useEffect(() => {
    if (!urlInitialized || isSyncingFromUrlRef.current) return;

    const prefix = orgHandle ? `/${orgHandle}` : "";
    let newPath: string;

    if (currentChatId) {
      newPath = `${prefix}/chat/${currentChatId}`;
    } else if (currentView === "app-view" && currentAppId) {
      newPath = `${prefix}/apps/${currentAppId}`;
    } else if (currentView === "artifact-view" && currentArtifactId) {
      newPath = `${prefix}/artifacts/${currentArtifactId}`;
    } else {
      const viewPaths: Record<typeof currentView, string> = {
        home: "/",
        chat: "/chat",
        chats: "/chats",
        "data-sources": "/sources",
        data: "/data",
        workflows: "/workflows",
        apps: "/apps",
        "app-view": "/apps",
        documents: "/documents",
        "artifact-view": "/chat",
        admin: "/admin",
        memory: "/memory",
        "pending-changes": "/changes",
      };
      const base = viewPaths[currentView] || "/";
      newPath = prefix ? `${prefix}${base === "/" ? "" : base}` : base;
    }

    if (window.location.pathname !== newPath) {
      window.history.pushState({}, "", newPath);
    }
  }, [currentChatId, currentAppId, currentArtifactId, currentView, urlInitialized, orgHandle, organization?.id, organization?.handle, organizations]);
  
  // Panels
  const [showOrgPanel, setShowOrgPanel] = useState(false);
  const [showProfilePanel, setShowProfilePanel] = useState(false);
  const [orgPanelTab, setOrgPanelTab] = useState<'team' | 'billing' | 'settings'>('team');

  // CRM approval results (shared across chats) - use state to trigger re-renders
  const [crmApprovalResults, setCrmApprovalResults] = useState<Map<string, unknown>>(() => new Map());

  const shouldBroadcastWebSocket = useCallback((type: string | undefined): boolean => {
    if (!type) {
      return false;
    }
    return [
      'task_started',
      'task_chunk',
      'task_complete',
      'conversation_created',
      'tool_progress',
      'crm_approval_result',
      'tool_approval_result',
      'new_message',
      'summary_updated',
      'workstreams_stale',
    ].includes(type);
  }, []);

  // Handle WebSocket messages
  const handleWebSocketMessage = useCallback((message: string, source: 'ws' | 'broadcast' = 'ws') => {
    try {
      const parsed = JSON.parse(message) as WsMessage;
      if (source === 'ws' && shouldBroadcastWebSocket(parsed.type)) {
        if (crossTab.isAvailable) {
          console.log('[AppLayout] Broadcasting WebSocket event to other tabs:', parsed.type);
          crossTab.postMessage({
            kind: 'ws-event',
            payload: { message },
          });
        }
      }
      
      switch (parsed.type) {
        case 'active_tasks': {
          console.log('[AppLayout] Received active tasks:', parsed.tasks.length);
          // Reconcile: clear any local active tasks the server no longer reports as running
          // (task completed while client was disconnected)
          const localActive = useAppStore.getState().activeTasksByConversation;
          const serverConvIds = new Set(
            (parsed.tasks as ActiveTask[]).map((t: ActiveTask) => t.conversation_id),
          );
          for (const convId of Object.keys(localActive)) {
            if (!serverConvIds.has(convId)) {
              setConversationActiveTask(convId, null);
              setConversationThinking(convId, false);
              markConversationMessageComplete(convId);
            }
          }
          setActiveTasks(parsed.tasks);
          // Request catchup for still-running tasks (missed chunks while disconnected)
          const tasks = parsed.tasks as ActiveTask[];
          for (const task of tasks) {
            const convState = useAppStore.getState().conversations[task.conversation_id];
            const sinceIndex = (convState?.lastChunkIndex ?? -1) + 1;
            sendJsonRef.current?.({ type: 'subscribe', task_id: task.id, since_index: sinceIndex });
          }
          break;
        }
        
        case 'task_started': {
          console.log('[AppLayout] Task started:', parsed.task_id, 'for conversation:', parsed.conversation_id);
          setConversationActiveTask(parsed.conversation_id, parsed.task_id);
          setConversationThinking(parsed.conversation_id, true);
          break;
        }
        
        case 'task_chunk': {
          const { conversation_id, chunk } = parsed;
          const chunkData = chunk.data;
          const chunkIndex = chunk.index;
          
          // Route chunk to appropriate conversation
          if (chunk.type === 'text_delta' && typeof chunkData === 'string') {
            // Text chunk - append to streaming message with index for ordering
            const state = useAppStore.getState();
            const convState = state.conversations[conversation_id];
            if (convState?.streamingMessageId) {
              appendToConversationStreaming(conversation_id, chunkData, chunkIndex);
            } else {
              // Start new streaming message with chunk index
              const msgId = `assistant-${Date.now()}`;
              startConversationStreaming(conversation_id, msgId, chunkData, chunkIndex);
            }
          } else if (typeof chunkData === 'object' && chunkData !== null) {
            const data = chunkData as Record<string, unknown>;

            if (data.type === 'thinking_start') {
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              const thinkingBlock = { type: 'thinking' as const, text: '', isStreaming: true };
              if (convState?.streamingMessageId) {
                const updated = convState.messages.map((msg) => {
                  if (msg.id !== convState.streamingMessageId) return msg;
                  return { ...msg, contentBlocks: [...msg.contentBlocks, thinkingBlock] };
                });
                useAppStore.setState({
                  conversations: {
                    ...state.conversations,
                    [conversation_id]: { ...convState, messages: updated },
                  },
                });
              } else {
                const msgId = `assistant-${Date.now()}`;
                startConversationStreaming(conversation_id, msgId, '', chunkIndex);
                const state2 = useAppStore.getState();
                const convState2 = state2.conversations[conversation_id];
                if (convState2?.streamingMessageId) {
                  const updated = convState2.messages.map((msg) => {
                    if (msg.id !== convState2.streamingMessageId) return msg;
                    return { ...msg, contentBlocks: [thinkingBlock] };
                  });
                  useAppStore.setState({
                    conversations: {
                      ...state2.conversations,
                      [conversation_id]: { ...convState2, messages: updated },
                    },
                  });
                }
              }
            } else if (data.type === 'thinking_delta') {
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              const streamingId = convState?.streamingMessageId;
              if (convState && streamingId) {
                const updated = convState.messages.map((msg) => {
                  if (msg.id !== streamingId) return msg;
                  const blocks = [...msg.contentBlocks];
                  const lastBlock = blocks[blocks.length - 1];
                  if (lastBlock && lastBlock.type === 'thinking') {
                    blocks[blocks.length - 1] = {
                      ...lastBlock,
                      text: lastBlock.text + (data.text as string),
                    };
                  }
                  return { ...msg, contentBlocks: blocks };
                });
                useAppStore.setState({
                  conversations: {
                    ...state.conversations,
                    [conversation_id]: { ...convState, messages: updated },
                  },
                });
              }
            } else if (data.type === 'thinking_stop') {
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              const streamingId = convState?.streamingMessageId;
              if (convState && streamingId) {
                const updated = convState.messages.map((msg) => {
                  if (msg.id !== streamingId) return msg;
                  const blocks = msg.contentBlocks.map((block) =>
                    block.type === 'thinking' && block.isStreaming
                      ? { ...block, isStreaming: false }
                      : block,
                  );
                  return { ...msg, contentBlocks: blocks };
                });
                useAppStore.setState({
                  conversations: {
                    ...state.conversations,
                    [conversation_id]: { ...convState, messages: updated },
                  },
                });
              }
            } else if (data.type === 'attachment_meta') {
              const attachments = data.attachments as Array<{
                filename: string;
                mimeType: string;
                size: number;
                attachment_id: string;
              }>;
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              if (convState && attachments?.length > 0) {
                const idByFilename = new Map<string, string>();
                for (const att of attachments) {
                  idByFilename.set(att.filename, att.attachment_id);
                }
                const updated = convState.messages.map((msg) => {
                  if (msg.role !== 'user') return msg;
                  const hasUnlinkedAttachment: boolean = msg.contentBlocks.some(
                    (b) => b.type === 'attachment' && !b.attachmentId && !(b as unknown as Record<string, unknown>)['attachment_id'],
                  );
                  if (!hasUnlinkedAttachment) return msg;
                  return {
                    ...msg,
                    contentBlocks: msg.contentBlocks.map((block) => {
                      if (block.type !== 'attachment') return block;
                      const aid: string | undefined = idByFilename.get(block.filename);
                      if (!aid) return block;
                      return { ...block, attachmentId: aid };
                    }),
                  };
                });
                useAppStore.setState({
                  conversations: {
                    ...state.conversations,
                    [conversation_id]: { ...convState, messages: updated },
                  },
                });
              }
            } else if (data.type === 'tool_call_start') {
              const toolBlock = {
                type: 'tool_use' as const,
                id: data.tool_id as string,
                name: data.tool_name as string,
                input: {} as Record<string, unknown>,
                status: 'streaming' as const,
              };
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              if (convState?.streamingMessageId) {
                const updated = convState.messages.map((msg) => {
                  if (msg.id !== convState.streamingMessageId) return msg;
                  return { ...msg, contentBlocks: [...msg.contentBlocks, toolBlock] };
                });
                useAppStore.setState({
                  conversations: {
                    ...state.conversations,
                    [conversation_id]: { ...convState, messages: updated },
                  },
                });
              } else {
                addConversationMessage(conversation_id, {
                  id: `assistant-${Date.now()}`,
                  role: 'assistant',
                  contentBlocks: [toolBlock],
                  timestamp: new Date(),
                });
              }
            } else if (data.type === 'tool_input_progress') {
              const toolId = data.tool_id as string;
              const chars = data.chars as number;
              const toolName = data.tool_name as string;
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              if (convState) {
                const updated = convState.messages.map((msg) => {
                  const hasBlock = msg.contentBlocks.some(
                    (b) => b.type === 'tool_use' && b.id === toolId,
                  );
                  if (!hasBlock) return msg;
                  return {
                    ...msg,
                    contentBlocks: msg.contentBlocks.map((block) => {
                      if (block.type === 'tool_use' && block.id === toolId) {
                        return { ...block, input: { ...block.input, _streaming_chars: chars, tool_name: toolName } };
                      }
                      return block;
                    }),
                  };
                });
                useAppStore.setState({
                  conversations: {
                    ...state.conversations,
                    [conversation_id]: { ...convState, messages: updated },
                  },
                });
              }
            } else if (data.type === 'tool_call') {
              // Tool call fully parsed — find and update the streaming placeholder
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              const toolId = data.tool_id as string;

              if (convState) {
                // Search ALL messages for a matching tool_use block to update
                let found = false;
                const updated = convState.messages.map((msg) => {
                  const hasBlock = msg.contentBlocks.some(
                    (b) => b.type === 'tool_use' && b.id === toolId,
                  );
                  if (!hasBlock) return msg;
                  found = true;
                  return {
                    ...msg,
                    contentBlocks: msg.contentBlocks.map((block) => {
                      if (block.type === 'tool_use' && block.id === toolId) {
                        return {
                          ...block,
                          input: data.tool_input as Record<string, unknown>,
                          status: 'running' as const,
                          ...(typeof data.status_text === 'string' && data.status_text
                            ? { statusText: data.status_text }
                            : {}),
                        };
                      }
                      return block;
                    }),
                  };
                });

                if (found) {
                  useAppStore.setState({
                    conversations: {
                      ...state.conversations,
                      [conversation_id]: { ...convState, messages: updated },
                    },
                  });
                } else {
                  // No placeholder found — add block to streaming message or create new
                  const targetMsgId = convState.streamingMessageId;
                  if (targetMsgId) {
                    const updated2 = convState.messages.map((msg) => {
                      if (msg.id !== targetMsgId) return msg;
                      return {
                        ...msg,
                        contentBlocks: [
                          ...msg.contentBlocks,
                          {
                            type: 'tool_use' as const,
                            id: toolId,
                            name: data.tool_name as string,
                            input: data.tool_input as Record<string, unknown>,
                            status: 'running' as const,
                            ...(typeof data.status_text === 'string' && data.status_text
                              ? { statusText: data.status_text }
                              : {}),
                          },
                        ],
                      };
                    });
                    useAppStore.setState({
                      conversations: {
                        ...state.conversations,
                        [conversation_id]: { ...convState, messages: updated2 },
                      },
                    });
                  } else {
                    addConversationMessage(conversation_id, {
                      id: `assistant-${Date.now()}`,
                      role: 'assistant',
                      contentBlocks: [{
                        type: 'tool_use',
                        id: toolId,
                        name: data.tool_name as string,
                        input: data.tool_input as Record<string, unknown>,
                        status: 'running',
                        ...(typeof data.status_text === 'string' && data.status_text
                          ? { statusText: data.status_text }
                          : {}),
                      }],
                      timestamp: new Date(),
                    });
                  }
                }
              }
            } else if (data.type === 'tool_result') {
              // Tool result received (include input so block has params if it was empty, e.g. modal)
              const updates: Partial<ToolCallData> = {
                result: data.result as Record<string, unknown>,
                status: 'complete',
              };
              if (data.tool_input != null && typeof data.tool_input === 'object') {
                updates.input = data.tool_input as Record<string, unknown>;
              }
              if (typeof data.status_text === 'string' && data.status_text) {
                updates.statusText = data.status_text;
              }
              updateConversationToolMessage(conversation_id, data.tool_id as string, updates);
              
              // Check if tool failed due to insufficient credits
              const result = data.result as Record<string, unknown> | undefined;
              if (result?.error && typeof result.error === 'string' && 
                  result.error.toLowerCase().includes('insufficient credits')) {
                setShowOrgPanel(true);
                setOrgPanelTab('billing');
                // Refresh billing status to show updated credits
                queryClient.invalidateQueries({ queryKey: ['billing'] });
              }
              
              // If workflows table was modified, notify the Workflows component to refresh
              if (result?.table === 'workflows' && result?.success) {
                window.dispatchEvent(new Event('workflows-updated'));
              }
              
              // If CRM write tool completed, notify PendingChangesBar to refresh
              const toolName = data.tool_name as string | undefined;
              if (toolName === 'write_to_system_of_record' || toolName === 'run_sql_write') {
                window.dispatchEvent(new Event('pending-changes-updated'));
              }
            } else if (data.type === 'text_block_complete') {
              // Text block complete, tools may be incoming.
              // Mark isStreaming=false but keep streamingMessageId so tool blocks
              // can still be appended to the same message.
              const tbcState = useAppStore.getState();
              const tbcConv = tbcState.conversations[conversation_id];
              if (tbcConv) {
                const updatedMsgs = tbcConv.messages.map((msg) =>
                  msg.isStreaming ? { ...msg, isStreaming: false } : msg,
                );
                useAppStore.setState({
                  conversations: {
                    ...tbcState.conversations,
                    [conversation_id]: { ...tbcConv, messages: updatedMsgs },
                  },
                });
              }
            } else if (data.type === 'crm_approval_result' || data.type === 'tool_approval_result') {
              // Store tool approval result - create new Map to trigger re-render
              setCrmApprovalResults((prev) => {
                const next = new Map(prev);
                next.set(data.operation_id as string, data);
                return next;
              });
            } else if (data.type === 'artifact') {
              // Artifact created or updated - add artifact block to the message
              const artifact = data.artifact as {
                id: string;
                title: string;
                filename: string;
                contentType: "text" | "markdown" | "pdf" | "chart";
                mimeType: string;
                updated?: boolean;
              } | undefined;
              if (artifact) {
                addConversationArtifactBlock(conversation_id, artifact);
                if (artifact.updated) {
                  useUIStore.getState().notifyArtifactUpdated(artifact.id);
                }
              }
            } else if (data.type === 'app') {
              // App created - add app block to the message
              const app = data.app as {
                id: string;
                title: string;
                description: string | null;
                frontendCode: string;
              } | undefined;
              if (app) {
                addConversationAppBlock(conversation_id, app);
              }
            } else if (data.type === 'connector_connect') {
              // Connector connection initiated - open OAuth popup or connect builtin
              const connectData = data as {
                type: string;
                action: 'connect_oauth' | 'connect_builtin';
                provider: string;
                scope: 'organization' | 'user';
                session_token?: string;
                connection_id?: string;
              };
              
              // Get current org/user from store (not closure) to ensure fresh values
              const currentState = useAppStore.getState();
              const orgId = currentState.organization?.id;
              const currentUserId = currentState.user?.id;
              
              if (connectData.action === 'connect_oauth' && connectData.session_token) {
                // Open Nango OAuth popup
                const nango = new Nango();
                nango.openConnectUI({
                  sessionToken: connectData.session_token,
                  onEvent: async (event) => {
                    const eventType = event.type as string;
                    if (eventType === 'connect' || eventType === 'connection-created' || eventType === 'success') {
                      // Confirm integration with backend
                      const eventData = event as { connectionId?: string; connection_id?: string; payload?: { connectionId?: string } };
                      const nangoConnectionId = eventData.connectionId || eventData.connection_id || eventData.payload?.connectionId || connectData.connection_id;
                      
                      try {
                        await fetch(`${API_BASE}/auth/integrations/confirm`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({
                            provider: connectData.provider,
                            connection_id: nangoConnectionId,
                            organization_id: orgId,
                            user_id: connectData.scope === 'user' ? currentUserId : undefined,
                          }),
                        });
                        // Refresh integrations list
                        queryClient.invalidateQueries({ queryKey: ['integrations'] });
                      } catch (err) {
                        console.error('Failed to confirm integration:', err);
                      }
                    }
                  },
                });
              } else if (connectData.action === 'connect_builtin') {
                // Connect built-in connector directly
                void apiRequest<{ status: string; provider: string }>('/auth/integrations/connect-builtin', {
                  method: 'POST',
                  body: JSON.stringify({
                    provider: connectData.provider,
                    organization_id: orgId,
                    user_id: currentUserId,
                  }),
                })
                  .then(({ error }) => {
                    if (error) {
                      throw new Error(error);
                    }
                    return queryClient.invalidateQueries({ queryKey: ['integrations'] });
                  })
                  .catch((err) => console.error('Failed to connect builtin:', err));
              }
            } else if (data.type === 'context_usage') {
              const usage = data as { input_tokens: number; output_tokens: number };
              setConversationContextTokens(conversation_id, usage.input_tokens);
            }

            // Advance chunk index so subsequent text_delta chunks aren't
            // incorrectly buffered as "out of order" (the backend uses a
            // single counter for ALL chunk types).
            advanceConversationChunkIndex(conversation_id, chunkIndex);
          }
          break;
        }

        case 'task_complete': {
          const taskComplete = parsed as WsTaskComplete;
          console.log('[AppLayout] Task complete:', taskComplete.task_id, 'status:', taskComplete.status);
          setConversationActiveTask(taskComplete.conversation_id, null);
          setConversationThinking(taskComplete.conversation_id, false);
          markConversationMessageComplete(taskComplete.conversation_id);
          
          // If task failed, add an error block to the conversation
          if (taskComplete.status === 'failed' && taskComplete.error) {
            console.error('[AppLayout] Task failed with error:', taskComplete.error);
            // Append error block to the last assistant message or create a new one
            const state = useAppStore.getState();
            const convState = state.conversations[taskComplete.conversation_id];
            if (convState) {
              const messages = [...convState.messages];
              const lastMsg = messages[messages.length - 1];
              
              // Create error block with structured data
              const errorBlock = {
                type: 'error' as const,
                message: taskComplete.error,
              };
              
              if (lastMsg && lastMsg.role === 'assistant') {
                // Append error block to existing assistant message
                messages[messages.length - 1] = {
                  ...lastMsg,
                  contentBlocks: [
                    ...lastMsg.contentBlocks,
                    errorBlock,
                  ],
                };
              } else {
                // Create new error message
                messages.push({
                  id: `error-${Date.now()}`,
                  role: 'assistant',
                  contentBlocks: [errorBlock],
                  timestamp: new Date(),
                });
              }
              useAppStore.setState({
                conversations: {
                  ...state.conversations,
                  [taskComplete.conversation_id]: { ...convState, messages },
                },
              });
            }
          }
          // Refresh billing status since credits may have been consumed
          queryClient.invalidateQueries({ queryKey: ['billing'] });
          break;
        }
        
        case 'conversation_created': {
          const title = parsed.title || 'New Chat';
          console.log('[AppLayout] Conversation created:', parsed.conversation_id, 'title:', title);
          addConversation(parsed.conversation_id, title);
          if (source === 'ws') {
            // Only update currentChatId when on new chat (null) - we're waiting for the backend
            // to assign an ID. Don't overwrite when user has selected an existing conversation.
            const currentId = useAppStore.getState().currentChatId;
            if (currentId === null) {
              setCurrentChatId(parsed.conversation_id);
            }
          }
          break;
        }
        
        case 'catchup': {
          const catchup = parsed as WsCatchup;
          console.log('[AppLayout] Catchup for task:', catchup.task_id, 'chunks:', catchup.chunks.length);
          const conversationId: string | null =
            catchup.conversation_id ??
            Object.entries(useAppStore.getState().activeTasksByConversation).find(
              ([, tid]) => tid === catchup.task_id,
            )?.[0] ??
            null;
          if (conversationId) {
            for (const chunk of catchup.chunks) {
              const chunkData = chunk.data;
              const chunkIndex = chunk.index;
              if (chunk.type === 'text_delta' && typeof chunkData === 'string') {
                const state = useAppStore.getState();
                const convState = state.conversations[conversationId];
                if (convState?.streamingMessageId) {
                  appendToConversationStreaming(conversationId, chunkData, chunkIndex);
                } else {
                  const msgId = `assistant-${Date.now()}`;
                  startConversationStreaming(conversationId, msgId, chunkData, chunkIndex);
                }
              } else if (typeof chunkData === 'object' && chunkData !== null) {
                const data = chunkData as Record<string, unknown>;
                if (data.type === 'thinking_start') {
                  const state = useAppStore.getState();
                  const convState = state.conversations[conversationId];
                  const thinkingBlock = { type: 'thinking' as const, text: '', isStreaming: true };
                  if (convState?.streamingMessageId) {
                    const updated = convState.messages.map((msg) =>
                      msg.id === convState.streamingMessageId
                        ? { ...msg, contentBlocks: [...msg.contentBlocks, thinkingBlock] }
                        : msg,
                    );
                    useAppStore.setState({
                      conversations: {
                        ...state.conversations,
                        [conversationId]: { ...convState, messages: updated },
                      },
                    });
                  } else {
                    const msgId = `assistant-${Date.now()}`;
                    startConversationStreaming(conversationId, msgId, '', chunkIndex);
                    const state2 = useAppStore.getState();
                    const convState2 = state2.conversations[conversationId];
                    if (convState2?.streamingMessageId) {
                      const updated = convState2.messages.map((msg) =>
                        msg.id === convState2.streamingMessageId
                          ? { ...msg, contentBlocks: [thinkingBlock] }
                          : msg,
                      );
                      useAppStore.setState({
                        conversations: {
                          ...state2.conversations,
                          [conversationId]: { ...convState2, messages: updated },
                        },
                      });
                    }
                  }
                } else if (data.type === 'thinking_delta') {
                  const state = useAppStore.getState();
                  const convState = state.conversations[conversationId];
                  const streamingId = convState?.streamingMessageId;
                  if (convState && streamingId) {
                    const updated = convState.messages.map((msg) => {
                      if (msg.id !== streamingId) return msg;
                      const blocks = [...msg.contentBlocks];
                      const lastBlock = blocks[blocks.length - 1];
                      if (lastBlock && lastBlock.type === 'thinking') {
                        blocks[blocks.length - 1] = {
                          ...lastBlock,
                          text: lastBlock.text + (data.text as string),
                        };
                      }
                      return { ...msg, contentBlocks: blocks };
                    });
                    useAppStore.setState({
                      conversations: {
                        ...state.conversations,
                        [conversationId]: { ...convState, messages: updated },
                      },
                    });
                  }
                } else if (data.type === 'thinking_stop') {
                  const state = useAppStore.getState();
                  const convState = state.conversations[conversationId];
                  const streamingId = convState?.streamingMessageId;
                  if (convState && streamingId) {
                    const updated = convState.messages.map((msg) => {
                      if (msg.id !== streamingId) return msg;
                      const blocks = msg.contentBlocks.map((block) =>
                        block.type === 'thinking' && block.isStreaming
                          ? { ...block, isStreaming: false }
                          : block,
                      );
                      return { ...msg, contentBlocks: blocks };
                    });
                    useAppStore.setState({
                      conversations: {
                        ...state.conversations,
                        [conversationId]: { ...convState, messages: updated },
                      },
                    });
                  }
                } else if (data.type === 'attachment_meta') {
                  const attachments = data.attachments as Array<{
                    filename: string;
                    mimeType: string;
                    size: number;
                    attachment_id: string;
                  }>;
                  const state = useAppStore.getState();
                  const convState = state.conversations[conversationId];
                  if (convState && attachments?.length > 0) {
                    const idByFilename = new Map<string, string>();
                    for (const att of attachments) {
                      idByFilename.set(att.filename, att.attachment_id);
                    }
                    const updated = convState.messages.map((msg) => {
                      if (msg.role !== 'user') return msg;
                      const hasUnlinkedAttachment: boolean = msg.contentBlocks.some(
                        (b) => b.type === 'attachment' && !b.attachmentId && !(b as unknown as Record<string, unknown>)['attachment_id'],
                      );
                      if (!hasUnlinkedAttachment) return msg;
                      return {
                        ...msg,
                        contentBlocks: msg.contentBlocks.map((block) => {
                          if (block.type !== 'attachment') return block;
                          const aid: string | undefined = idByFilename.get(block.filename);
                          if (!aid) return block;
                          return { ...block, attachmentId: aid };
                        }),
                      };
                    });
                    useAppStore.setState({
                      conversations: {
                        ...state.conversations,
                        [conversationId]: { ...convState, messages: updated },
                      },
                    });
                  }
                } else if (data.type === 'tool_call_start') {
                  const toolBlock = {
                    type: 'tool_use' as const,
                    id: data.tool_id as string,
                    name: data.tool_name as string,
                    input: {} as Record<string, unknown>,
                    status: 'streaming' as const,
                  };
                  const state = useAppStore.getState();
                  const convState = state.conversations[conversationId];
                  if (convState?.streamingMessageId) {
                    const updated = convState.messages.map((msg) =>
                      msg.id === convState.streamingMessageId
                        ? { ...msg, contentBlocks: [...msg.contentBlocks, toolBlock] }
                        : msg,
                    );
                    useAppStore.setState({
                      conversations: {
                        ...state.conversations,
                        [conversationId]: { ...convState, messages: updated },
                      },
                    });
                  } else {
                    addConversationMessage(conversationId, {
                      id: `assistant-${Date.now()}`,
                      role: 'assistant',
                      contentBlocks: [toolBlock],
                      timestamp: new Date(),
                    });
                  }
                } else if (data.type === 'tool_input_progress') {
                  updateConversationToolMessage(conversationId, data.tool_id as string, {
                    input: { _streaming_chars: data.chars as number, tool_name: data.tool_name as string },
                  });
                } else if (data.type === 'tool_call') {
                  updateConversationToolMessage(conversationId, data.tool_id as string, {
                    toolName: data.tool_name as string,
                    input: data.tool_input as Record<string, unknown>,
                    status: 'running',
                    ...(typeof data.status_text === 'string' && data.status_text
                      ? { statusText: data.status_text }
                      : {}),
                  });
                } else if (data.type === 'tool_result') {
                  const updates: Partial<ToolCallData> = {
                    result: data.result as Record<string, unknown>,
                    status: 'complete',
                  };
                  if (data.tool_input != null && typeof data.tool_input === 'object') {
                    updates.input = data.tool_input as Record<string, unknown>;
                  }
                  if (typeof data.status_text === 'string' && data.status_text) {
                    updates.statusText = data.status_text;
                  }
                  updateConversationToolMessage(conversationId, data.tool_id as string, updates);
                } else if (data.type === 'artifact') {
                  const artifact = data.artifact as {
                    id: string;
                    title: string;
                    filename: string;
                    contentType: 'text' | 'markdown' | 'pdf' | 'chart';
                    mimeType: string;
                    updated?: boolean;
                  } | undefined;
                  if (artifact) {
                    addConversationArtifactBlock(conversationId, artifact);
                    if (artifact.updated) {
                      useUIStore.getState().notifyArtifactUpdated(artifact.id);
                    }
                  }
                } else if (data.type === 'app') {
                  const app = data.app as {
                    id: string;
                    title: string;
                    description: string | null;
                    frontendCode: string;
                  } | undefined;
                  if (app) addConversationAppBlock(conversationId, app);
                }
              }
            }
            if (catchup.task_status !== 'running') {
              setConversationActiveTask(conversationId, null);
              setConversationThinking(conversationId, false);
              markConversationMessageComplete(conversationId);
            }
          }
          break;
        }

        case 'error': {
          const err = parsed as WsError;
          if (err.code === 'insufficient_credits') {
            // Add a message to the current conversation explaining the issue
            const chatId = useAppStore.getState().currentChatId;
            if (chatId) {
              const errorMessage = {
                id: `credits-error-${Date.now()}`,
                role: 'assistant' as const,
                contentBlocks: [{
                  type: 'text' as const,
                  text: "I wasn't able to complete your request because your team has run out of credits for this billing period. You can view your usage and upgrade your plan in the **Billing** tab under Team Settings.",
                }],
                timestamp: new Date(),
              };
              addConversationMessage(chatId, errorMessage);
              setConversationThinking(chatId, false);
            }
          }
          break;
        }
        
        case 'crm_approval_result':
        case 'tool_approval_result': {
          console.log('[AppLayout] Tool approval result:', parsed.operation_id, parsed.type);
          setCrmApprovalResults((prev) => {
            const next = new Map(prev);
            next.set(parsed.operation_id, parsed);
            return next;
          });
          break;
        }
        
        case 'tool_progress': {
          // Tool progress update - update tool result in real-time
          const { conversation_id, tool_id, tool_name, result, status } = parsed;
          if (conversation_id && tool_id) {
            updateConversationToolMessage(conversation_id, tool_id, {
              toolName: tool_name,
              result,
              status: status === 'complete' ? 'complete' : 'running',
            });
          }
          break;
        }

        case 'new_message': {
          // New message from another participant in a shared conversation
          const { conversation_id, message, sender_user_id } = parsed;
          const currentUserId = useAppStore.getState().user?.id;
          
          // Skip if this is our own message (already added optimistically)
          if (sender_user_id === currentUserId) {
            console.log('[AppLayout] Skipping own message broadcast');
            break;
          }
          
          if (conversation_id && message) {
            console.log('[AppLayout] New message from participant:', sender_user_id, 'in conversation:', conversation_id);
            // Convert API message format to store format
            const chatMessage: ChatMessage = {
              id: message.id,
              role: message.role as 'user' | 'assistant',
              contentBlocks: message.content_blocks as ContentBlock[],
              timestamp: new Date(message.created_at),
              userId: message.user_id ?? undefined,
              senderName: message.sender_name ?? undefined,
              senderEmail: message.sender_email ?? undefined,
            };
            addConversationMessage(conversation_id, chatMessage);
          }
          break;
        }

        case 'notification': {
          const notif = (parsed as { notification?: { conversation_id?: string } }).notification;
          if (notif?.conversation_id) {
            useChatStore.getState().addUnreadConversation(notif.conversation_id);
          }
          break;
        }

        case 'message_sent': {
          const { conversation_id, agent_responding } = parsed as { conversation_id?: string; agent_responding?: boolean };
          if (conversation_id) {
            setConversationThinking(conversation_id, false);
            if (agent_responding !== undefined) {
              useChatStore.getState().setConversationAgentResponding(conversation_id, agent_responding);
            }
          }
          break;
        }

        case 'summary_updated': {
          const { conversation_id, summary } = parsed;
          if (conversation_id && summary) {
            useAppStore.getState().setConversationSummary(conversation_id, summary);
          }
          break;
        }

        case 'workstreams_stale': {
          window.dispatchEvent(new Event('workstreams-stale'));
          break;
        }

        default: {
          // Handle pending_changes_updated from backend WS broadcast
          const msg = parsed as Record<string, unknown>;
          if (msg.type === 'pending_changes_updated') {
            window.dispatchEvent(new Event('pending-changes-updated'));
          }
          break;
        }
      }
    } catch {
      // Not JSON, ignore
    }
  }, [
    shouldBroadcastWebSocket,
    setActiveTasks, setConversationActiveTask, setConversationThinking,
    addConversation, addConversationMessage, appendToConversationStreaming,
    startConversationStreaming, markConversationMessageComplete, updateConversationToolMessage,
    addConversationArtifactBlock, addConversationAppBlock, setCurrentChatId,
    setConversationContextTokens, advanceConversationChunkIndex, queryClient
  ]);

  // Cross-tab sync for optimistic UI and streamed updates
  useEffect(() => {
    if (!crossTab.isAvailable) {
      console.log('[AppLayout] Cross-tab sync unavailable (BroadcastChannel not supported)');
      return;
    }
    return subscribeCrossTab((event) => {
      if (event.kind === 'ws-event') {
        console.log('[AppLayout] Cross-tab WebSocket event received:', event.payload.message);
        handleWebSocketMessage(event.payload.message, 'broadcast');
        return;
      }

      if (event.kind === 'optimistic_message') {
        const { conversationId, message, setThinking } = event.payload;
        const state = useAppStore.getState();
        const existingMessages = state.conversations[conversationId]?.messages ?? [];
        const alreadyPresent = existingMessages.some((msg) => msg.id === message.id);
        if (alreadyPresent) {
          console.log('[AppLayout] Skipping duplicate optimistic message:', message.id);
          return;
        }
        console.log('[AppLayout] Applying optimistic message from another tab:', message.id);
        addConversationMessage(conversationId, message);
        if (setThinking) {
          setConversationThinking(conversationId, true);
        }
      }
    });
  }, [addConversationMessage, handleWebSocketMessage, setConversationThinking]);

  // Global WebSocket connection - authenticated via JWT token
  // reconnectKey = org ID so the socket reconnects when the user switches organizations
  const { sendJson, isConnected, connectionState } = useWebSocket(
    user ? '/ws/chat' : '',
    {
      onMessage: (message) => handleWebSocketMessage(message, 'ws'),
      onConnect: () => console.log('[AppLayout] WebSocket connected'),
      onDisconnect: () => console.log('[AppLayout] WebSocket disconnected'),
    },
    organization?.id ?? '',
  );

  useEffect(() => {
    sendJsonRef.current = sendJson;
    return () => { sendJsonRef.current = null; };
  }, [sendJson]);

  // Fetch conversations on mount (only once per user)
  const userId = user?.id;
  useEffect(() => {
    if (userId) {
      void fetchConversations();
    }
  }, [userId, fetchConversations]);

  // Fetch unread notifications on mount
  useEffect(() => {
    if (!userId) return;
    void (async () => {
      try {
        const { data } = await apiRequest<Array<{ conversation_id: string }>>('/notifications/?unread_only=true');
        const ids = [...new Set((data ?? []).map((n) => n.conversation_id))];
        useChatStore.getState().setUnreadConversations(ids);
      } catch {
        // Best-effort; ignore errors
      }
    })();
  }, [userId]);

  // Listen for navigation events from child components (e.g., Home banner)
  useEffect(() => {
    const handleNavigate = (event: Event): void => {
      const customEvent = event as CustomEvent<string>;
      if (customEvent.detail) {
        setCurrentView(customEvent.detail as 'home' | 'chat' | 'data-sources' | 'data' | 'workflows' | 'memory' | 'admin');
      }
    };
    window.addEventListener('navigate', handleNavigate);
    return () => window.removeEventListener('navigate', handleNavigate);
  }, [setCurrentView]);

  const handleSelectChat = useCallback((chatId: string): void => {
    setCurrentChatId(chatId);
    setCurrentView('chat');
  }, [setCurrentChatId, setCurrentView]);

  const handleDeleteChat = useCallback((chatId: string): void => {
    void deleteConversation(chatId);
  }, [deleteConversation]);

  const handleConversationNotFound = useCallback((): void => {
    setCurrentChatId(null);
  }, [setCurrentChatId]);

  const isGlobalAdmin: boolean = user?.roles.includes('global_admin') ?? false;

  useEffect(() => {
    if (currentView === 'admin' && !isGlobalAdmin) {
      setCurrentView('home');
    }
  }, [currentView, isGlobalAdmin, setCurrentView]);

  // Guard against missing user/org (shouldn't happen, but be safe)
  if (!user || !organization) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-surface-950">
        <div className="flex flex-col items-center gap-6">
          <div className="relative">
            <div className="w-14 h-14 rounded-full border-2 border-surface-700 border-t-primary-500 animate-spin" />
            <div className="absolute inset-0 flex items-center justify-center">
              <img src={LOGO_PATH} alt="" className="w-7 h-7 opacity-90" />
            </div>
          </div>
          <div className="flex flex-col items-center gap-1">
            <p className="text-surface-200 font-medium">Loading</p>
            <p className="text-surface-500 text-sm">Preparing your workspace…</p>
          </div>
        </div>
      </div>
    );
  }

  // Get current view title for mobile header
  const viewTitles: Record<string, string> = {
    home: 'Home',
    chat: 'Chat',
    chats: 'All Chats',
    'data-sources': 'Connectors',
    workflows: 'Workflows',
    memory: 'Memory',
    apps: 'Apps',
    'app-view': 'App',
    'artifact-view': 'Artifact',
    admin: 'Global Admin',
    'pending-changes': 'Pending Changes',
  };

  return (
    <div className="h-full flex flex-col bg-surface-950 overflow-hidden">
      {/* Masquerade Banner */}
      {masquerade && (
        <div className="bg-amber-500/20 dark:bg-amber-500/20 border-b border-amber-500/30 px-4 py-2 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-2 text-amber-700 dark:text-amber-400">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
            </svg>
            <span className="text-sm font-medium">
              Viewing as <strong>{masquerade.masqueradingAs.email}</strong>
              {masquerade.masqueradeOrganization && (
                <span className="text-amber-600/80 dark:text-amber-400/70"> ({masquerade.masqueradeOrganization.name})</span>
              )}
            </span>
          </div>
          <button
            onClick={exitMasquerade}
            className="px-3 py-1 rounded-lg bg-amber-500/30 hover:bg-amber-500/40 text-amber-800 dark:text-amber-300 text-sm font-medium transition-colors"
          >
            Exit Masquerade
          </button>
        </div>
      )}

      {/* Main Content Row */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0 overflow-hidden">
      {/* Mobile Header */}
      {isMobile && (
        <header className="h-14 bg-surface-900 border-b border-surface-800 flex items-center justify-between px-4 flex-shrink-0">
          <button
            onClick={() => setMobileSidebarOpen(true)}
            className="p-2 -ml-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
            aria-label="Open menu"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-surface-800 flex items-center justify-center">
              <img src={LOGO_PATH} alt={APP_NAME} className="w-4 h-4" />
            </div>
            <span className="font-semibold text-surface-100">{viewTitles[currentView] || APP_NAME}</span>
          </div>
          <button
            onClick={startNewChat}
            className="p-2 -mr-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
            aria-label="New chat"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
          </button>
        </header>
      )}

      {/* Mobile Sidebar Backdrop */}
      {isMobile && mobileSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-40 transition-opacity"
          onClick={() => setMobileSidebarOpen(false)}
        />
      )}

      {/* Sidebar - hidden on mobile, shown as overlay when open */}
      <div className={`
        ${isMobile 
          ? `fixed inset-y-0 left-0 z-50 transform transition-transform duration-300 ease-in-out ${mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`
          : ''
        }
      `}>
        <Sidebar
          collapsed={isMobile ? false : sidebarCollapsed}
          onToggleCollapse={() => isMobile ? setMobileSidebarOpen(false) : setSidebarCollapsed(!sidebarCollapsed)}
          currentView={currentView}
          onViewChange={setCurrentView}
          connectedSourcesCount={connectedIntegrationsCount}
          workflowCount={workflowCount}
          pendingChangesCount={pendingChangesCount}
          recentChats={recentChats}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
          currentChatId={currentChatId}
          onNewChat={startNewChat}
          organization={organization}
          members={teamData?.members ?? []}
          creditsDisplay={billingStatus ? { balance: billingStatus.credits_balance, included: billingStatus.credits_included } : null}
          onOpenOrgPanel={() => { setOrgPanelTab('team'); setShowOrgPanel(true); }}
          onOpenBilling={() => { setOrgPanelTab('billing'); setShowOrgPanel(true); }}
          onCreateNewOrg={onCreateNewOrg}
          onOpenProfilePanel={() => setShowProfilePanel(true)}
          isMobile={isMobile}
          onCloseMobile={() => setMobileSidebarOpen(false)}
        />
      </div>

      {/* Resize divider (desktop only, expanded sidebar only) */}
      {!isMobile && !sidebarCollapsed && (
        <div
          onMouseDown={handleDividerMouseDown}
          onDoubleClick={() => setSidebarWidth(256)}
          className="w-1 cursor-col-resize hover:bg-primary-500/40 active:bg-primary-500/60 transition-colors flex-shrink-0"
        />
      )}

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
        {/* Release Stage Banner */}
        {RELEASE_STAGE.stage && showReleaseBanner && (
          <div className="flex-shrink-0 px-4 md:px-6 py-3 bg-primary-500/10 border-b border-primary-500/20">
            <div className="flex items-center justify-between gap-3 max-w-7xl mx-auto">
              <p className="text-sm text-surface-300 min-w-0 flex-1 leading-relaxed">
                <span className="text-primary-400 font-semibold mr-2">
                  {RELEASE_STAGE.message}
                </span>
                <span>{RELEASE_STAGE.description}</span>
              </p>
              <button
                onClick={dismissReleaseBanner}
                className="flex-shrink-0 self-center text-surface-400 hover:text-surface-200 transition-colors px-1"
                aria-label="Dismiss"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
        )}
        {currentView === 'home' && (
          <Home />
        )}
        {currentView === 'chat' && (
          <Chat
            userId={user.id}
            organizationId={organization.id}
            chatId={currentChatId}
            sendMessage={sendJson}
            isConnected={isConnected}
            connectionState={connectionState}
            crmApprovalResults={crmApprovalResults}
            onConversationNotFound={handleConversationNotFound}
            creditsInfo={billingStatus ? { balance: billingStatus.credits_balance, included: billingStatus.credits_included } : null}
          />
        )}
        {currentView === 'chats' && (
          <ChatsList
            chats={recentChats}
            onSelectChat={handleSelectChat}
            onNewChat={startNewChat}
          />
        )}
        {currentView === 'data-sources' && (
          <DataSources />
        )}
        {currentView === 'data' && (
          <Data />
        )}
        {currentView === 'workflows' && (
          <Workflows />
        )}
        {currentView === 'memory' && (
          <Memories />
        )}
        {currentView === 'apps' && (
          <Suspense fallback={<div className="flex items-center justify-center h-64"><div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" /></div>}>
            <AppsGallery />
          </Suspense>
        )}
        {currentView === 'documents' && (
          <Suspense fallback={<div className="flex items-center justify-center h-64"><div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" /></div>}>
            <DocumentsGallery />
          </Suspense>
        )}
        {currentView === 'app-view' && currentAppId && (
          <Suspense fallback={<div className="flex items-center justify-center h-64"><div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" /></div>}>
            <AppFullView appId={currentAppId} />
          </Suspense>
        )}
        {currentView === 'artifact-view' && currentArtifactId && (
          <Suspense fallback={<div className="flex items-center justify-center h-64"><div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" /></div>}>
            <ArtifactFullView artifactId={currentArtifactId} />
          </Suspense>
        )}
        {currentView === 'admin' && isGlobalAdmin && (
          <AdminPanel />
        )}
        {currentView === 'pending-changes' && (
          <PendingChangesPage />
        )}
      </main>

      {/* Organization Panel */}
      {showOrgPanel && (
        <OrganizationPanel
          key={`org-panel-${organization.id}`}
          organization={organization}
          currentUser={user}
          initialTab={orgPanelTab}
          onClose={() => setShowOrgPanel(false)}
        />
      )}

      {/* Profile Panel */}
      {showProfilePanel && (
        <ProfilePanel
          user={user}
          onClose={() => setShowProfilePanel(false)}
          onLogout={onLogout}
          onUpdateUser={(updates) => setUser({ ...user, ...updates })}
        />
      )}
      </div>{/* End Main Content Row */}
    </div>
  );
}
