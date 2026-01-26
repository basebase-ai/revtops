/**
 * Main application layout with collapsible sidebar.
 * 
 * Modeled after Claude's UX with:
 * - Collapsible left sidebar (icons when collapsed)
 * - New Chat button
 * - Data Sources tab with badge
 * - Chats tab with recent conversations
 * - Organization & Profile sections at bottom
 * 
 * Also manages global WebSocket connection for background task updates.
 * Tasks continue running server-side even when browser tabs are closed.
 */

import { useState, useEffect, useCallback } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { Sidebar } from './Sidebar';
import { Home } from './Home';
import { DataSources } from './DataSources';
import { Search } from './Search';
import { Chat } from './Chat';
import { AdminPanel } from './AdminPanel';
import { OrganizationPanel } from './OrganizationPanel';
import { ProfilePanel } from './ProfilePanel';
import { useAppStore, type ActiveTask } from '../store';
import { useIntegrations, useTeamMembers, useWebSocket } from '../hooks';

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
  chunks: Array<{ index: number; type: string; data: unknown; timestamp: string }>;
  task_status: string;
}

interface WsCrmApprovalResult {
  type: 'crm_approval_result';
  operation_id: string;
  status: string;
  [key: string]: unknown;
}

type WsMessage = WsActiveTasks | WsTaskStarted | WsTaskChunk | WsTaskComplete | WsConversationCreated | WsCatchup | WsCrmApprovalResult;

// Props
interface AppLayoutProps {
  onLogout: () => void;
}

