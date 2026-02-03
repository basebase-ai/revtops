/**
 * Main application layout with collapsible sidebar.
 * 
 * Modeled after Claude's UX with:
 * - Collapsible left sidebar (icons when collapsed)
 * - Slide-out drawer on mobile
 * - New Chat button
 * - Data Sources tab with badge
 * - Chats tab with recent conversations
 * - Organization & Profile sections at bottom
 * 
 * Also manages global WebSocket connection for background task updates.
 * Tasks continue running server-side even when browser tabs are closed.
 */

import { useState, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API_BASE } from '../lib/api';

// Hook to detect mobile viewport
function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false);
  
  useEffect(() => {
    const checkMobile = (): void => {
      setIsMobile(window.innerWidth < 768);
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);
  
  return isMobile;
}
import { useShallow } from 'zustand/react/shallow';
import { Sidebar } from './Sidebar';
import { Home } from './Home';
import { DataSources } from './DataSources';
import { Data } from './Data';
import { Search } from './Search';
import { Chat } from './Chat';
import { Workflows } from './Workflows';
import { AdminPanel } from './AdminPanel';
import { OrganizationPanel } from './OrganizationPanel';
import { ProfilePanel } from './ProfilePanel';
import { useAppStore, useMasquerade, useIntegrations, type ActiveTask } from '../store';
import { useTeamMembers, useWebSocket } from '../hooks';

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

interface WsToolApprovalResult {
  type: 'tool_approval_result';
  operation_id: string;
  status: string;
  [key: string]: unknown;
}

type WsMessage = WsActiveTasks | WsTaskStarted | WsTaskChunk | WsTaskComplete | WsConversationCreated | WsCatchup | WsCrmApprovalResult | WsToolApprovalResult;

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
  const exitMasquerade = useAppStore((state) => state.exitMasquerade);
  
  // Masquerade state
  const masquerade = useMasquerade();
  const addConversation = useAppStore((state) => state.addConversation);
  const addConversationMessage = useAppStore((state) => state.addConversationMessage);
  const appendToConversationStreaming = useAppStore((state) => state.appendToConversationStreaming);
  const startConversationStreaming = useAppStore((state) => state.startConversationStreaming);
  const markConversationMessageComplete = useAppStore((state) => state.markConversationMessageComplete);
  const setConversationThinking = useAppStore((state) => state.setConversationThinking);
  const updateConversationToolMessage = useAppStore((state) => state.updateConversationToolMessage);
  
  // Mobile responsive state
  const isMobile = useIsMobile();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  
  // Close mobile sidebar when view changes
  useEffect(() => {
    if (isMobile) {
      setMobileSidebarOpen(false);
    }
  }, [currentView, currentChatId, isMobile]);
  
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
              
              // If workflows table was modified, notify the Workflows component to refresh
              const result = data.result as Record<string, unknown> | undefined;
              if (result?.table === 'workflows' && result?.success) {
                window.dispatchEvent(new Event('workflows-updated'));
              }
              
              // If CRM write tool completed, notify PendingChangesBar to refresh
              const toolName = data.tool_name as string | undefined;
              if (toolName === 'crm_write' || toolName === 'run_sql_write') {
                window.dispatchEvent(new Event('pending-changes-updated'));
              }
            } else if (data.type === 'text_block_complete') {
              // Text block complete, tools incoming
              markConversationMessageComplete(conversation_id);
            } else if (data.type === 'crm_approval_result' || data.type === 'tool_approval_result') {
              // Store tool approval result - create new Map to trigger re-render
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
      }
    } catch {
      // Not JSON, ignore
    }
  }, [
    setActiveTasks, setConversationActiveTask, setConversationThinking,
    addConversation, addConversationMessage, appendToConversationStreaming,
    startConversationStreaming, markConversationMessageComplete, updateConversationToolMessage,
    setCurrentChatId
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

  // Listen for navigation events from child components (e.g., Home banner)
  useEffect(() => {
    const handleNavigate = (event: Event): void => {
      const customEvent = event as CustomEvent<string>;
      if (customEvent.detail) {
        setCurrentView(customEvent.detail as 'home' | 'chat' | 'data-sources' | 'search' | 'workflows' | 'admin');
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

  // Guard against missing user/org (shouldn't happen, but be safe)
  if (!user || !organization) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-surface-400">Loading...</p>
      </div>
    );
  }

  // Get current view title for mobile header
  const viewTitles: Record<string, string> = {
    home: 'Home',
    chat: 'Chat',
    'data-sources': 'Data Sources',
    search: 'Search',
    workflows: 'Workflows',
    admin: 'Admin',
  };

  return (
    <div className="h-screen flex flex-col bg-surface-950 overflow-hidden">
      {/* Masquerade Banner */}
      {masquerade && (
        <div className="bg-amber-500/20 border-b border-amber-500/30 px-4 py-2 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-2 text-amber-400">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
            </svg>
            <span className="text-sm font-medium">
              Viewing as <strong>{masquerade.masqueradingAs.email}</strong>
              {masquerade.masqueradeOrganization && (
                <span className="text-amber-400/70"> ({masquerade.masqueradeOrganization.name})</span>
              )}
            </span>
          </div>
          <button
            onClick={exitMasquerade}
            className="px-3 py-1 rounded-lg bg-amber-500/30 hover:bg-amber-500/40 text-amber-300 text-sm font-medium transition-colors"
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
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <img src="/logo.svg" alt="Revtops" className="w-4 h-4 invert" />
            </div>
            <span className="font-semibold text-surface-100">{viewTitles[currentView] || 'Revtops'}</span>
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
          recentChats={recentChats.slice(0, 10)}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
          currentChatId={currentChatId}
          onNewChat={startNewChat}
          organization={organization}
          memberCount={teamMembers.length}
          onOpenOrgPanel={() => setShowOrgPanel(true)}
          onOpenProfilePanel={() => setShowProfilePanel(true)}
          isMobile={isMobile}
          onCloseMobile={() => setMobileSidebarOpen(false)}
        />
      </div>

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
        {currentView === 'data' && (
          <Data />
        )}
        {currentView === 'search' && (
          <Search organizationId={organization.id} />
        )}
        {currentView === 'workflows' && (
          <Workflows />
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
      </div>{/* End Main Content Row */}
    </div>
  );
}
