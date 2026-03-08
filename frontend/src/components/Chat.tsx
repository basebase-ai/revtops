/**
 * Chat interface component.
 *
 * Features:
 * - Uses global WebSocket from AppLayout for persistent connections
 * - Per-conversation state (messages, streaming) from Zustand
 * - Background tasks continue even when switching chats
 * - Streaming response display with "thinking" indicator
 * - Artifact viewer for dashboards/reports
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { ArtifactViewer, type FileArtifact } from './ArtifactViewer';
import { ArtifactTile } from './ArtifactTile';
import { AppTile } from './apps/AppTile';
import { AppPreviewPanel } from './apps/AppPreviewPanel';
import { Avatar } from './Avatar';
import { PendingApprovalCard, type ApprovalResult } from './PendingApprovalCard';
import { getConversation, updateConversation, uploadChatFile, type UploadResponse } from '../api/client';
import { useTeamMembers } from '../hooks/useOrganization';
import { apiRequest } from '../lib/api';
import { crossTab } from '../lib/crossTab';
import { APP_NAME, LOGO_PATH } from '../lib/brand';
import {
  useAppStore,
  useConversationState,
  useActiveTasksByConversation,
  useConnectedIntegrations,
  type AppBlock,
  type ChatMessage,
  type ConversationSummaryData,
  type Integration,
  type ToolCallData,
  type ToolUseBlock,
  type ErrorBlock,
  type AttachmentBlock,
} from '../store';

// Legacy data artifact format
interface LegacyArtifact {
  id: string;
  type: string;
  title: string;
  data: Record<string, unknown>;
}

// Union type for all artifact formats
type AnyArtifact = LegacyArtifact | FileArtifact;

interface ChatProps {
  userId?: string | null;
  organizationId: string;
  chatId?: string | null;
  sendMessage: (data: Record<string, unknown>) => void;
  isConnected: boolean;
  connectionState: 'connecting' | 'connected' | 'disconnected' | 'error';
  crmApprovalResults: Map<string, unknown>;
  /** Called when the current conversation ID returns 404 (e.g. deleted or wrong org). Clears selection. */
  onConversationNotFound?: () => void;
  /** Credits remaining and total included for the org. Null if billing not loaded. */
  creditsInfo?: { balance: number; included: number } | null;
}

// Tool approval result type (received via parent component)
interface WsToolApprovalResult {
  type: 'tool_approval_result';
  operation_id: string;
  tool_name: string;
  status: string;
  message?: string;
  success_count?: number;
  failure_count?: number;
  skipped_count?: number;
  error?: string;
}

// Tool approval state tracking (generic for all tools)
interface ToolApprovalState {
  operationId: string;
  toolName: string;
  isProcessing: boolean;
  result: WsToolApprovalResult | null;
}