export function AppLayout({ onLogout }: AppLayoutProps): JSX.Element {
  // Get state from Zustand store using shallow comparison to prevent unnecessary re-renders
  const {
    user,
    organization,
    sidebarCollapsed,
    currentView,
    currentChatId,
    recentChats,
  } = useAppStore(
    useShallow((state) => ({
      user: state.user,
      organization: state.organization,
      sidebarCollapsed: state.sidebarCollapsed,
      currentView: state.currentView,
      currentChatId: state.currentChatId,
      recentChats: state.recentChats,
    }))
  );

  // React Query: Get integrations for connected count badge
  const { data: integrations = [] } = useIntegrations(
    organization?.id ?? null, 
    user?.id ?? null
  );
  const connectedIntegrationsCount = integrations.filter((i) => i.isActive).length;

  // React Query: Get team members for member count (single source of truth)
  const { data: teamMembers = [] } = useTeamMembers(
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
  const addConversation = useAppStore((state) => state.addConversation);
  const addConversationMessage = useAppStore((state) => state.addConversationMessage);
  const appendToConversationStreaming = useAppStore((state) => state.appendToConversationStreaming);
  const startConversationStreaming = useAppStore((state) => state.startConversationStreaming);
  const markConversationMessageComplete = useAppStore((state) => state.markConversationMessageComplete);
  const setConversationThinking = useAppStore((state) => state.setConversationThinking);
  const updateConversationToolMessage = useAppStore((state) => state.updateConversationToolMessage);
  
  // Panels
  const [showOrgPanel, setShowOrgPanel] = useState(false);
  const [showProfilePanel, setShowProfilePanel] = useState(false);

  // CRM approval results (shared across chats) - use state to trigger re-renders
  const [crmApprovalResults, setCrmApprovalResults] = useState<Map<string, unknown>>(() => new Map());

  // Handle WebSocket messages
  const handleWebSocketMessage = useCallback((message: string) => {
    try {
      const parsed = JSON.parse(message) as WsMessage;
      
      switch (parsed.type) {
        case 'active_tasks': {
          console.log('[AppLayout] Received active tasks:', parsed.tasks.length);
          setActiveTasks(parsed.tasks);
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
          
          // Route chunk to appropriate conversation
          if (chunk.type === 'text_delta' && typeof chunkData === 'string') {
            // Text chunk - append to streaming message
            const state = useAppStore.getState();
            const convState = state.conversations[conversation_id];
            if (convState?.streamingMessageId) {
              appendToConversationStreaming(conversation_id, chunkData);
            } else {
              // Start new streaming message
              const msgId = `assistant-${Date.now()}`;
              startConversationStreaming(conversation_id, msgId, chunkData);
            }
          } else if (typeof chunkData === 'object' && chunkData !== null) {
            const data = chunkData as Record<string, unknown>;
            
            if (data.type === 'tool_call') {
              // Tool call starting
              const state = useAppStore.getState();
              const convState = state.conversations[conversation_id];
              
              if (convState?.streamingMessageId) {
                // Add tool_use block to existing streaming message
                const updated = convState.messages.map((msg) => {
                  if (msg.id !== convState.streamingMessageId) return msg;
                  return {
                    ...msg,
                    contentBlocks: [
                      ...msg.contentBlocks,
                      {
                        type: 'tool_use' as const,
                        id: data.tool_id as string,
                        name: data.tool_name as string,
                        input: data.tool_input as Record<string, unknown>,
                        status: 'running' as const,
                      },
                    ],
                  };
                });
                useAppStore.setState({
                  conversations: {
                    ...state.conversations,
                    [conversation_id]: { ...convState, messages: updated },
                  },
                });
              } else {
                // Create new message with tool_use block
                addConversationMessage(conversation_id, {
                  id: `assistant-${Date.now()}`,
                  role: 'assistant',
                  contentBlocks: [{
                    type: 'tool_use',
                    id: data.tool_id as string,
                    name: data.tool_name as string,
                    input: data.tool_input as Record<string, unknown>,
                    status: 'running',
                  }],
                  timestamp: new Date(),
                });
              }
            } else if (data.type === 'tool_result') {
              // Tool result received
              updateConversationToolMessage(conversation_id, data.tool_id as string, {
                result: data.result as Record<string, unknown>,
                status: 'complete',
              });
            } else if (data.type === 'text_block_complete') {
              // Text block complete, tools incoming
              markConversationMessageComplete(conversation_id);
            } else if (data.type === 'crm_approval_result') {
              // Store CRM approval result - create new Map to trigger re-render
              setCrmApprovalResults((prev) => {
                const next = new Map(prev);
                next.set(data.operation_id as string, data);
                return next;
              });
            }
          }
          break;
        }
        
        case 'task_complete': {
          console.log('[AppLayout] Task complete:', parsed.task_id, 'status:', parsed.status);
          setConversationActiveTask(parsed.conversation_id, null);
          setConversationThinking(parsed.conversation_id, false);
          markConversationMessageComplete(parsed.conversation_id);
          break;
        }
        
        case 'conversation_created': {
          const title = parsed.title || 'New Chat';
          console.log('[AppLayout] Conversation created:', parsed.conversation_id, 'title:', title);
          addConversation(parsed.conversation_id, title);
          // Update currentChatId so Chat component knows about the new conversation
          setCurrentChatId(parsed.conversation_id);
          break;
        }
        
        case 'catchup': {
          console.log('[AppLayout] Catchup for task:', parsed.task_id, 'chunks:', parsed.chunks.length);
          // Process catchup chunks - they're already ordered by index
          // For now, just mark task as complete if it's done
          if (parsed.task_status !== 'running') {
            // Task is complete, no need to process chunks
          }
          break;
        }
        
        case 'crm_approval_result': {
          console.log('[AppLayout] CRM approval result:', parsed.operation_id);
          setCrmApprovalResults((prev) => {
            const next = new Map(prev);
            next.set(parsed.operation_id, parsed);
            return next;
          });
          break;
        }
      }
    } catch {
      // Not JSON, ignore
    }
  }, [
    setActiveTasks, setConversationActiveTask, setConversationThinking,
    addConversation, addConversationMessage, appendToConversationStreaming,
    startConversationStreaming, markConversationMessageComplete, updateConversationToolMessage
  ]);

  // Global WebSocket connection
  const { sendJson, isConnected, connectionState } = useWebSocket(
    user ? `/ws/chat/${user.id}` : '',
    {
      onMessage: handleWebSocketMessage,
      onConnect: () => console.log('[AppLayout] WebSocket connected'),
      onDisconnect: () => console.log('[AppLayout] WebSocket disconnected'),
    }
  );

  // Fetch conversations on mount
  useEffect(() => {
    if (user) {
      void fetchConversations();
    }
  }, [user, fetchConversations]);

  const handleSelectChat = useCallback((chatId: string): void => {
    setCurrentChatId(chatId);
    setCurrentView('chat');
  }, [setCurrentChatId, setCurrentView]);

  const handleDeleteChat = useCallback((chatId: string): void => {
    void deleteConversation(chatId);
  }, [deleteConversation]);

  // Guard against missing user/org (shouldn't happen, but be safe)
  if (!user || !organization) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-surface-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="h-screen flex bg-surface-950 overflow-hidden">
      {/* Sidebar */}
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        currentView={currentView}
        onViewChange={setCurrentView}
        connectedSourcesCount={connectedIntegrationsCount}
        recentChats={recentChats.slice(0, 10)}
        onSelectChat={handleSelectChat}
        onDeleteChat={handleDeleteChat}
        currentChatId={currentChatId}
        onNewChat={startNewChat}
        organization={organization}
        memberCount={teamMembers.length}
        onOpenOrgPanel={() => setShowOrgPanel(true)}
        onOpenProfilePanel={() => setShowProfilePanel(true)}
      />

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
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
          />
        )}
        {currentView === 'data-sources' && (
          <DataSources />
        )}
        {currentView === 'search' && (
          <Search organizationId={organization.id} />
        )}
        {currentView === 'admin' && (
          <AdminPanel />
        )}
      </main>

      {/* Organization Panel */}
      {showOrgPanel && (
        <OrganizationPanel
          organization={organization}
          currentUser={user}
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
    </div>
  );
}
