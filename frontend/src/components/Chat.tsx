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
import { Message } from './Message';
import { ArtifactViewer } from './ArtifactViewer';
import { PendingApprovalCard, type ApprovalResult } from './PendingApprovalCard';
import { getConversation } from '../api/client';
import { 
  useAppStore,
  useConversationState,
  type ChatMessage,
  type ToolCallData,
  type ToolUseBlock,
} from '../store';

interface Artifact {
  id: string;
  type: string;
  title: string;
  data: Record<string, unknown>;
}

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
  chatId, 
  sendMessage,
  isConnected,
  connectionState,
  crmApprovalResults,
}: ChatProps): JSX.Element {
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
  const [currentArtifact, setCurrentArtifact] = useState<Artifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [selectedToolCall, setSelectedToolCall] = useState<ToolCallData | null>(null);
  const [toolApprovals, setToolApprovals] = useState<Map<string, ToolApprovalState>>(new Map());
  const [localConversationId, setLocalConversationId] = useState<string | null>(chatId ?? null);
  // Pending messages for new conversations (before we have an ID)
  const [pendingMessages, setPendingMessages] = useState<ChatMessage[]>([]);
  const [pendingThinking, setPendingThinking] = useState<boolean>(false);
  
  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const pendingTitleRef = useRef<string | null>(null);
  const pendingMessagesRef = useRef<ChatMessage[]>([]);
  const pendingAutoSendRef = useRef<string | null>(null);
  
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
        const { data, error } = await getConversation(chatId, userId);
        
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
          console.log('[Chat] Loaded', loadedMessages.length, 'messages');
          
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

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  const sendChatMessage = useCallback((message: string, source: 'input' | 'suggestion' | 'auto'): void => {
    if (!message.trim() || !isConnected) {
      console.log(`[Chat] sendChatMessage blocked (${source}) - empty or not connected`);
      return;
    }

    console.log(`[Chat] Sending message (${source}):`, message.substring(0, 30) + '...');

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      contentBlocks: [{ type: 'text', text: message }],
      timestamp: new Date(),
    };

    // Get current conversation ID
    const currentConvId = localConversationId || chatId;

    if (currentConvId) {
      // Add message to existing conversation
      addConversationMessage(currentConvId, userMessage);
      setConversationThinking(currentConvId, true);
    } else {
      // New conversation - store in pending state
      pendingTitleRef.current = generateTitle(message);
      setPendingMessages(prev => [...prev, userMessage]);
      setPendingThinking(true);
    }

    // Send message with conversation context and timezone info
    const now = new Date();
    sendMessage({
      type: 'send_message',
      message,
      conversation_id: currentConvId,
      local_time: now.toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });

    console.log(`[Chat] Sent to WebSocket (${source})`);
    setInput('');

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
        <div className="flex-1 overflow-y-auto overflow-x-hidden p-3 md:p-6">
          {messages.length === 0 && !isThinking ? (
            <EmptyState onSuggestionClick={handleSuggestionClick} />
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
            <div className="fixed inset-y-0 right-0 w-full max-w-md z-50 md:relative md:w-96 md:z-auto border-l border-surface-800 bg-surface-900 p-4 overflow-y-auto">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-surface-100 truncate">
                  {currentArtifact.title}
                </h2>
                <button
                  onClick={() => setCurrentArtifact(null)}
                  className="text-surface-400 hover:text-surface-200 p-1"
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
          <div className="flex items-end gap-2">
            {/* Attach button - hidden on very small screens */}
            <button
              type="button"
              className="hidden sm:flex flex-shrink-0 w-8 h-8 mb-0.5 rounded-full border border-surface-600 text-surface-400 hover:text-surface-200 hover:border-surface-500 items-center justify-center transition-colors"
              title="Attach file"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
            </button>
            
            {/* Text input */}
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                // Auto-resize textarea
                e.target.style.height = 'auto';
                e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`; // 240px ≈ 10 lines
              }}
              onKeyDown={handleKeyDown}
              placeholder={isThinking ? 'Thinking...' : 'Ask about your pipeline...'}
              className="flex-1 resize-none bg-surface-900 text-surface-100 rounded-2xl border border-surface-700 px-4 py-2 text-sm placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all duration-150 leading-5 scrollbar-none disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ minHeight: '36px', maxHeight: '240px' }}
              rows={1}
              disabled={!isConnected || isThinking}
              autoFocus={chatId === null}
            />
            
            {/* Send/Stop button */}
            {isThinking ? (
              <button
                onClick={handleStop}
                className="flex-shrink-0 w-8 h-8 mb-0.5 rounded-full bg-red-600 text-white hover:bg-red-500 flex items-center justify-center transition-colors"
                title="Stop"
              >
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim() || !isConnected}
                className="flex-shrink-0 w-8 h-8 mb-0.5 rounded-full bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 10l7-7m0 0l7 7m-7-7v18" />
                </svg>
              </button>
            )}
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
  onArtifactClick: (artifact: { id: string; type: string; title: string; data: Record<string, unknown> }) => void;
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

  // For user messages, use the simple Message component
  if (isUser) {
    const textContent = blocks
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('');
    
    return (
      <Message
        message={{
          id: message.id,
          role: message.role,
          content: textContent,
          timestamp: message.timestamp,
          isStreaming: message.isStreaming,
        }}
        onArtifactClick={onArtifactClick}
      />
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
            return (
              <div key={block.id} className="my-0.5">
                {renderToolBlock(block)}
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
  return (
    <div className="inline-block px-3 py-2 rounded-xl rounded-tl-sm bg-surface-800/80 text-surface-200 text-[13px] leading-relaxed">
      <div className="prose prose-sm prose-invert max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-pre:my-2 prose-code:text-primary-300 prose-code:bg-surface-900/50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-surface-900/80 prose-pre:text-xs prose-table:text-xs prose-th:bg-surface-700/50 prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1 prose-td:border-surface-700 prose-th:border-surface-700">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
      {isStreaming && (
        <span className="inline-block w-1.5 h-3 bg-current animate-pulse ml-0.5" />
      )}
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
    case 'create_artifact':
      return isComplete ? 'Created artifact' : 'Creating artifact...';
    case 'crm_write': {
      const recordType = typeof input?.record_type === 'string' ? input.record_type : 'record';
      const recordCount = Array.isArray(input?.records) ? input.records.length : 1;
      const pluralType = recordCount === 1 ? recordType : `${recordType}s`;
      if (isComplete) {
        return `Prepared ${recordCount} ${pluralType} for review`;
      }
      return `Preparing ${recordCount} ${pluralType} for CRM...`;
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