function SummaryCard({ summary }: { summary: ConversationSummaryData }): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mx-auto max-w-3xl mb-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left rounded-lg border border-surface-700 bg-surface-850 px-4 py-3 transition-colors hover:bg-surface-800"
      >
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-primary-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span className="text-sm font-medium text-surface-300">Conversation Summary</span>
          <svg
            className={`w-4 h-4 text-surface-400 ml-auto transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
        {!expanded && (
          <p className="mt-1 text-sm text-surface-400 truncate">{summary.overall}</p>
        )}
      </button>
      {expanded && (
        <div className="mt-0 rounded-b-lg border border-t-0 border-surface-700 bg-surface-850 px-4 py-3 space-y-3">
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-surface-400 mb-1">Overall</h4>
            <p className="text-sm text-surface-200">{summary.overall}</p>
          </div>
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-surface-400 mb-1">Recent Updates</h4>
            <p className="text-sm text-surface-200">{summary.recent}</p>
          </div>
        </div>
      )}
    </div>
  );
}

export function Chat({
  userId,
  organizationId,
  chatId,
  sendMessage,
  isConnected,
  connectionState,
  crmApprovalResults,
  onConversationNotFound,
  creditsInfo,
}: ChatProps): JSX.Element {
  // Credits status
  const creditsPct = creditsInfo && creditsInfo.included > 0 ? creditsInfo.balance / creditsInfo.included : 1;
  const outOfCredits = creditsInfo != null && creditsInfo.balance <= 0;
  const lowCredits = creditsInfo != null && creditsPct <= 0.1 && !outOfCredits;

  // Get per-conversation state from Zustand
  const conversationState = useConversationState(chatId ?? null);
  const activeTasksByConversation = useActiveTasksByConversation();
  const chatTitle = conversationState?.title ?? 'New Chat';
  const conversationThinking = conversationState?.isThinking ?? false;
  
  // Get actions from Zustand (stable references)
  const addConversationMessage = useAppStore((s) => s.addConversationMessage);
  const setConversationMessages = useAppStore((s) => s.setConversationMessages);
  const setConversationTitle = useAppStore((s) => s.setConversationTitle);
  const setConversationSummary = useAppStore((s) => s.setConversationSummary);
  const setConversationThinking = useAppStore((s) => s.setConversationThinking);
  const setConversationHasMore = useAppStore((s) => s.setConversationHasMore);
  const fetchOlderMessages = useAppStore((s) => s.fetchOlderMessages);
  const pendingChatInput = useAppStore((s) => s.pendingChatInput);
  const setPendingChatInput = useAppStore((s) => s.setPendingChatInput);
  const pendingChatAutoSend = useAppStore((s) => s.pendingChatAutoSend);
  const setPendingChatAutoSend = useAppStore((s) => s.setPendingChatAutoSend);
  
  // Local state
  const [input, setInput] = useState<string>('');
  const [currentArtifact, setCurrentArtifact] = useState<AnyArtifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  // App preview panel state
  const [previewAppId, setPreviewAppId] = useState<string | null>(null);
  const [previewCollapsed, setPreviewCollapsed] = useState(false);
  const [previewDismissed, setPreviewDismissed] = useState(false);
  const [previewHeight, setPreviewHeight] = useState(300);
  const [selectedToolCall, setSelectedToolCall] = useState<ToolCallData | null>(null);
  const [toolApprovals, setToolApprovals] = useState<Map<string, ToolApprovalState>>(new Map());
  const [localConversationId, setLocalConversationId] = useState<string | null>(chatId ?? null);
  // Use activeTasksByConversation as fallback when chatId doesn't match (e.g. new chat before URL update, post-WS reconnect)
  const currentConvIdForTask: string | null = localConversationId ?? chatId ?? null;
  const taskIdFromMap: string | undefined = currentConvIdForTask ? activeTasksByConversation[currentConvIdForTask] : undefined;
  const activeTaskId: string | null = (conversationState?.activeTaskId ?? taskIdFromMap) ?? null;
  // Pending messages for new conversations (before we have an ID)
  const [pendingMessages, setPendingMessages] = useState<ChatMessage[]>([]);
  const [pendingThinking, setPendingThinking] = useState<boolean>(false);
  const [conversationType, setConversationType] = useState<string | null>(null);
  const [conversationScope, setConversationScope] = useState<'private' | 'shared'>('shared');
  const [conversationCreatorId, setConversationCreatorId] = useState<string | null>(null);
  const [isEditingHeaderTitle, setIsEditingHeaderTitle] = useState(false);
  const [headerTitleDraft, setHeaderTitleDraft] = useState('');
  const headerTitleInputRef = useRef<HTMLInputElement>(null);
  const [conversationParticipants, setConversationParticipants] = useState<Array<{
    id: string;
    name: string | null;
    email: string;
    avatarUrl?: string | null;
  }>>([]);
  const [isWorkflowPolling, setIsWorkflowPolling] = useState<boolean>(false);
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [newConversationScope, setNewConversationScope] = useState<'private' | 'shared'>('shared');
  const [showScrollToBottom, setShowScrollToBottom] = useState<boolean>(false);
  const [isLoadingOlder, setIsLoadingOlder] = useState<boolean>(false);
  const { data: teamMembersData } = useTeamMembers(organizationId ?? null, userId ?? null);
  
  // Attachment state
  const [pendingAttachments, setPendingAttachments] = useState<UploadResponse[]>([]);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [isDragOver, setIsDragOver] = useState<boolean>(false);
  
  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const isUserNearBottomRef = useRef<boolean>(true);
  const isProgrammaticScrollRef = useRef<boolean>(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pendingTitleRef = useRef<string | null>(null);
  const pendingMessagesRef = useRef<ChatMessage[]>([]);
  const pendingAutoSendRef = useRef<string | null>(null);
  const messagesRef = useRef<ChatMessage[]>([]); // Track current messages for polling comparison
  const workflowDoneRef = useRef<boolean>(false); // Prevents polling restart after workflow completes
  const loadInFlightChatIdRef = useRef<string | null>(null); // Dedupe load requests for same chatId
  const prevAppCountRef = useRef(0); // Track app count for auto-switching preview
  const dragContainerRef = useRef<HTMLDivElement>(null); // Container for drag-resize

  // Keep ref in sync with state
  pendingMessagesRef.current = pendingMessages;

  // Combined messages and thinking state (conversation + pending for new chats).
  // Always sort by timestamp to guard against race conditions where WebSocket
  // chunks (assistant message) arrive before the pending user message is moved
  // into the conversation state, which would otherwise cause out-of-order display.
  // If two messages share the same timestamp (common when backend timestamps have
  // coarse resolution), keep user questions before assistant responses so grouped
  // responses always appear after the asking question.
  const messages = useMemo(() => {
    const conversationMessages = conversationState?.messages ?? [];
    const combined: ChatMessage[] = pendingMessages.length > 0
      ? [...pendingMessages, ...conversationMessages]
      : conversationMessages;
    // Fast path: skip sort when already ordered (common case)
    let needsSort = false;
    for (let i = 1; i < combined.length; i++) {
      const prev = combined[i - 1] as ChatMessage;
      const curr = combined[i] as ChatMessage;
      if (prev.timestamp.getTime() > curr.timestamp.getTime()) {
        needsSort = true;
        break;
      }
    }
    if (!needsSort) return combined;

    return combined
      .map((message, index) => ({ message, index }))
      .sort((a, b) => {
        const timeDiff = a.message.timestamp.getTime() - b.message.timestamp.getTime();
        if (timeDiff !== 0) return timeDiff;

        if (a.message.role !== b.message.role) {
          return a.message.role === 'user' ? -1 : 1;
        }

        return a.index - b.index;
      })
      .map(({ message }) => message);
  }, [pendingMessages, conversationState?.messages]);
  const isThinking = pendingThinking || conversationThinking;
  const hasMoreMessages = conversationState?.hasMore ?? false;

  // Agent is running if there's an active task OR we're in a thinking/pending state
  const agentRunning = activeTaskId !== null || isThinking;

  // Extract all apps from conversation messages (for preview panel)
  const conversationApps = useMemo((): AppBlock["app"][] => {
    const apps: AppBlock["app"][] = [];
    const seen = new Set<string>();
    for (const msg of messages) {
      for (const block of msg.contentBlocks) {
        if (block.type === "app" && !seen.has((block as AppBlock).app.id)) {
          seen.add((block as AppBlock).app.id);
          apps.push((block as AppBlock).app);
        }
      }
    }
    return apps;
  }, [messages]);

  // Auto-switch to latest app when a new one appears
  useEffect(() => {
    if (conversationApps.length > prevAppCountRef.current && conversationApps.length > 0) {
      const latestApp = conversationApps[conversationApps.length - 1];
      if (latestApp) {
        setPreviewAppId(latestApp.id);
        setPreviewCollapsed(false);
        setPreviewDismissed(false);
      }
    }
    // Default to latest app if no selection yet
    if (conversationApps.length > 0 && previewAppId === null) {
      const latestApp = conversationApps[conversationApps.length - 1];
      if (latestApp) {
        setPreviewAppId(latestApp.id);
      }
    }
    prevAppCountRef.current = conversationApps.length;
  }, [conversationApps, previewAppId]);

  // Track if this conversation has uncommitted changes (write tools completed)
  const hasUncommittedChanges = useMemo(() => {
    return messages.some((msg) =>
      (msg.contentBlocks ?? []).some(
        (block) =>
          block.type === 'tool_use' &&
          (block as ToolUseBlock).status === 'complete' &&
          ((block as ToolUseBlock).name === 'write_to_system_of_record' ||
           (block as ToolUseBlock).name === 'run_sql_write')
      )
    );
  }, [messages]);

  // Handle tool approval (generic for all tools)
  const handleToolApprove = useCallback((operationId: string, options?: Record<string, unknown>) => {
    console.log('[Chat] Approving tool operation:', operationId, options);
    const existing = toolApprovals.get(operationId);
    setToolApprovals((prev) => {
      const newMap = new Map(prev);
      newMap.set(operationId, {
        operationId,
        toolName: existing?.toolName ?? 'unknown',
        isProcessing: true,
        result: null,
      });
      return newMap;
    });
    const currentConversationId = localConversationId || chatId;
    sendMessage({
      type: 'tool_approval',
      operation_id: operationId,
      approved: true,
      options: options ?? {},
      conversation_id: currentConversationId,
    });
  }, [sendMessage, localConversationId, chatId, toolApprovals]);

  // Handle tool cancel (generic for all tools)
  const handleToolCancel = useCallback((operationId: string) => {
    console.log('[Chat] Canceling tool operation:', operationId);
    const existing = toolApprovals.get(operationId);
    setToolApprovals((prev) => {
      const newMap = new Map(prev);
      newMap.set(operationId, {
        operationId,
        toolName: existing?.toolName ?? 'unknown',
        isProcessing: true,
        result: null,
      });
      return newMap;
    });
    const currentConversationId = localConversationId || chatId;
    sendMessage({
      type: 'tool_approval',
      operation_id: operationId,
      approved: false,
      conversation_id: currentConversationId,
    });
  }, [sendMessage, localConversationId, chatId, toolApprovals]);

  // Sync tool approval results from parent (handles both old crm_approval and new tool_approval)
  useEffect(() => {
    crmApprovalResults.forEach((result, operationId) => {
      setToolApprovals((prev) => {
        const existing = prev.get(operationId);
        if (existing?.isProcessing) {
          const newMap = new Map(prev);
          newMap.set(operationId, {
            ...existing,
            isProcessing: false,
            result: result as WsToolApprovalResult,
          });
          return newMap;
        }
        return prev;
      });
    });
  }, [crmApprovalResults]);

  // Reset local state when chatId changes
  useEffect(() => {
    setLocalConversationId(chatId ?? null);
    setCurrentArtifact(null);
    // Reset preview state for new conversation
    setPreviewDismissed(false);
    setPreviewAppId(null);
    setPreviewCollapsed(false);
    prevAppCountRef.current = 0;
    // Reset conversation type and scope when starting a new chat
    if (!chatId) {
      setConversationType(null);
      setIsWorkflowPolling(false);
      setNewConversationScope('shared'); // Default to shared for new conversations
      setConversationCreatorId(null);
    }
    setIsEditingHeaderTitle(false);
    // Reset workflow-done flag whenever the conversation changes
    workflowDoneRef.current = false;
    // Only clear pending messages if we're switching to an EXISTING chat
    // (i.e., when we have no pending messages to move to the new conversation)
    // If pendingMessages exist, the next effect will move them instead
    if (chatId && pendingMessagesRef.current.length === 0) {
      setPendingMessages([]);
      setPendingThinking(false);
    }
  }, [chatId]);

  // When a new conversation is created, move pending messages to it
  useEffect(() => {
    if (localConversationId && pendingMessages.length > 0) {
      console.log('[Chat] Moving pending messages to conversation:', localConversationId);
      // Add pending messages to the new conversation
      for (const msg of pendingMessages) {
        addConversationMessage(localConversationId, msg);
      }
      if (pendingThinking) {
        setConversationThinking(localConversationId, true);
      }
      // Clear pending state
      setPendingMessages([]);
      setPendingThinking(false);
    }
  }, [localConversationId, pendingMessages, pendingThinking, addConversationMessage, setConversationThinking]);

  // Listen for conversation_created in parent and update localConversationId
  // This happens via the store update from AppLayout

  // Auto-focus input when on a new empty chat
  useEffect(() => {
    if (chatId === null && messages.length === 0 && !isLoading && isConnected) {
      const timer = setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [chatId, messages.length, isLoading, isConnected]);

  // Load conversation when selecting an existing chat from sidebar
  useEffect(() => {
    // If no chatId, this is a new chat
    if (!chatId) {
      setIsLoading(false);
      return;
    }

    // If we have pending messages, we're creating a new conversation - don't load from API
    // The pending messages will be moved to this conversation by another effect
    // Use ref to avoid re-running effect when pendingMessages changes
    if (pendingMessagesRef.current.length > 0) {
      console.log('[Chat] Skipping load - have pending messages to move');
      // New conversation created by this user — set creator to self
      setConversationCreatorId(userId ?? null);
      setIsLoading(false);
      return;
    }

    // If we already have messages for this conversation in state, don't reload
    // (This handles both active tasks populating via WebSocket AND cached state)
    const existingState = useAppStore.getState().conversations[chatId];
    if (existingState && existingState.messages.length > 0) {
      console.log('[Chat] Using existing state for conversation:', chatId);
      // Still set conversation metadata from recentChats (skipping API fetch skips this otherwise)
      const chatInfo = useAppStore.getState().recentChats.find(c => c.id === chatId);
      if (chatInfo) {
        setConversationScope(chatInfo.scope);
        setConversationCreatorId(chatInfo.userId ?? null);
      }
      setIsLoading(false);
      return;
    }

    // Avoid duplicate in-flight requests (e.g. React Strict Mode or unstable callback deps)
    if (loadInFlightChatIdRef.current === chatId) {
      return;
    }
    loadInFlightChatIdRef.current = chatId;

    let cancelled = false;

    const loadConversation = async (): Promise<void> => {
      console.log('[Chat] Loading conversation:', chatId);
      setIsLoading(true);

      try {
        const { data, error } = await getConversation(chatId);

        if (cancelled) {
          console.log('[Chat] Load cancelled - chatId changed');
          return;
        }

        if (data && !error) {
          // Convert API messages to store format (content_blocks)
          const loadedMessages: ChatMessage[] = data.messages.map((msg) => ({
            id: msg.id,
            role: msg.role as 'user' | 'assistant',
            contentBlocks: msg.content_blocks,
            timestamp: new Date(msg.created_at),
            userId: msg.user_id ?? undefined,
            senderName: msg.sender_name ?? undefined,
            senderEmail: msg.sender_email ?? undefined,
            senderAvatarUrl: msg.sender_avatar_url ?? undefined,
          }));

          // Set conversation state
          setConversationMessages(chatId, loadedMessages);
          setConversationHasMore(chatId, data.has_more);
          setConversationTitle(chatId, data.title ?? 'New Chat');
          if (data.summary) {
            try {
              const parsed = JSON.parse(data.summary) as ConversationSummaryData;
              setConversationSummary(chatId, parsed);
            } catch {
              // Invalid summary JSON, ignore
            }
          }
          setConversationType(data.type ?? null);
          setConversationScope((data.scope ?? 'shared') as 'private' | 'shared');
          setConversationCreatorId(data.user_id ?? null);
          setConversationParticipants(
            (data.participants ?? []).map((p: { id: string; name: string | null; email: string; avatar_url?: string | null }) => ({
              id: p.id,
              name: p.name,
              email: p.email,
              avatarUrl: p.avatar_url,
            }))
          );
          console.log('[Chat] Loaded', loadedMessages.length, 'messages, has_more:', data.has_more, 'type:', data.type, 'scope:', data.scope);

          // Scroll to bottom immediately after loading
          setTimeout(() => {
            messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
          }, 50);
        } else {
          const is404 =
            error != null &&
            (String(error).includes('404') || String(error).toLowerCase().includes('not found'));
          if (is404 && onConversationNotFound) {
            onConversationNotFound();
          } else {
            console.error('[Chat] Failed to load conversation:', error);
          }
        }
      } catch (err) {
        console.error('[Chat] Exception loading conversation:', err);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
        if (loadInFlightChatIdRef.current === chatId) {
          loadInFlightChatIdRef.current = null;
        }
      }
    };

    void loadConversation();

    return () => {
      cancelled = true;
      loadInFlightChatIdRef.current = null; // Allow re-run to start load (e.g. Strict Mode)
      setIsLoading(false);
    };
  }, [chatId, userId, setConversationMessages, setConversationTitle, setConversationSummary, setConversationHasMore, onConversationNotFound]);

  // Keep messagesRef in sync for polling comparison (avoids stale closure)
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Poll for updates on workflow conversations (Celery workers can't send WebSocket updates)
  useEffect(() => {
    // Only poll for workflow conversations that haven't finished yet
    if (!chatId || conversationType !== 'workflow' || workflowDoneRef.current) {
      setIsWorkflowPolling(false);
      return;
    }

    console.log('[Chat] Starting polling for workflow conversation');
    setIsWorkflowPolling(true);
    let pollCount = 0;
    const maxPolls = 300; // Poll for up to 10 minutes (300 * 2 seconds)

    const pollInterval = setInterval(async () => {
      pollCount++;
      if (pollCount > maxPolls) {
        console.log('[Chat] Stopping polling - max polls reached');
        setIsWorkflowPolling(false);
        clearInterval(pollInterval);
        return;
      }

      try {
        const { data, error } = await getConversation(chatId);
        if (data && !error) {
          const loadedMessages: ChatMessage[] = data.messages.map((msg) => ({
            id: msg.id,
            role: msg.role as 'user' | 'assistant',
            contentBlocks: msg.content_blocks,
            timestamp: new Date(msg.created_at),
          }));
          
          // Check if content has changed (not just message count)
          // Use ref to get current messages (avoids stale closure)
          const currentContent = JSON.stringify(messagesRef.current.map(m => m.contentBlocks));
          const newContent = JSON.stringify(loadedMessages.map(m => m.contentBlocks));
          
          // Debug: Log tool call status from API response
          for (const msg of loadedMessages) {
            for (const block of msg.contentBlocks || []) {
              if (block.type === 'tool_use') {
                console.log(`[Chat] Poll: tool ${block.name} status=${block.status}, result=`, block.result);
              }
            }
          }
          
          if (newContent !== currentContent) {
            console.log('[Chat] Poll found updated content, updating UI');
            setConversationMessages(chatId, loadedMessages);

            // If any completed tool is write_to_system_of_record, refresh
            // the pending-changes sidebar badge (workflows don't use WS).
            const hasPendingWrite: boolean = loadedMessages.some((m) =>
              (m.contentBlocks || []).some(
                (b) =>
                  b.type === 'tool_use' &&
                  (b as ToolUseBlock).status === 'complete' &&
                  ((b as ToolUseBlock).name === 'write_to_system_of_record' ||
                   (b as ToolUseBlock).name === 'run_sql_write'),
              ),
            );
            if (hasPendingWrite) {
              window.dispatchEvent(new Event('pending-changes-updated'));
            }
          }
          
          // Stop polling when the workflow is truly finished.
          // The agent always ends with a text summary after all tool calls,
          // so we check that (a) there are no running tools AND (b) the last
          // content block is a text block (not a tool_use that might be
          // followed by more tool calls in the next orchestrator turn).
          const lastMsg = loadedMessages[loadedMessages.length - 1];
          const blocks = lastMsg?.contentBlocks || [];
          const lastBlock = blocks[blocks.length - 1];
          const hasRunningTools: boolean = lastMsg?.role === 'assistant' && blocks.some(
            (b) => b.type === 'tool_use' && (b as ToolUseBlock).status !== 'complete'
          );
          const endsWithText: boolean = lastBlock?.type === 'text' && typeof lastBlock.text === 'string' && lastBlock.text.length > 0;
          const workflowDone: boolean = loadedMessages.length >= 2 && lastMsg?.role === 'assistant' && !hasRunningTools && endsWithText;
          if (workflowDone) {
            console.log('[Chat] Stopping polling - workflow complete (ends with text, no running tools)');
            workflowDoneRef.current = true;
            setIsWorkflowPolling(false);
            clearInterval(pollInterval);
          }
        }
      } catch (err) {
        console.error('[Chat] Polling error:', err);
      }
    }, 2000); // Poll every 2 seconds

    return () => {
      console.log('[Chat] Cleaning up workflow polling');
      setIsWorkflowPolling(false);
      clearInterval(pollInterval);
    };
  // Note: messages.length deliberately excluded — polling is self-contained via
  // the interval and stops via workflowDoneRef. Including it caused restarts.
  }, [chatId, userId, conversationType, setConversationMessages]);

  // Track whether user is near the bottom of the scroll container.
  // Only update on user-initiated scrolls (ignore programmatic ones).
  // When user scrolls up, we "lock" the scroll position until they scroll back down.
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleScroll = (): void => {
      if (isProgrammaticScrollRef.current) return;
      const threshold = 100; // px from bottom
      const distanceFromBottom: number = container.scrollHeight - container.scrollTop - container.clientHeight;
      const isNearBottom = distanceFromBottom <= threshold;
      isUserNearBottomRef.current = isNearBottom;
      // Show "scroll to bottom" button when user has scrolled up significantly
      setShowScrollToBottom(!isNearBottom && distanceFromBottom > 200);
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  // Auto-scroll to bottom only if user is near the bottom.
  // This allows users to scroll up and read while the agent is working.
  useEffect(() => {
    // Only auto-scroll if user hasn't scrolled up
    if (!isUserNearBottomRef.current) return;
    
    const container = messagesContainerRef.current;
    if (!container) return;
    
    isProgrammaticScrollRef.current = true;
    container.scrollTop = container.scrollHeight;
    // Use a small delay to ensure the flag is cleared after the scroll event fires
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        isProgrammaticScrollRef.current = false;
      });
    });
  }, [messages, isThinking]);

  // Load earlier messages handler (pagination)
  const handleLoadOlderMessages = useCallback(async (): Promise<void> => {
    if (!chatId || isLoadingOlder || !hasMoreMessages) return;

    const container = messagesContainerRef.current;
    const previousScrollHeight = container?.scrollHeight ?? 0;

    setIsLoadingOlder(true);
    try {
      await fetchOlderMessages(chatId);
    } finally {
      setIsLoadingOlder(false);
    }

    // Preserve scroll position after prepending messages
    if (container) {
      requestAnimationFrame(() => {
        const newScrollHeight = container.scrollHeight;
        isProgrammaticScrollRef.current = true;
        container.scrollTop = newScrollHeight - previousScrollHeight;
        requestAnimationFrame(() => {
          isProgrammaticScrollRef.current = false;
        });
      });
    }
  }, [chatId, isLoadingOlder, hasMoreMessages, fetchOlderMessages]);

  // Scroll to bottom handler (for the button)
  const scrollToBottom = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    
    isProgrammaticScrollRef.current = true;
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    isUserNearBottomRef.current = true;
    setShowScrollToBottom(false);
    // Clear the flag after animation completes
    setTimeout(() => {
      isProgrammaticScrollRef.current = false;
    }, 500);
  }, []);

  const sendChatMessage = useCallback((message: string, source: 'input' | 'suggestion' | 'auto'): void => {
    if ((!message.trim() && pendingAttachments.length === 0) || !isConnected) {
      console.log(`[Chat] sendChatMessage blocked (${source}) - empty or not connected`);
      return;
    }

    console.log(`[Chat] Sending message (${source}):`, message.substring(0, 30) + '...');

    // Build content blocks for local display
    const contentBlocks: ChatMessage['contentBlocks'] = [];
    for (const att of pendingAttachments) {
      contentBlocks.push({
        type: 'attachment',
        filename: att.filename,
        mimeType: att.mime_type,
        size: att.size,
      } satisfies AttachmentBlock);
    }
    contentBlocks.push({ type: 'text', text: message });

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      contentBlocks,
      timestamp: new Date(),
    };

    // Get current conversation ID
    const currentConvId = localConversationId || chatId;

    if (currentConvId) {
      // Add message to existing conversation
      addConversationMessage(currentConvId, userMessage);
      setConversationThinking(currentConvId, true);
      if (crossTab.isAvailable) {
        console.log('[Chat] Broadcasting optimistic message to other tabs', {
          conversationId: currentConvId,
          messageId: userMessage.id,
        });
        crossTab.postMessage({
          kind: 'optimistic_message',
          payload: {
            conversationId: currentConvId,
            message: userMessage,
            setThinking: true,
          },
        });
      }
    } else {
      // New conversation - store in pending state
      pendingTitleRef.current = generateTitle(message);
      setPendingMessages(prev => [...prev, userMessage]);
      setPendingThinking(true);
    }

    // Send message with conversation context, timezone info, and attachment IDs
    const attachmentIds: string[] = pendingAttachments.map((a) => a.upload_id);
    const now = new Date();
    // Build a local ISO-style string (no "Z" suffix) so the backend sees the
    // user's wall-clock time rather than UTC.
    const localIso: string = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    sendMessage({
      type: 'send_message',
      message,
      conversation_id: currentConvId,
      local_time: localIso,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
      // Include scope for new conversations
      ...(!currentConvId ? { scope: newConversationScope } : {}),
    });

    console.log(`[Chat] Sent to WebSocket (${source}) with ${attachmentIds.length} attachment(s)`);
    setInput('');
    setPendingAttachments([]);

    // Reset textarea height to default
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
    }
  }, [
    isConnected,
    sendMessage,
    localConversationId,
    chatId,
    addConversationMessage,
    setConversationThinking,
    pendingAttachments,
    newConversationScope,
  ]);

  // Handle retry: re-send the last user message
  const handleRetry = useCallback(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg?.role === 'user') {
        const text = (msg.contentBlocks ?? [])
          .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
          .map((b) => b.text)
          .join('');
        if (text.trim()) {
          sendChatMessage(text, 'input');
          return;
        }
      }
    }
  }, [messages, sendChatMessage]);

  // Consume pending chat input (from Search "Ask about" button or pipeline deal click)
  useEffect(() => {
    if (!pendingChatInput) {
      if (pendingAutoSendRef.current !== null) {
        console.log('[Chat] Clearing pending auto-send guard');
      }
      pendingAutoSendRef.current = null;
      return;
    }

    if (chatId !== null) {
      return;
    }

    setInput(pendingChatInput);
    console.log('[Chat] Pending chat input received', {
      autoSend: pendingChatAutoSend,
      connected: isConnected,
    });

    if (pendingChatAutoSend) {
      if (pendingAutoSendRef.current === pendingChatInput) {
        console.log('[Chat] Pending chat input already auto-sent, skipping duplicate send');
        return;
      }

      if (isConnected) {
        console.log('[Chat] Auto-sending pending chat input');
        pendingAutoSendRef.current = pendingChatInput;
        sendChatMessage(pendingChatInput, 'auto');
        setPendingChatInput(null);
        setPendingChatAutoSend(false);
      } else {
        console.warn('[Chat] Auto-send requested but socket not connected yet');
      }
      return;
    }

    {
      // Focus the input so user can see the pre-filled text
      setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
    }
    setPendingChatInput(null);
    setPendingChatAutoSend(false);
  }, [
    pendingChatInput,
    pendingChatAutoSend,
    chatId,
    isConnected,
    sendChatMessage,
    setPendingChatInput,
    setPendingChatAutoSend,
  ]);

  const handleSend = useCallback((): void => {
    sendChatMessage(input, 'input');
  }, [input, sendChatMessage]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const files: FileList | null = e.target.files;
    if (!files || files.length === 0) return;

    setIsUploading(true);
    try {
      const uploads: UploadResponse[] = [];
      for (const file of Array.from(files)) {
        const { data, error } = await uploadChatFile(file);
        if (error || !data) {
          console.error(`[Chat] Upload failed for ${file.name}:`, error);
          continue;
        }
        uploads.push(data);
      }
      if (uploads.length > 0) {
        setPendingAttachments((prev) => [...prev, ...uploads]);
      }
    } finally {
      setIsUploading(false);
      // Reset the input so the same file can be re-selected
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }, []);

  const handlePaste = useCallback(async (e: React.ClipboardEvent<HTMLTextAreaElement>): Promise<void> => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: File[] = [];
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }
    }
    if (imageFiles.length === 0) return;

    // Prevent the default paste (would insert garbled text for images)
    e.preventDefault();

    setIsUploading(true);
    try {
      const uploads: UploadResponse[] = [];
      for (const file of imageFiles) {
        const { data, error } = await uploadChatFile(file);
        if (error || !data) {
          console.error(`[Chat] Paste upload failed:`, error);
          continue;
        }
        uploads.push(data);
      }
      if (uploads.length > 0) {
        setPendingAttachments((prev) => [...prev, ...uploads]);
      }
    } finally {
      setIsUploading(false);
    }
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent<HTMLDivElement>): Promise<void> => {
    e.preventDefault();
    setIsDragOver(false);

    const files = Array.from(e.dataTransfer.files).filter(
      (f) => f.type.startsWith('image/') || f.type === 'application/pdf' || f.type.startsWith('text/') || f.name.endsWith('.csv') || f.name.endsWith('.xlsx') || f.name.endsWith('.xls') || f.name.endsWith('.json'),
    );
    if (files.length === 0) return;

    setIsUploading(true);
    try {
      const uploads: UploadResponse[] = [];
      for (const file of files) {
        const { data, error } = await uploadChatFile(file);
        if (error || !data) {
          console.error(`[Chat] Drop upload failed for ${file.name}:`, error);
          continue;
        }
        uploads.push(data);
      }
      if (uploads.length > 0) {
        setPendingAttachments((prev) => [...prev, ...uploads]);
      }
    } finally {
      setIsUploading(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>): void => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>): void => {
    // Only hide if leaving the container (not entering a child)
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragOver(false);
  }, []);

  const removeAttachment = useCallback((uploadId: string): void => {
    setPendingAttachments((prev) => prev.filter((a) => a.upload_id !== uploadId));
  }, []);

  const handleStop = useCallback((): void => {
    if (!activeTaskId) {
      console.log('[Chat] handleStop blocked - no active task');
      return;
    }
    
    console.log('[Chat] Stopping task:', activeTaskId);
    sendMessage({
      type: 'cancel',
      task_id: activeTaskId,
    });
    
    // Clear thinking state immediately for responsiveness
    const currentConvId = localConversationId || chatId;
    if (currentConvId) {
      setConversationThinking(currentConvId, false);
    } else {
      setPendingThinking(false);
    }
  }, [activeTaskId, sendMessage, localConversationId, chatId, setConversationThinking]);

  // Drag handle for resizing preview panel
  const handlePreviewDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const container = dragContainerRef.current;
    if (!container) return;
    const containerRect = container.getBoundingClientRect();
    const startY = e.clientY;
    const startHeight = previewHeight;

    const onMouseMove = (ev: MouseEvent): void => {
      const delta = ev.clientY - startY;
      const maxH = containerRect.height * 0.7;
      const newHeight = Math.min(maxH, Math.max(150, startHeight + delta));
      setPreviewHeight(newHeight);
    };
    const onMouseUp = (): void => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  }, [previewHeight]);

  const handleSuggestionClick = (text: string): void => {
    console.log('[Chat] Suggestion clicked - sending immediately');
    setInput(text);
    inputRef.current?.focus();
    sendChatMessage(text, 'suggestion');
  };

  // Copy conversation to clipboard
  const [copySuccess, setCopySuccess] = useState(false);
  const handleCopyConversation = useCallback(async () => {
    const lines: string[] = [];
    
    for (const msg of messages) {
      const role = msg.role === 'user' ? 'User' : 'Assistant';
      lines.push(`--- ${role} ---`);
      
      for (const block of msg.contentBlocks) {
        if (block.type === 'text') {
          lines.push(block.text);
        } else if (block.type === 'tool_use') {
          lines.push(`[Tool: ${block.name}]`);
          lines.push(`Input: ${JSON.stringify(block.input, null, 2)}`);
          if (block.result) {
            lines.push(`Result: ${JSON.stringify(block.result, null, 2)}`);
          }
          if (block.status) {
            lines.push(`Status: ${block.status}`);
          }
        }
      }
      lines.push('');
    }
    
    const text = lines.join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [messages]);

  // Check if the current user can rename this conversation
  const canRenameHeader = chatId && (
    conversationScope === 'private' || conversationCreatorId === userId
  );

  const startEditingHeaderTitle = useCallback(() => {
    if (!canRenameHeader) return;
    setHeaderTitleDraft(chatTitle);
    setIsEditingHeaderTitle(true);
    setTimeout(() => {
      headerTitleInputRef.current?.focus();
      headerTitleInputRef.current?.select();
    }, 0);
  }, [canRenameHeader, chatTitle]);

  const saveHeaderTitle = useCallback(async () => {
    setIsEditingHeaderTitle(false);
    const trimmed = headerTitleDraft.trim();
    if (!trimmed || !chatId || trimmed === chatTitle) return;
    setConversationTitle(chatId, trimmed);
    const { error } = await updateConversation(chatId, trimmed);
    if (error) {
      setConversationTitle(chatId, chatTitle);
    }
  }, [headerTitleDraft, chatId, chatTitle, setConversationTitle]);

  const cancelEditingHeaderTitle = useCallback(() => {
    setIsEditingHeaderTitle(false);
  }, []);

  // Convert private conversation to shared
  const handleMakeShared = useCallback(async () => {
    if (!chatId) return;

    try {
      const { data, error } = await apiRequest<{ scope: string; participants: Array<{ id: string; name: string | null; email: string; avatar_url?: string | null }> }>(
        `/chat/conversations/${chatId}/scope`,
        { method: 'PATCH', body: JSON.stringify({ scope: 'shared' }) },
      );

      if (error || !data) {
        console.error('Failed to make shared:', error);
        return;
      }

      setConversationScope('shared');
      useAppStore.getState().setChatScope(chatId, 'shared');
      setConversationParticipants(
        (data.participants ?? []).map((p) => ({
          id: p.id,
          name: p.name,
          email: p.email,
          avatarUrl: p.avatar_url,
        }))
      );
    } catch (err) {
      console.error('Failed to make shared:', err);
    }
  }, [chatId]);

  // Convert shared conversation to private (creator only)
  const handleMakePrivate = useCallback(async () => {
    if (!chatId) return;

    try {
      const { error } = await apiRequest(
        `/chat/conversations/${chatId}/scope`,
        { method: 'PATCH', body: JSON.stringify({ scope: 'private' }) },
      );

      if (error) {
        console.error('Failed to make private:', error);
        return;
      }

      setConversationScope('private');
      useAppStore.getState().setChatScope(chatId, 'private');
      setConversationParticipants([]);
    } catch (err) {
      console.error('Failed to make private:', err);
    }
  }, [chatId]);

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center min-h-0 overflow-hidden">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-surface-400">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Header - hidden on mobile since AppLayout has mobile header */}
      <header className="hidden md:flex h-14 border-b border-surface-800 items-center justify-between px-4 md:px-6 flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          {isEditingHeaderTitle ? (
            <input
              ref={headerTitleInputRef}
              type="text"
              value={headerTitleDraft}
              onChange={(e) => setHeaderTitleDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveHeaderTitle();
                if (e.key === 'Escape') cancelEditingHeaderTitle();
              }}
              onBlur={() => void saveHeaderTitle()}
              className="text-lg font-semibold text-surface-100 bg-transparent border-b border-primary-500 outline-none max-w-[200px] md:max-w-md"
              maxLength={100}
            />
          ) : (
            <div
              className={`flex items-center gap-1.5 group/title min-w-0 ${canRenameHeader ? 'cursor-pointer' : ''}`}
              onClick={canRenameHeader ? startEditingHeaderTitle : undefined}
              title={canRenameHeader ? 'Click to rename' : undefined}
            >
              <h1 className="text-lg font-semibold text-surface-100 truncate max-w-[200px] md:max-w-md">
                {chatTitle}
              </h1>
              {canRenameHeader && (
                <svg className="w-3.5 h-3.5 text-surface-500 opacity-0 group-hover/title:opacity-100 transition-opacity flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              )}
            </div>
          )}
          {/* Scope badge / toggle */}
          {chatId && (() => {
            const canToggleScope = conversationScope === 'private' || conversationCreatorId === userId;
            if (canToggleScope) {
              return (
                <button
                  onClick={() => void (conversationScope === 'private' ? handleMakeShared() : handleMakePrivate())}
                  className={`px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide rounded transition-colors cursor-pointer ${
                    conversationScope === 'shared'
                      ? 'bg-primary-500/20 text-primary-400 hover:bg-primary-500/30'
                      : 'bg-surface-700 text-surface-400 hover:bg-surface-600'
                  }`}
                  title={conversationScope === 'shared' ? 'Click to make private' : 'Click to share with team'}
                >
                  {conversationScope}
                </button>
              );
            }
            return (
              <span className={`px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide rounded ${
                conversationScope === 'shared'
                  ? 'bg-primary-500/20 text-primary-400'
                  : 'bg-surface-700 text-surface-400'
              }`}>
                {conversationScope}
              </span>
            );
          })()}
          {/* Uncommitted changes indicator */}
          {hasUncommittedChanges && (
            <span 
              className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-yellow-500/20 text-yellow-400 cursor-pointer hover:bg-yellow-500/30 transition-colors"
              title="This conversation has uncommitted changes. Click to review."
              onClick={() => {
                const setCurrentView = useAppStore.getState().setCurrentView;
                setCurrentView('pending-changes');
              }}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400" />
              Changes
            </span>
          )}
          {messages.length > 0 && (
            <button
              onClick={() => void handleCopyConversation()}
              className="p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
              title="Copy conversation"
            >
              {copySuccess ? (
                <svg className="w-4 h-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              )}
            </button>
          )}
          {(() => {
            const contextPct = (conversationState?.contextTokens ?? 0) / 200_000;
            return conversationState?.contextTokens != null ? (
              <div className="flex items-center gap-1.5 ml-2" title={`${Math.round(contextPct * 100)}% context used (${(conversationState.contextTokens / 1000).toFixed(0)}k / 200k tokens)`}>
                <div className="w-16 h-1.5 bg-surface-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      contextPct > 0.85 ? 'bg-red-400' :
                      contextPct > 0.6 ? 'bg-yellow-400' :
                      'bg-primary-400'
                    }`}
                    style={{ width: `${Math.min(contextPct * 100, 100)}%` }}
                  />
                </div>
                <span className={`text-[10px] tabular-nums ${
                  contextPct > 0.85 ? 'text-red-400' :
                  contextPct > 0.6 ? 'text-yellow-400' :
                  'text-surface-500'
                }`}>
                  {Math.round(contextPct * 100)}%
                </span>
              </div>
            ) : null;
          })()}
        </div>
        <div className="flex items-center gap-3">
          {/* Participant avatars for shared conversations */}
          {conversationScope === 'shared' && conversationParticipants.length > 0 && (
            <div className="flex items-center gap-2">
              <div className="flex -space-x-2">
                {conversationParticipants.slice(0, 4).map((p, idx) => (
                  <Avatar
                    key={p.id}
                    user={p}
                    size="sm"
                    bordered
                    className="border-2 border-surface-900"
                    style={{ zIndex: 4 - idx }}
                  />
                ))}
                {conversationParticipants.length > 4 && (
                  <div
                    className="w-6 h-6 rounded-full border-2 border-surface-900 bg-surface-700 flex items-center justify-center text-xs font-medium text-surface-300"
                    title={`${conversationParticipants.length - 4} more participants`}
                  >
                    +{conversationParticipants.length - 4}
                  </div>
                )}
              </div>
              {/* Invite button */}
              <button
                onClick={() => setShowInviteModal(true)}
                className="p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
                title="Invite teammate"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
                </svg>
              </button>
            </div>
          )}
          <ConnectionStatus state={connectionState} />
        </div>
      </header>

      {/* Content area with messages and optional artifact sidebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* Messages column (vertical flex with optional app preview above) */}
        <div ref={dragContainerRef} className={`flex flex-col md:transition-all md:duration-300 md:ease-in-out ${currentArtifact ? 'md:w-1/2' : ''} flex-1 min-w-0 min-h-0`}>
          {/* App preview panel (above messages) */}
          {conversationApps.length > 0 && !previewDismissed && (
            <>
              <AppPreviewPanel
                apps={conversationApps}
                activeAppId={previewAppId}
                onActiveAppChange={setPreviewAppId}
                collapsed={previewCollapsed}
                onCollapsedChange={setPreviewCollapsed}
                onClose={() => setPreviewDismissed(true)}
                onAppError={(errorMsg: string) => {
                  const activeApp = conversationApps.find((a) => a.id === previewAppId) ?? conversationApps[conversationApps.length - 1];
                  if (activeApp) {
                    const fixPrompt = `The app "${activeApp.title}" has a compile/runtime error. Please fix it and create an updated version.\n\nError:\n\`\`\`\n${errorMsg}\n\`\`\``;
                    sendChatMessage(fixPrompt, 'input');
                  }
                }}
                height={previewHeight}
              />
              {/* Drag handle for resizing */}
              {!previewCollapsed && (
                <div
                  className="h-1 flex-shrink-0 cursor-row-resize bg-surface-800 hover:bg-primary-600 transition-colors group flex items-center justify-center"
                  onMouseDown={handlePreviewDragStart}
                >
                  <div className="w-8 h-0.5 rounded-full bg-surface-600 group-hover:bg-primary-400 transition-colors" />
                </div>
              )}
            </>
          )}

          {/* Messages scroll area */}
          <div className="relative flex-1 min-h-0">
            <div ref={messagesContainerRef} className="absolute inset-0 overflow-y-auto overflow-x-hidden p-3 md:p-6">
            {conversationState?.summary && <SummaryCard summary={conversationState.summary} />}
            {!userId && (
              <div className="mb-3 rounded-lg border border-amber-600/50 bg-amber-900/20 px-3 py-2 text-sm text-amber-200">
                User context is missing — artifacts and apps may not save correctly. Please refresh or re-sign in.
              </div>
            )}
            {messages.length === 0 && !isThinking ? (
              conversationType === 'workflow' ? (
                // Show loading state for workflow conversations waiting for agent to start
                <div className="flex-1 flex flex-col items-center justify-center py-20">
                  <div className="relative mb-6">
                    {/* Spinning ring */}
                    <div className="w-16 h-16 rounded-full border-4 border-surface-700 border-t-primary-500 animate-spin" />
                    {/* Center icon */}
                    <div className="absolute inset-0 flex items-center justify-center">
                      <svg className="w-6 h-6 text-primary-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    </div>
                  </div>
                  <h3 className="text-lg font-medium text-surface-200 mb-2">Running Workflow</h3>
                  <p className="text-surface-400 text-center max-w-md">
                    The agent is processing your workflow. Results will appear here momentarily...
                  </p>
                </div>
              ) : (
                <EmptyState onSuggestionClick={handleSuggestionClick} />
              )
            ) : (
              <div className="max-w-3xl mx-auto space-y-3">
                {hasMoreMessages && (
                  <div className="flex justify-center py-2">
                    <button
                      type="button"
                      onClick={() => void handleLoadOlderMessages()}
                      disabled={isLoadingOlder}
                      className="text-xs text-surface-400 hover:text-surface-200 transition-colors disabled:opacity-50"
                    >
                      {isLoadingOlder ? 'Loading...' : 'Load earlier messages'}
                    </button>
                  </div>
                )}
                {messages.map((msg) => (
                  <MessageWithBlocks
                    key={msg.id}
                    message={msg}
                    toolApprovals={toolApprovals}
                    onArtifactClick={setCurrentArtifact}
                    onAppClick={(app: AppBlock["app"]) => { setPreviewAppId(app.id); setPreviewCollapsed(false); setPreviewDismissed(false); setCurrentArtifact(null); }}
                    onToolApprove={handleToolApprove}
                    onToolCancel={handleToolCancel}
                    onToolClick={(block) => setSelectedToolCall({
                      toolName: block.name,
                      toolId: block.id,
                      input: block.input,
                      result: block.result,
                      status: block.status === 'complete' ? 'complete' : 'running',
                    })}
                    onRetry={handleRetry}
                    conversationScope={conversationScope}
                    currentUserId={userId}
                  />
                ))}

                {/* Thinking indicator */}
                {isThinking && <ThinkingIndicator />}

                {/* Workflow polling spinner - shows at bottom while workflow is running */}
                {isWorkflowPolling && messages.length > 0 && !isThinking && (
                  <div className="flex items-center justify-center gap-2 py-4 text-surface-400">
                    <div className="w-4 h-4 border-2 border-surface-600 border-t-primary-500 rounded-full animate-spin" />
                    <span className="text-sm">Workflow running...</span>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>
            )}
            </div>

            {/* Scroll to bottom button */}
            {showScrollToBottom && (
              <button
                onClick={scrollToBottom}
                className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface-800 border border-surface-700 text-surface-300 hover:text-surface-100 hover:bg-surface-700 shadow-lg transition-all text-xs font-medium z-10"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                </svg>
                Scroll to bottom
              </button>
            )}
          </div>
        </div>

        {/* Artifact sidebar - overlay on mobile, sidebar on desktop */}
        {currentArtifact && (
          <>
            {/* Mobile backdrop */}
            <div
              className="fixed inset-0 bg-black/50 z-40 md:hidden animate-fade-in"
              onClick={() => setCurrentArtifact(null)}
            />
            <div className="fixed inset-y-0 right-0 w-full max-w-md z-50 animate-slide-in-right md:relative md:w-1/2 md:z-auto md:animate-none md:transition-all md:duration-300 md:ease-in-out border-l border-surface-800 bg-surface-900 p-4 overflow-y-auto">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-lg font-semibold text-surface-100 truncate">
                  {currentArtifact.title}
                </h2>
                <button
                  onClick={() => setCurrentArtifact(null)}
                  className="text-surface-400 hover:text-surface-200 p-1 -mr-1"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <ArtifactViewer artifact={currentArtifact} />
            </div>
          </>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-surface-800 p-2 md:p-3">
        <div className="max-w-3xl mx-auto">
          {/* Credits warnings */}
          {outOfCredits && (
            <div className="mb-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm flex items-center gap-2">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              You&apos;re out of credits. Upgrade your plan to continue chatting.
            </div>
          )}
          {lowCredits && (
            <div className="mb-2 px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/30 text-yellow-300 text-sm flex items-center gap-2">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              Running low on credits ({creditsInfo?.balance} remaining).
            </div>
          )}
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            accept="image/*,.pdf,.csv,.xlsx,.xls,.txt,.json,.md,.xml,.html"
            onChange={handleFileSelect}
          />

          {/* Single container that looks like one input box */}
          <div
            onDrop={(e) => void handleDrop(e)}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            className={`relative rounded-2xl border bg-surface-900 transition-all duration-150 ${
              isDragOver
                ? 'border-primary-500 ring-2 ring-primary-500/40'
                : (!isConnected || agentRunning || outOfCredits) ? 'border-surface-700 opacity-50' : 'border-surface-700 focus-within:ring-2 focus-within:ring-primary-500 focus-within:border-transparent'
            }`}
          >
            {/* Drop zone overlay */}
            {isDragOver && (
              <div className="absolute inset-0 rounded-2xl bg-primary-500/10 flex items-center justify-center z-10 pointer-events-none">
                <span className="text-sm font-medium text-primary-400">Drop files here</span>
              </div>
            )}

            {/* Attachment cards */}
            {pendingAttachments.length > 0 && (
              <div className="flex flex-wrap gap-2 px-3 pt-3">
                {pendingAttachments.map((att) => (
                  <AttachmentCard
                    key={att.upload_id}
                    filename={att.filename}
                    mimeType={att.mime_type}
                    size={att.size}
                    onRemove={() => removeAttachment(att.upload_id)}
                  />
                ))}
              </div>
            )}

            {/* Textarea — no border/bg of its own */}
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                // Auto-resize textarea
                e.target.style.height = 'auto';
                e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`;
              }}
              onKeyDown={handleKeyDown}
              onPaste={(e) => void handlePaste(e)}
              placeholder={outOfCredits ? 'Out of credits — upgrade to continue' : agentRunning ? 'Agent working...' : 'Ask about your pipeline...'}
              className="w-full resize-none bg-transparent text-surface-100 px-4 pt-3 pb-1 text-sm placeholder-surface-500 focus:outline-none leading-5 scrollbar-none disabled:cursor-not-allowed"
              style={{ minHeight: '36px', maxHeight: '240px' }}
              rows={1}
              disabled={!isConnected || agentRunning || outOfCredits}
              autoFocus={chatId === null}
            />

            {/* Bottom row: attach on left, scope toggle (for new chats), send/stop on right */}
            <div className="flex items-center justify-between px-2 pb-2">
              <div className="flex items-center gap-2">
                {/* Attach button */}
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isUploading || agentRunning}
                  className="flex w-8 h-8 rounded-full text-surface-400 hover:text-surface-200 hover:bg-surface-800 items-center justify-center transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="Attach file"
                >
                  {isUploading ? (
                    <div className="w-4 h-4 border-2 border-surface-600 border-t-primary-500 rounded-full animate-spin" />
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                  )}
                </button>

                {/* Scope toggle - only for new conversations */}
                {!chatId && !localConversationId && (
                  <button
                    type="button"
                    onClick={() => setNewConversationScope(prev => prev === 'shared' ? 'private' : 'shared')}
                    className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium transition-colors ${
                      newConversationScope === 'shared'
                        ? 'bg-primary-500/20 text-primary-400 hover:bg-primary-500/30'
                        : 'bg-surface-700 text-surface-400 hover:bg-surface-600'
                    }`}
                    title={newConversationScope === 'shared' 
                      ? 'Shared: Teammates can join this conversation' 
                      : 'Private: Only you can see this conversation'}
                  >
                    {newConversationScope === 'shared' ? (
                      <>
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                        </svg>
                        Shared
                      </>
                    ) : (
                      <>
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                        Private
                      </>
                    )}
                  </button>
                )}
              </div>

              {/* Send/Stop button */}
              {agentRunning ? (
                <button
                  onClick={handleStop}
                  className="flex-shrink-0 w-8 h-8 rounded-lg bg-red-600 text-white hover:bg-red-500 flex items-center justify-center transition-colors"
                  title="Stop"
                >
                  <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="6" width="12" height="12" rx="1" />
                  </svg>
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={(!input.trim() && pendingAttachments.length === 0) || !isConnected || outOfCredits}
                  className="flex-shrink-0 w-8 h-8 rounded-lg bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 10l7-7m0 0l7 7m-7-7v18" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Tool Call Detail Modal */}
      {selectedToolCall && (
        <ToolCallModal 
          toolCall={selectedToolCall} 
          onClose={() => setSelectedToolCall(null)} 
        />
      )}

      {/* Invite Participant Modal */}
      {showInviteModal && chatId && (
        <InviteParticipantModal
          conversationId={chatId}
          teamMembers={teamMembersData?.members ?? []}
          existingParticipantIds={new Set(conversationParticipants.map((p) => p.id))}
          onClose={() => setShowInviteModal(false)}
          onParticipantAdded={(participant) => {
            setConversationParticipants((prev) => [...prev, participant]);
            setShowInviteModal(false);
          }}
        />
      )}
    </div>
  );
}

