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
import { PendingApprovalCard, type ApprovalResult } from './PendingApprovalCard';
import { getConversation, uploadChatFile, type UploadResponse } from '../api/client';
import { crossTab } from '../lib/crossTab';
import { 
  useAppStore,
  useConversationState,
  type ChatMessage,
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
  userId: string;
  organizationId: string;
  chatId?: string | null;
  sendMessage: (data: Record<string, unknown>) => void;
  isConnected: boolean;
  connectionState: 'connecting' | 'connected' | 'disconnected' | 'error';
  crmApprovalResults: Map<string, unknown>;
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

export function Chat({ 
  userId, 
  organizationId: _organizationId,
  chatId, 
  sendMessage,
  isConnected,
  connectionState,
  crmApprovalResults,
}: ChatProps): JSX.Element {
  void _organizationId; // kept for API compatibility
  // Get per-conversation state from Zustand
  const conversationState = useConversationState(chatId ?? null);
  const chatTitle = conversationState?.title ?? 'New Chat';
  const conversationThinking = conversationState?.isThinking ?? false;
  const activeTaskId = conversationState?.activeTaskId ?? null;
  
  // Get actions from Zustand (stable references)
  const addConversationMessage = useAppStore((s) => s.addConversationMessage);
  const setConversationMessages = useAppStore((s) => s.setConversationMessages);
  const setConversationTitle = useAppStore((s) => s.setConversationTitle);
  const setConversationThinking = useAppStore((s) => s.setConversationThinking);
  const pendingChatInput = useAppStore((s) => s.pendingChatInput);
  const setPendingChatInput = useAppStore((s) => s.setPendingChatInput);
  const pendingChatAutoSend = useAppStore((s) => s.pendingChatAutoSend);
  const setPendingChatAutoSend = useAppStore((s) => s.setPendingChatAutoSend);
  
  // Local state
  const [input, setInput] = useState<string>('');
  const [currentArtifact, setCurrentArtifact] = useState<AnyArtifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [selectedToolCall, setSelectedToolCall] = useState<ToolCallData | null>(null);
  const [toolApprovals, setToolApprovals] = useState<Map<string, ToolApprovalState>>(new Map());
  const [localConversationId, setLocalConversationId] = useState<string | null>(chatId ?? null);
  // Pending messages for new conversations (before we have an ID)
  const [pendingMessages, setPendingMessages] = useState<ChatMessage[]>([]);
  const [pendingThinking, setPendingThinking] = useState<boolean>(false);
  const [conversationType, setConversationType] = useState<string | null>(null);
  const [isWorkflowPolling, setIsWorkflowPolling] = useState<boolean>(false);
  
  // Attachment state
  const [pendingAttachments, setPendingAttachments] = useState<UploadResponse[]>([]);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  
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
  
  // Keep ref in sync with state
  pendingMessagesRef.current = pendingMessages;

  // Combined messages and thinking state (conversation + pending for new chats)
  const messages = useMemo(() => {
    const conversationMessages = conversationState?.messages ?? [];
    return pendingMessages.length > 0
      ? [...pendingMessages, ...conversationMessages]
      : conversationMessages;
  }, [pendingMessages, conversationState?.messages]);
  const isThinking = pendingThinking || conversationThinking;

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
    // Reset conversation type when starting a new chat
    if (!chatId) {
      setConversationType(null);
      setIsWorkflowPolling(false);
    }
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
      setIsLoading(false);
      return;
    }

    // If there's an active task for this conversation, don't load from API yet
    // The task will populate the state via WebSocket updates
    const activeTasks = useAppStore.getState().activeTasksByConversation;
    if (chatId in activeTasks) {
      console.log('[Chat] Skipping load - active task in progress');
      setIsLoading(false);
      return;
    }

    // If we already have messages for this conversation in state, don't reload
    const existingState = useAppStore.getState().conversations[chatId];
    if (existingState && existingState.messages.length > 0) {
      console.log('[Chat] Using existing state for conversation:', chatId);
      setIsLoading(false);
      return;
    }

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
          }));
          
          // Set conversation state
          setConversationMessages(chatId, loadedMessages);
          setConversationTitle(chatId, data.title ?? 'New Chat');
          setConversationType(data.type ?? null);
          console.log('[Chat] Loaded', loadedMessages.length, 'messages, type:', data.type);
          
          // Scroll to bottom immediately after loading
          setTimeout(() => {
            messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
          }, 50);
        } else {
          console.error('[Chat] Failed to load conversation:', error);
        }
      } catch (err) {
        console.error('[Chat] Exception loading conversation:', err);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    void loadConversation();

    return () => {
      cancelled = true;
    };
  }, [chatId, userId, setConversationMessages, setConversationTitle]);

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
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleScroll = (): void => {
      if (isProgrammaticScrollRef.current) return;
      const threshold = 100; // px from bottom
      const distanceFromBottom: number = container.scrollHeight - container.scrollTop - container.clientHeight;
      isUserNearBottomRef.current = distanceFromBottom <= threshold;
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  // Auto-scroll to bottom only if user hasn't scrolled up.
  // Use instant scroll during streaming to avoid smooth-scroll animations
  // that fire intermediate scroll events and defeat the user's scroll-up.
  useEffect(() => {
    if (isUserNearBottomRef.current) {
      const container = messagesContainerRef.current;
      if (container) {
        isProgrammaticScrollRef.current = true;
        container.scrollTop = container.scrollHeight;
        requestAnimationFrame(() => {
          isProgrammaticScrollRef.current = false;
        });
      }
    }
  }, [messages, isThinking]);

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
  ]);

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
          <h1 className="text-lg font-semibold text-surface-100 truncate max-w-[200px] md:max-w-md">
            {chatTitle}
          </h1>
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
        </div>
        <ConnectionStatus state={connectionState} />
      </header>

      {/* Content area with messages and optional artifact sidebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* Messages */}
        <div ref={messagesContainerRef} className={`overflow-y-auto overflow-x-hidden p-3 md:p-6 ${currentArtifact ? 'w-1/2' : 'flex-1'}`}>
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
              {messages.map((msg) => (
                <MessageWithBlocks
                  key={msg.id}
                  message={msg}
                  toolApprovals={toolApprovals}
                  onArtifactClick={setCurrentArtifact}
                  onToolApprove={handleToolApprove}
                  onToolCancel={handleToolCancel}
                  onToolClick={(block) => setSelectedToolCall({
                    toolName: block.name,
                    toolId: block.id,
                    input: block.input,
                    result: block.result,
                    status: block.status === 'complete' ? 'complete' : 'running',
                  })}
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

        {/* Artifact sidebar - overlay on mobile, sidebar on desktop */}
        {currentArtifact && (
          <>
            {/* Mobile backdrop */}
            <div 
              className="fixed inset-0 bg-black/50 z-40 md:hidden"
              onClick={() => setCurrentArtifact(null)}
            />
            <div className="fixed inset-y-0 right-0 w-full max-w-md z-50 md:relative md:w-1/2 md:z-auto border-l border-surface-800 bg-surface-900 p-4 overflow-y-auto">
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
          <div className={`rounded-2xl border bg-surface-900 transition-all duration-150 ${
            (!isConnected || isThinking) ? 'border-surface-700 opacity-50' : 'border-surface-700 focus-within:ring-2 focus-within:ring-primary-500 focus-within:border-transparent'
          }`}>
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
              placeholder={isThinking ? 'Thinking...' : 'Ask about your pipeline...'}
              className="w-full resize-none bg-transparent text-surface-100 px-4 pt-3 pb-1 text-sm placeholder-surface-500 focus:outline-none leading-5 scrollbar-none disabled:cursor-not-allowed"
              style={{ minHeight: '36px', maxHeight: '240px' }}
              rows={1}
              disabled={!isConnected || isThinking}
              autoFocus={chatId === null}
            />

            {/* Bottom row: attach on left, send/stop on right */}
            <div className="flex items-center justify-between px-2 pb-2">
              {/* Attach button */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading || isThinking}
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

              {/* Send/Stop button */}
              {isThinking ? (
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
                  disabled={(!input.trim() && pendingAttachments.length === 0) || !isConnected}
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
  onToolApprove,
  onToolCancel,
  onToolClick,
}: {
  message: ChatMessage;
  toolApprovals: Map<string, { operationId: string; toolName: string; isProcessing: boolean; result: unknown }>;
  onArtifactClick: (artifact: AnyArtifact) => void;
  onToolApprove: (operationId: string, options?: Record<string, unknown>) => void;
  onToolCancel: (operationId: string) => void;
  onToolClick: (block: ToolUseBlock) => void;
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
    
    return (
      <div className="flex gap-2 flex-row-reverse animate-slide-up">
        {/* Avatar */}
        <div className="flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center bg-primary-600">
          <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
        </div>

        {/* Content: bubble + attachments + timestamp, all right-aligned */}
        <div className="flex-1 max-w-[85%] text-right">
          <div className="inline-block px-3 py-2 rounded-xl rounded-tr-sm bg-primary-600 text-white text-[13px] leading-relaxed">
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
      <div className="flex-shrink-0 w-6 h-6 rounded-md bg-gradient-to-br from-surface-700 to-surface-800 flex items-center justify-center">
        <img 
          src="/logo.svg" 
          alt="Revtops" 
          className="w-3.5 h-3.5" 
          style={{ filter: 'invert(67%) sepia(51%) saturate(439%) hue-rotate(108deg) brightness(92%) contrast(88%)' }} 
        />
      </div>

      {/* Content blocks in order */}
      <div className="flex-1 max-w-[85%]">
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
                <ErrorBlockIndicator block={block} />
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
    <div className="inline-block px-3 py-2 rounded-xl rounded-tl-sm bg-surface-800/80 text-surface-200 text-[13px] leading-relaxed">
      <div className={`prose prose-sm prose-invert max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-pre:my-2 prose-code:text-primary-300 prose-code:bg-surface-900/50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-surface-900/80 prose-pre:text-xs prose-table:text-xs prose-th:bg-surface-700/50 prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1 prose-td:border-surface-700 prose-th:border-surface-700 ${isStreaming ? '[&>p:last-of-type]:inline [&>p:last-of-type]:mb-0' : ''}`}>
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
}: {
  block: ErrorBlock;
}): JSX.Element {
  // Parse the error message to extract a user-friendly summary
  const errorSummary = getErrorSummary(block.message);

  return (
    <div className="flex items-center gap-1.5 py-0.5 text-xs text-red-400/80">
      <svg className="w-3.5 h-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
      <span className="italic">{errorSummary}</span>
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
    case 'search_activities': {
      const query = typeof input?.query === 'string' ? input.query : '';
      const truncatedQuery = query.length > 40 ? query.slice(0, 40) + '...' : query;
      if (isComplete) {
        const count = typeof result?.count === 'number' ? result.count : 0;
        const countText = ` (${count} result${count === 1 ? '' : 's'})`;
        return `Searched activities for '${truncatedQuery}'${countText}`;
      }
      return `Searching activities for '${truncatedQuery}'...`;
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
            <pre className="bg-surface-800 rounded-lg p-3 text-sm text-surface-200 overflow-x-auto">
              {JSON.stringify(toolCall.input, null, 2)}
            </pre>
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
        <img src="/logo.svg" alt="Revtops" className="w-3.5 h-3.5 opacity-70" style={{ filter: 'invert(67%) sepia(51%) saturate(439%) hue-rotate(108deg) brightness(92%) contrast(88%)' }} />
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
}): JSX.Element {
  const statusConfig = {
    connecting: { color: 'bg-yellow-500', text: 'Connecting...' },
    connected: { color: 'bg-green-500', text: 'Connected' },
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

interface EmptyStateProps {
  onSuggestionClick: (text: string) => void;
}

function EmptyState({ onSuggestionClick }: EmptyStateProps): JSX.Element {
  const suggestions = [
    'What deals are closing this month?',
    'Show me my pipeline by stage',
    'Which accounts need attention?',
    'Compare rep performance this quarter',
    'What meetings do I have this week?',
  ];

  return (
    <div className="h-full flex items-center justify-center px-4">
      <div className="text-center max-w-lg">
        <div className="w-16 h-16 md:w-20 md:h-20 rounded-2xl bg-gradient-to-br from-primary-500/20 to-primary-700/20 flex items-center justify-center mx-auto mb-4 md:mb-6">
          <img 
            src="/logo.svg" 
            alt="Revtops" 
            className="w-8 h-8 md:w-10 md:h-10" 
            style={{ filter: 'invert(67%) sepia(51%) saturate(439%) hue-rotate(108deg) brightness(92%) contrast(88%)' }} 
          />
        </div>
        <h2 className="text-xl md:text-2xl font-bold text-surface-50 mb-2">
          Ask anything about your revenue
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