/**
 * Modal for inviting participants to a shared conversation
 */
function InviteParticipantModal({
  conversationId,
  teamMembers,
  existingParticipantIds,
  onClose,
  onParticipantAdded,
}: {
  conversationId: string;
  teamMembers: Array<{ id: string; name: string | null; email: string; avatarUrl: string | null }>;
  existingParticipantIds: Set<string>;
  onClose: () => void;
  onParticipantAdded: (participant: { id: string; name: string | null; email: string; avatarUrl?: string | null }) => void;
}): JSX.Element {
  const [email, setEmail] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const emailSuggestions = useMemo(() => {
    const query = email.trim().toLowerCase();
    return teamMembers
      .filter((member) => !existingParticipantIds.has(member.id))
      .filter((member) => {
        if (!query) return true;
        const displayName = (member.name ?? '').toLowerCase();
        return member.email.toLowerCase().includes(query) || displayName.includes(query);
      })
      .slice(0, 6);
  }, [email, existingParticipantIds, teamMembers]);

  const handleInvite = async (): Promise<void> => {
    if (!email.trim()) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const { data, error: inviteError } = await apiRequest<{ participant: { id: string; name: string | null; email: string; avatar_url?: string | null } }>(`/chat/conversations/${conversationId}/participants`, {
        method: 'POST',
        body: JSON.stringify({ email: email.trim() }),
      });

      if (inviteError || !data?.participant) {
        throw new Error(inviteError || 'Failed to add participant');
      }

      onParticipantAdded({
        id: data.participant.id,
        name: data.participant.name,
        email: data.participant.email,
        avatarUrl: data.participant.avatar_url,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add participant');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface-900 rounded-xl border border-surface-700 shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700">
          <h3 className="text-lg font-semibold text-surface-100">Invite Teammate</h3>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="p-4">
          <label className="block text-sm font-medium text-surface-300 mb-2">
            Email address
          </label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="teammate@company.com"
            className="w-full px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isLoading) {
                void handleInvite();
              }
            }}
          />
          {emailSuggestions.length > 0 && (
            <div className="mt-2 max-h-44 overflow-y-auto rounded-lg border border-surface-700 bg-surface-850">
              {emailSuggestions.map((member) => (
                <button
                  key={member.id}
                  type="button"
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-surface-800"
                  onClick={() => {
                    setEmail(member.email);
                    setError(null);
                  }}
                >
                  <span className="truncate text-surface-200">{member.name?.trim() || member.email}</span>
                  <span className="ml-3 truncate text-xs text-surface-400">{member.email}</span>
                </button>
              ))}
            </div>
          )}
          {error && (
            <p className="mt-2 text-sm text-red-400">{error}</p>
          )}
          <p className="mt-2 text-xs text-surface-500">
            The user must be a member of your team.
          </p>
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-surface-700">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-surface-100 hover:bg-surface-800 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleInvite()}
            disabled={!email.trim() || isLoading}
            className="px-4 py-2 text-sm font-medium bg-primary-600 text-white rounded-lg hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? 'Adding...' : 'Add to Conversation'}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Render a message with its content blocks (text + tool_use)
 */
function MessageWithBlocks({
  message,
  toolApprovals,
  onArtifactClick,
  onAppClick,
  onToolApprove,
  onToolCancel,
  onToolClick,
  onRetry,
  conversationScope,
  currentUserId,
}: {
  message: ChatMessage;
  toolApprovals: Map<string, { operationId: string; toolName: string; isProcessing: boolean; result: unknown }>;
  onArtifactClick: (artifact: AnyArtifact) => void;
  onAppClick: (app: AppBlock["app"]) => void;
  onToolApprove: (operationId: string, options?: Record<string, unknown>) => void;
  onToolCancel: (operationId: string) => void;
  onToolClick: (block: ToolUseBlock) => void;
  onRetry?: () => void;
  conversationScope: 'private' | 'shared';
  currentUserId?: string | null;
}): JSX.Element {
  const blocks = message.contentBlocks ?? [];
  const isUser = message.role === 'user';
  
  if (blocks.length === 0) {
    console.warn('[MessageWithBlocks] Empty contentBlocks for message:', message.id, message.role);
    return <></>;
  }

  // For user messages, use the simple Message component (with attachment cards if any)
  if (isUser) {
    const textContent = blocks
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('');
    const attachments = blocks.filter(
      (b): b is AttachmentBlock => b.type === 'attachment',
    );
    
    // In shared conversations, check if this is from the current user or another participant
    const isOwnMessage = !message.userId || message.userId === currentUserId;
    const showSenderInfo = conversationScope === 'shared' && !isOwnMessage;
    
    // For other users' messages in shared conversations, show on the left like assistant
    if (showSenderInfo) {
      const senderName = message.senderName ?? message.senderEmail ?? 'Unknown';
      const senderUser = {
        id: message.userId ?? 'unknown',
        name: message.senderName,
        email: message.senderEmail,
        avatarUrl: message.senderAvatarUrl,
      };
      
      return (
        <div className="flex gap-2 animate-slide-up">
          {/* Avatar */}
          <Avatar user={senderUser} size="sm" className="flex-shrink-0 rounded-md" />

          {/* Content */}
          <div className="flex-1 max-w-[85%] overflow-hidden">
            <div className="text-xs text-surface-400 mb-0.5">{senderName}</div>
            <div className="inline-block max-w-full px-3 py-2 rounded-xl rounded-tl-sm bg-surface-700 text-surface-100 text-[13px] leading-relaxed">
              <div className="whitespace-pre-wrap break-words">{textContent}</div>
            </div>
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-1.5">
                {attachments.map((att, i) => (
                  <AttachmentCard key={`att-${i}`} filename={att.filename} mimeType={att.mimeType} size={att.size} />
                ))}
              </div>
            )}
            <div className="mt-0.5">
              <span className="text-[10px] text-surface-500">
                {message.timestamp.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
              </span>
            </div>
          </div>
        </div>
      );
    }
    
    // Own messages (or private conversation) - right-aligned
    return (
      <div className="flex gap-2 flex-row-reverse animate-slide-up">
        {/* Avatar */}
        <div className="flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center bg-primary-600">
          <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
        </div>

        {/* Content: bubble + attachments + timestamp, all right-aligned */}
        <div className="flex-1 max-w-[85%] overflow-hidden text-right">
          <div className="inline-block max-w-full px-3 py-2 rounded-xl rounded-tr-sm bg-primary-600 text-white text-[13px] leading-relaxed">
            <div className="whitespace-pre-wrap break-words text-left">{textContent}</div>
          </div>
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 justify-end mt-1.5">
              {attachments.map((att, i) => (
                <AttachmentCard key={`att-${i}`} filename={att.filename} mimeType={att.mimeType} size={att.size} />
              ))}
            </div>
          )}
          <div className="mt-0.5">
            <span className="text-[10px] text-surface-500">
              {message.timestamp.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
            </span>
          </div>
        </div>
      </div>
    );
  }

  // For assistant messages, render blocks in order (interleaved)
  // Find the last text block index for timestamp placement
  const lastTextIndex = blocks.reduce((lastIdx, block, idx) => 
    block.type === 'text' ? idx : lastIdx, -1);

  const renderToolBlock = (block: ToolUseBlock): JSX.Element => {
    // Check if this is a pending_approval response from any tool
    const result = block.result as Record<string, unknown> | undefined;
    const isPendingApproval = result?.type === 'pending_approval' || result?.status === 'pending_approval';
    
    if (isPendingApproval && result) {
      const operationId = result.operation_id as string;
      const toolName = (result.tool_name as string) || block.name;
      const approvalState = toolApprovals.get(operationId);
      
      // Check if we have a final result stored (completed/failed/canceled)
      const storedStatus = result?.status as string | undefined;
      const isFinalState = storedStatus && ['completed', 'failed', 'canceled', 'expired'].includes(storedStatus);
      
      const finalResult = isFinalState
        ? (result as unknown as ApprovalResult)
        : (approvalState?.result as ApprovalResult | null) ?? null;

      return (
        <div key={block.id} className="my-1">
          <PendingApprovalCard
            data={{
              type: 'pending_approval',
              status: (result.status as string) ?? 'pending_approval',
              operation_id: operationId,
              tool_name: toolName,
              preview: (result.preview as Record<string, unknown>) ?? {},
              message: (result.message as string) ?? '',
              target_system: result.target_system as string | undefined,
              record_type: result.record_type as string | undefined,
              operation: result.operation as string | undefined,
            }}
            onApprove={onToolApprove}
            onCancel={onToolCancel}
            isProcessing={approvalState?.isProcessing ?? false}
            result={finalResult}
          />
        </div>
      );
    }

    return (
      <ToolBlockIndicator
        key={block.id}
        block={block}
        onClick={() => onToolClick(block)}
      />
    );
  };

  return (
    <div className="flex gap-2">
      {/* Avatar */}
      <div className="flex-shrink-0 w-6 h-6 rounded-md bg-surface-800 flex items-center justify-center">
        <img 
          src={LOGO_PATH} 
          alt={APP_NAME} 
          className="w-3.5 h-3.5" 
        />
      </div>

      {/* Content blocks in order */}
      <div className="flex-1 max-w-[85%] overflow-hidden">
        {blocks.map((block, index) => {
          if (block.type === 'text') {
            const isLast = index === lastTextIndex;
            return (
              <div key={`text-${index}`} className={index > 0 ? 'mt-2' : ''}>
                <AssistantTextBlock 
                  text={block.text} 
                  isStreaming={isLast && message.isStreaming}
                />
              </div>
            );
          }
          if (block.type === 'tool_use') {
            // Hide tool blocks that haven't started yet (no status, no result).
            // The orchestrator saves all tool_use blocks from Claude's response
            // in one early save before executing them sequentially, so without
            // this check they'd all show as "running" simultaneously.
            const toolBlock = block as ToolUseBlock;
            if (!toolBlock.status && !toolBlock.result) {
              return null;
            }
            return (
              <div key={block.id} className="my-0.5">
                {renderToolBlock(toolBlock)}
              </div>
            );
          }
          if (block.type === 'error') {
            return (
              <div key={`error-${index}`} className="my-0.5">
                <ErrorBlockIndicator block={block} onRetry={onRetry} />
              </div>
            );
          }
          if (block.type === 'artifact') {
            return (
              <div key={`artifact-${block.artifact.id}`} className="my-2">
                <ArtifactTile
                  artifact={block.artifact}
                  onClick={() => onArtifactClick(block.artifact)}
                />
              </div>
            );
          }
          if (block.type === 'app') {
            return (
              <div key={`app-${block.app.id}`} className="my-2">
                <AppTile
                  app={block.app}
                  onClick={() => onAppClick(block.app)}
                />
              </div>
            );
          }
          return null;
        })}
        
        {/* Timestamp at the end */}
        <div className="mt-0.5">
          <span className="text-[10px] text-surface-500">
            {message.timestamp.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
          </span>
        </div>
      </div>
    </div>
  );
}

/**
 * Assistant text block - renders markdown without avatar (avatar is at parent level)
 */
function AssistantTextBlock({
  text,
  isStreaming,
}: {
  text: string;
  isStreaming?: boolean;
}): JSX.Element {
  // Trim trailing whitespace when streaming to prevent cursor appearing on empty line
  const displayText: string = isStreaming ? text.trimEnd() : text;
  
  return (
    <div className="inline-block max-w-full px-3 py-2 rounded-xl rounded-tl-sm bg-surface-800/80 text-surface-200 text-[13px] leading-relaxed">
      <div className={`prose prose-sm prose-invert max-w-none overflow-x-auto prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-pre:my-2 prose-code:text-primary-300 prose-code:bg-surface-900/50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-surface-900/80 prose-pre:text-xs prose-table:text-xs prose-th:bg-surface-700/50 prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1 prose-td:border-surface-700 prose-th:border-surface-700 ${isStreaming ? '[&>p:last-of-type]:inline [&>p:last-of-type]:mb-0' : ''}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayText}</ReactMarkdown>
        {isStreaming && (
          <span className="inline-block w-1.5 h-3 bg-current animate-pulse ml-0.5 align-middle" />
        )}
      </div>
    </div>
  );
}

/**
 * Tool block indicator - clickable to show details
 */
function ToolBlockIndicator({
  block,
  onClick,
}: {
  block: ToolUseBlock;
  onClick: () => void;
}): JSX.Element {
  const isComplete = block.status === 'complete';
  const statusText = getToolStatusText(block.name, block.input, isComplete, block.result);

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 py-0.5 text-xs text-surface-500 hover:text-surface-300 transition-colors cursor-pointer group text-left"
    >
      {isComplete ? (
        <svg className="w-3.5 h-3.5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5 text-surface-600 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      )}
      <span className="text-surface-500 italic group-hover:text-surface-300">{statusText}</span>
      <svg className="w-3 h-3 text-surface-600 opacity-0 group-hover:opacity-100 transition-opacity" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    </button>
  );
}

/**
 * Error block indicator - shows errors in a compact, non-intrusive style
 */
function ErrorBlockIndicator({
  block,
  onRetry,
}: {
  block: ErrorBlock;
  onRetry?: () => void;
}): JSX.Element {
  // Parse the error message to extract a user-friendly summary
  const errorSummary = getErrorSummary(block.message);

  return (
    <div className="flex items-center gap-1.5 py-0.5 text-xs text-red-400/80">
      <svg className="w-3.5 h-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
      <span className="italic">{errorSummary}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-red-400 hover:text-red-300 underline ml-1"
        >
          Retry
        </button>
      )}
    </div>
  );
}

/**
 * Extract a user-friendly error summary from error messages
 */
function getErrorSummary(errorMessage: string): string {
  // Check for common error patterns
  if (errorMessage.includes('overloaded_error') || errorMessage.includes('Overloaded')) {
    return 'Service temporarily unavailable. Please try again.';
  }
  if (errorMessage.includes('rate_limit')) {
    return 'Rate limit reached. Please wait a moment and try again.';
  }
  if (
    errorMessage.includes('prompt is too long') ||
    errorMessage.includes('context window') ||
    (errorMessage.includes('exceeds') && errorMessage.includes('context'))
  ) {
    return 'This conversation is too long. Please start a new conversation.';
  }
  if (errorMessage.includes('timeout') || errorMessage.includes('Timeout')) {
    return 'Request timed out. Please try again.';
  }
  if (errorMessage.includes('connection') || errorMessage.includes('network')) {
    return 'Connection error. Please check your network and try again.';
  }
  
  // For other errors, truncate if too long
  const maxLength = 80;
  if (errorMessage.length > maxLength) {
    return errorMessage.slice(0, maxLength) + '...';
  }
  
  return errorMessage || 'An error occurred. Please try again.';
}

/**
 * Generate user-friendly status text for tool calls
 */
function getToolStatusText(
  toolName: string, 
  input: Record<string, unknown> | undefined, 
  isComplete: boolean,
  result: Record<string, unknown> | undefined
): string {
  switch (toolName) {
    case 'web_search': {
      const query = typeof input?.query === 'string' ? input.query : '';
      const truncatedQuery = query.length > 40 ? query.slice(0, 40) + '...' : query;
      if (isComplete) {
        const sources = Array.isArray(result?.sources) ? result.sources.length : 0;
        const sourceText = sources > 0 ? ` (${sources} source${sources === 1 ? '' : 's'})` : '';
        return `Searched the web for '${truncatedQuery}'${sourceText}`;
      }
      return `Searching the web for '${truncatedQuery}'...`;
    }
    case 'run_sql_query': {
      // Extract table names from the SQL query for a more descriptive message
      const query = typeof input?.query === 'string' ? input.query.toLowerCase() : '';
      const tableNames: string[] = [];
      const knownTables = [
        'deals', 'accounts', 'contacts', 'activities', 'integrations', 
        'users', 'organizations', 'pipelines', 'pipeline_stages'
      ];
      for (const table of knownTables) {
        if (query.includes(table)) {
          tableNames.push(table === 'pipeline_stages' ? 'stages' : table);
        }
      }
      const tableDesc = tableNames.length > 0 
        ? tableNames.join(' and ') 
        : 'synced data';
      
      if (isComplete) {
        const rowCount = typeof result?.row_count === 'number' ? result.row_count : 0;
        return `Queried ${tableDesc} (${rowCount} row${rowCount === 1 ? '' : 's'})`;
      }
      return `Querying ${tableDesc}...`;
    }
    case 'create_artifact': {
      const artifactTitle = typeof input?.title === 'string' ? input.title : 'artifact';
      const artifactType = typeof input?.content_type === 'string' ? input.content_type : 'file';
      if (isComplete) {
        return `Created ${artifactType}: ${artifactTitle}`;
      }
      // Show progress message from result if available
      const progressMsg = typeof result?.message === 'string' ? result.message : null;
      const charsProcessed = typeof result?.chars_processed === 'number' ? result.chars_processed : 0;
      const totalChars = typeof result?.total_chars === 'number' ? result.total_chars : 0;
      if (progressMsg && totalChars > 0) {
        const progress = Math.round((charsProcessed / totalChars) * 100);
        return `${progressMsg} (${progress}%)`;
      }
      return progressMsg || `Creating ${artifactType}...`;
    }
    case 'write_to_system_of_record': {
      const targetSystem = typeof input?.target_system === 'string' ? input.target_system : '';
      const recordType = typeof input?.record_type === 'string' ? input.record_type : 'record';
      const recordCount = Array.isArray(input?.records) ? input.records.length : 0;
      const systemLabel = targetSystem || 'system';
      if (recordCount === 0) {
        return `Preparing ${recordType}s for ${systemLabel}...`;
      }
      const pluralType = recordCount === 1 ? recordType : `${recordType}s`;
      const DIRECT_WRITE_THRESHOLD = 5;
      if (isComplete) {
        const verb = typeof input?.operation === 'string' && input.operation === 'update' ? 'Updated' : 'Created';
        return recordCount > DIRECT_WRITE_THRESHOLD
          ? `Prepared ${recordCount} ${pluralType} for review`
          : `${verb} ${recordCount} ${pluralType} in ${systemLabel}`;
      }
      return `Writing ${recordCount} ${pluralType} to ${systemLabel}...`;
    }
    case 'foreach': {
      const opName: string = typeof result?.operation_name === 'string'
        ? result.operation_name
        : (typeof result?.workflow_name === 'string'
          ? result.workflow_name
          : (typeof input?.operation_name === 'string' ? input.operation_name : 'foreach'));
      const total: number = typeof result?.total === 'number' ? result.total
        : (typeof result?.total_items === 'number' ? result.total_items
          : (Array.isArray(input?.items) ? input.items.length : 0));
      const completed: number = typeof result?.completed === 'number' ? result.completed : 0;
      const succeeded: number = typeof result?.succeeded === 'number' ? result.succeeded
        : (typeof result?.succeeded_items === 'number' ? result.succeeded_items : 0);
      const failed: number = typeof result?.failed === 'number' ? result.failed
        : (typeof result?.failed_items === 'number' ? result.failed_items : 0);

      if (isComplete) {
        if (failed > 0) {
          return `Completed ${opName}: ${succeeded}/${total} succeeded, ${failed} failed`;
        }
        return `Completed ${opName}: ${total} item${total === 1 ? '' : 's'} processed`;
      }

      if (total > 0) {
        const pct: number = Math.round((completed / total) * 100);
        const progressText: string = failed > 0
          ? `${completed}/${total} (${pct}%) — ${succeeded} ok, ${failed} failed`
          : `${completed}/${total} (${pct}%)`;
        return `Running ${opName}... ${progressText}`;
      }
      return `Running ${opName}...`;
    }
    case 'run_workflow': {
      const workflowName = typeof result?.workflow_name === 'string'
        ? result.workflow_name
        : (typeof input?.workflow_name === 'string' ? input.workflow_name : 'workflow');
      if (isComplete) {
        return `Completed ${workflowName}`;
      }
      return `Running ${workflowName}...`;
    }
    case 'query_on_connector': {
      const connectorSlug: string = typeof input?.connector === 'string' ? input.connector : '';
      const connectorLabel: string = connectorSlug
        ? connectorSlug.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
        : 'connector';
      if (isComplete) {
        if (result?.error) return `Query to ${connectorLabel} failed`;
        const count = typeof result?.count === 'number' ? result.count : (Array.isArray(result?.files) ? result.files.length : undefined);
        const isSingleFileRead: boolean = connectorSlug === 'google_drive' && result?.file_name != null && result?.content != null;
        if (count !== undefined && connectorSlug === 'google_drive') {
          return count === 1 ? `Read 1 file from ${connectorLabel}` : `Read ${count} files from ${connectorLabel}`;
        }
        if (isSingleFileRead) return `Read 1 file from ${connectorLabel}`;
        return `Queried ${connectorLabel}`;
      }
      return `Querying ${connectorLabel}...`;
    }
    case 'write_on_connector': {
      const writeConnector: string = typeof input?.connector === 'string' ? input.connector : '';
      const writeOp: string = typeof input?.operation === 'string' ? input.operation : 'write';
      const connectorLabel: string = writeConnector ? writeConnector.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase()) : 'connector';
      const opLabel: string = writeOp.replace(/_/g, ' ');
      if (isComplete) {
        return result?.error ? `Write to ${connectorLabel} failed` : `Wrote to ${connectorLabel} (${opLabel})`;
      }
      return `Writing to ${connectorLabel} (${opLabel})...`;
    }
    case 'run_on_connector': {
      const actionConnector: string = typeof input?.connector === 'string' ? input.connector : '';
      const actionName: string = typeof input?.action === 'string' ? input.action : '';
      const connectorLabel: string = actionConnector ? actionConnector.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase()) : '';
      const actionLabel: string = actionName ? actionName.replace(/_/g, ' ') : '';
      if (connectorLabel && actionLabel) {
        if (isComplete) {
          return result?.error ? `Action on ${connectorLabel} failed` : `Ran ${actionLabel} on ${connectorLabel}`;
        }
        return `Running ${actionLabel} on ${connectorLabel}...`;
      }
      if (isComplete) {
        return result?.error ? 'Connector action failed' : 'Completed connector action';
      }
      return 'Running action (details when available)...';
    }
    case 'write_app': {
      const operation: string = typeof input?.operation === 'string' ? input.operation : 'create';
      const appTitle: string = typeof input?.title === 'string' ? input.title 
        : (typeof result?.title === 'string' ? result.title : 'app');
      if (operation === 'create') {
        if (isComplete) {
          return result?.error ? 'Failed to create app' : `Created app: ${appTitle}`;
        }
        return `Creating app: ${appTitle}...`;
      }
      if (operation === 'update') {
        if (isComplete) {
          return result?.error ? 'Failed to update app' : `Updated app: ${appTitle}`;
        }
        return `Updating app...`;
      }
      if (operation === 'read') {
        if (isComplete) {
          return result?.error ? 'Failed to read app' : `Read app: ${appTitle}`;
        }
        return 'Reading app code...';
      }
      if (operation === 'test_query') {
        const queryName: string = typeof input?.query_name === 'string' ? input.query_name : 'query';
        const rowCount: number | undefined = typeof result?.row_count === 'number' ? result.row_count : undefined;
        if (isComplete) {
          return result?.error ? `Query test failed` : `Tested query "${queryName}" (${rowCount ?? 0} rows)`;
        }
        return `Testing query "${queryName}"...`;
      }
      // Fallback for unknown operations
      if (isComplete) {
        return result?.error ? 'App operation failed' : 'Completed app operation';
      }
      return 'Working on app...';
    }
    default:
      return isComplete ? `Completed ${toolName}` : `Running ${toolName}...`;
  }
}

/**
 * Tool call indicator - clickable to show details
 */
/**
 * Modal for showing tool call details
 */
function ToolCallModal({ 
  toolCall, 
  onClose 
}: { 
  toolCall: ToolCallData; 
  onClose: () => void;
}): JSX.Element {
  const isComplete = toolCall.status === 'complete';
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div 
        className="bg-surface-900 border border-surface-700 rounded-xl max-w-2xl w-full max-h-[80vh] overflow-hidden shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-surface-700">
          <div className="flex items-center gap-3">
            {isComplete ? (
              <div className="w-8 h-8 rounded-lg bg-green-500/20 flex items-center justify-center">
                <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
            ) : (
              <div className="w-8 h-8 rounded-lg bg-primary-500/20 flex items-center justify-center">
                <svg className="w-4 h-4 text-primary-400 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              </div>
            )}
            <div>
              <h3 className="text-lg font-semibold text-surface-100">{toolCall.toolName}</h3>
              <p className="text-sm text-surface-400">
                {isComplete ? 'Completed' : 'Running...'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-surface-400 hover:text-surface-200 p-1"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto max-h-[calc(80vh-80px)] space-y-4">
          {/* Input */}
          <div>
            <h4 className="text-sm font-medium text-surface-300 mb-2">Input</h4>
            {toolCall.input && Object.keys(toolCall.input).length > 0 ? (
              <pre className="bg-surface-800 rounded-lg p-3 text-sm text-surface-200 overflow-x-auto">
                {JSON.stringify(toolCall.input, null, 2)}
              </pre>
            ) : (
              <p className="text-surface-500 text-sm italic">
                Parameters not yet available. They will appear when the request is fully received or after it completes.
              </p>
            )}
          </div>

          {/* Result */}
          {toolCall.result && (
            <div>
              <h4 className="text-sm font-medium text-surface-300 mb-2">Result</h4>
              <pre className="bg-surface-800 rounded-lg p-3 text-sm text-surface-200 overflow-x-auto max-h-96 overflow-y-auto">
                {JSON.stringify(toolCall.result, null, 2)}
              </pre>
            </div>
          )}

          {/* Tool ID for debugging */}
          <div className="text-xs text-surface-500 pt-2 border-t border-surface-800">
            Tool ID: {toolCall.toolId}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Thinking indicator - shows while waiting for assistant response
 */
function ThinkingIndicator(): JSX.Element {
  return (
    <div className="flex gap-3">
      {/* Avatar */}
      <div className="w-6 h-6 rounded-md bg-gradient-to-br from-surface-700 to-surface-800 flex items-center justify-center flex-shrink-0">
        <img src={LOGO_PATH} alt={APP_NAME} className="w-3.5 h-3.5 opacity-90" />
      </div>

      {/* Thinking dots */}
      <div className="bg-surface-800/50 rounded-xl rounded-tl-sm px-3 py-2">
        <div className="flex items-center gap-1">
          <div className="w-1.5 h-1.5 bg-surface-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <div className="w-1.5 h-1.5 bg-surface-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <div className="w-1.5 h-1.5 bg-surface-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
      </div>
    </div>
  );
}

/**
 * Generate a chat title from the first message.
 */
function generateTitle(message: string): string {
  const cleaned = message.trim().replace(/\n/g, ' ');

  if (cleaned.endsWith('?') && cleaned.length <= 50) {
    return cleaned;
  }

  const words = cleaned.split(' ').slice(0, 6);
  let title = words.join(' ');

  if (title.length > 40) {
    title = title.slice(0, 40);
  }

  if (cleaned.length > title.length) {
    title += '...';
  }

  return title || 'New Chat';
}

function ConnectionStatus({
  state,
}: {
  state: 'connecting' | 'connected' | 'disconnected' | 'error';
}): JSX.Element | null {
  if (state === 'connected') return null;

  const statusConfig = {
    connecting: { color: 'bg-yellow-500', text: 'Connecting...' },
    disconnected: { color: 'bg-surface-500', text: 'Disconnected' },
    error: { color: 'bg-red-500', text: 'Error' },
  };

  const config = statusConfig[state];

  return (
    <div className="flex items-center gap-2 text-sm text-surface-400">
      <div className={`w-2 h-2 rounded-full ${config.color}`} />
      <span>{config.text}</span>
    </div>
  );
}

/**
 * Get a short file-type label from a mime type or filename extension.
 */
function getFileTypeLabel(filename: string, mimeType: string): string {
  const ext: string = filename.split('.').pop()?.toLowerCase() ?? '';
  const extMap: Record<string, string> = {
    pdf: 'PDF', csv: 'CSV', xlsx: 'Excel', xls: 'Excel',
    json: 'JSON', md: 'Markdown', xml: 'XML', html: 'HTML', txt: 'Text',
    png: 'PNG', jpg: 'JPEG', jpeg: 'JPEG', gif: 'GIF', webp: 'WebP', svg: 'SVG',
  };
  if (ext && ext in extMap) return extMap[ext] as string;
  if (mimeType.startsWith('image/')) return 'Image';
  if (mimeType.startsWith('text/')) return 'Text';
  return ext.toUpperCase() || 'File';
}

/**
 * Format file size in human-readable form.
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * File-type icon color based on extension.
 */
function getFileIconColor(filename: string, mimeType: string): string {
  const ext: string = filename.split('.').pop()?.toLowerCase() ?? '';
  if (['csv', 'xlsx', 'xls'].includes(ext)) return 'bg-emerald-700 text-emerald-200';
  if (ext === 'pdf') return 'bg-red-800 text-red-200';
  if (ext === 'json') return 'bg-yellow-800 text-yellow-200';
  if (mimeType.startsWith('image/')) return 'bg-violet-800 text-violet-200';
  return 'bg-surface-700 text-surface-300';
}

/**
 * Attachment card — used in both pending input and sent message bubbles.
 * Pass `onRemove` to show a dismiss button (for pending attachments).
 */
function AttachmentCard({
  filename,
  mimeType,
  size,
  onRemove,
}: {
  filename: string;
  mimeType: string;
  size: number;
  onRemove?: () => void;
}): JSX.Element {
  const label: string = getFileTypeLabel(filename, mimeType);
  const sizeStr: string = formatFileSize(size);
  const iconColor: string = getFileIconColor(filename, mimeType);

  return (
    <div className="relative group inline-flex items-center gap-2.5 rounded-xl bg-surface-800 border border-surface-700 px-3 py-2 max-w-[220px]">
      {/* File type icon */}
      <div className={`flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center text-[10px] font-bold ${iconColor}`}>
        {label}
      </div>
      {/* Name + size */}
      <div className="min-w-0 flex flex-col">
        <span className="text-xs text-surface-200 font-medium truncate">{filename}</span>
        <span className="text-[10px] text-surface-500">{sizeStr}</span>
      </div>
      {/* Remove button (only for pending) */}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-surface-700 border border-surface-600 text-surface-400 hover:text-surface-100 hover:bg-surface-600 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  );
}

function buildSuggestions(connected: Integration[]): string[] {
  const providers = new Set(connected.map((i) => i.provider));
  const suggestions: string[] = [];

  if (providers.has('hubspot') || providers.has('salesforce'))
    suggestions.push('What deals are closing this month?', 'Show me my pipeline by stage');
  if (providers.has('gmail') || providers.has('microsoft_mail'))
    suggestions.push('Summarize my unread emails from today');
  if (providers.has('google_calendar') || providers.has('microsoft_calendar') || providers.has('zoom'))
    suggestions.push('What meetings do I have this week?');
  if (providers.has('github') || providers.has('linear') || providers.has('jira') || providers.has('asana'))
    suggestions.push('Show me open issues assigned to me');
  if (providers.has('slack'))
    suggestions.push('What are the latest messages in my Slack channels?');

  if (suggestions.length < 3)
    return ['What can you help me with?', 'What data sources can I connect?', 'Show me what you can do'];

  return suggestions.slice(0, 5);
}

interface EmptyStateProps {
  onSuggestionClick: (text: string) => void;
}

function EmptyState({ onSuggestionClick }: EmptyStateProps): JSX.Element {
  const connected = useConnectedIntegrations();
  const suggestions = buildSuggestions(connected);

  return (
    <div className="h-full flex items-center justify-center px-4">
      <div className="text-center max-w-lg">
        <div className="w-16 h-16 md:w-20 md:h-20 rounded-2xl bg-primary-500/10 flex items-center justify-center mx-auto mb-4 md:mb-6">
          <img 
            src={LOGO_PATH} 
            alt={APP_NAME} 
            className="w-8 h-8 md:w-10 md:h-10" 
          />
        </div>
        <h2 className="text-xl md:text-2xl font-bold text-surface-50 mb-2">
          Ask me anything
        </h2>
        <p className="text-surface-400 mb-6 md:mb-8 text-sm md:text-base">
          Get instant insights from your connected data sources
        </p>
        <div className="flex flex-wrap gap-2 justify-center">
          {suggestions.map((text) => (
            <button
              key={text}
              onClick={() => onSuggestionClick(text)}
              className="px-3 md:px-4 py-1.5 md:py-2 rounded-full bg-surface-800 hover:bg-surface-700 text-surface-300 text-xs md:text-sm transition-colors border border-surface-700"
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
