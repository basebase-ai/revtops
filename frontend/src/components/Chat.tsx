/**
 * Chat interface component.
 *
 * Features:
 * - WebSocket connection to backend with conversation support
 * - Message history display (stored in Zustand)
 * - Input for user messages
 * - Streaming response display with "thinking" indicator
 * - Artifact viewer for dashboards/reports
 * - Auto-generates chat title from first message
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import { Message } from './Message';
import { ArtifactViewer } from './ArtifactViewer';
import { CrmApprovalCard } from './CrmApprovalCard';
import { getConversation } from '../api/client';
import { 
  useAppStore, 
  useMessages, 
  useChatTitle, 
  useIsThinking,
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
}

// WebSocket message types
interface WsConversationCreated {
  type: 'conversation_created';
  conversation_id: string;
}

interface WsMessageComplete {
  type: 'message_complete';
  conversation_id: string;
}

interface WsToolCall {
  type: 'tool_call';
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_id: string;
  status: 'running';
}

interface WsToolResult {
  type: 'tool_result';
  tool_name: string;
  tool_id: string;
  result: Record<string, unknown>;
  status: 'complete';
}

interface WsTextBlockComplete {
  type: 'text_block_complete';
}

interface WsCrmApprovalResult {
  type: 'crm_approval_result';
  operation_id: string;
  status: string;
  message?: string;
  success_count?: number;
  failure_count?: number;
  skipped_count?: number;
  error?: string;
}

type WsControlMessage = WsConversationCreated | WsMessageComplete | WsToolCall | WsToolResult | WsTextBlockComplete | WsCrmApprovalResult;

// CRM approval state tracking
interface CrmApprovalState {
  operationId: string;
  isProcessing: boolean;
  result: WsCrmApprovalResult | null;
}

function isControlMessage(data: unknown): data is WsControlMessage {
  return typeof data === 'object' && data !== null && 'type' in data;
}

export function Chat({ userId, organizationId: _organizationId, chatId }: ChatProps): JSX.Element {
  // Get state from Zustand
  const messages = useMessages();
  const chatTitle = useChatTitle();
  const isThinking = useIsThinking();
  
  // Get actions from Zustand (stable references)
  const addMessage = useAppStore((s) => s.addMessage);
  const appendToStreamingMessage = useAppStore((s) => s.appendToStreamingMessage);
  const startStreamingMessage = useAppStore((s) => s.startStreamingMessage);
  const markMessageComplete = useAppStore((s) => s.markMessageComplete);
  const setChatTitle = useAppStore((s) => s.setChatTitle);
  const setIsThinking = useAppStore((s) => s.setIsThinking);
  const setConversationId = useAppStore((s) => s.setConversationId);
  const setMessages = useAppStore((s) => s.setMessages);
  const clearChat = useAppStore((s) => s.clearChat);
  const addConversation = useAppStore((s) => s.addConversation);
  const conversationId = useAppStore((s) => s.conversationId);
  const updateToolMessage = useAppStore((s) => s.updateToolMessage);
  
  // Local state
  const [input, setInput] = useState<string>('');
  const [currentArtifact, setCurrentArtifact] = useState<Artifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [selectedToolCall, setSelectedToolCall] = useState<ToolCallData | null>(null);
  const [crmApprovals, setCrmApprovals] = useState<Map<string, CrmApprovalState>>(new Map());
  
  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const notifiedConversationRef = useRef<string | null>(null);
  const pendingTitleRef = useRef<string | null>(null);

  // Handle WebSocket message - uses Zustand actions directly
  const handleWebSocketMessage = useCallback((message: string): void => {
    // Try to parse as JSON control message
    try {
      const parsed: unknown = JSON.parse(message);
      if (isControlMessage(parsed)) {
        if (parsed.type === 'conversation_created') {
          // Only process if we haven't already notified for this conversation
          if (notifiedConversationRef.current === parsed.conversation_id) {
            console.log('[Chat] Already notified for conversation:', parsed.conversation_id);
            return;
          }
          
          console.log('[Chat] Conversation created:', parsed.conversation_id);
          setConversationId(parsed.conversation_id);
          notifiedConversationRef.current = parsed.conversation_id;
          
          // Use pending title
          const title = pendingTitleRef.current ?? 'New Chat';
          setChatTitle(title);
          addConversation(parsed.conversation_id, title);
          pendingTitleRef.current = null;
          return;
        }
        if (parsed.type === 'message_complete') {
          console.log('[Chat] Message complete');
          markMessageComplete();
          return;
        }
        if (parsed.type === 'text_block_complete') {
          console.log('[Chat] Text block complete (tools incoming)');
          // Mark current message complete so next text starts fresh
          markMessageComplete();
          return;
        }
        if (parsed.type === 'tool_call') {
          console.log('[Chat] Tool call:', parsed.tool_name, parsed.tool_id);
          // Add tool_use block to current streaming message
          const currentMessages = useAppStore.getState().messages;
          const streamingId = useAppStore.getState().streamingMessageId;
          
          if (streamingId) {
            // Add tool_use block to existing streaming message
            const updated = currentMessages.map((msg) => {
              if (msg.id !== streamingId) return msg;
              return {
                ...msg,
                contentBlocks: [
                  ...msg.contentBlocks,
                  {
                    type: 'tool_use' as const,
                    id: parsed.tool_id,
                    name: parsed.tool_name,
                    input: parsed.tool_input,
                    status: 'running' as const,
                  },
                ],
              };
            });
            useAppStore.setState({ messages: updated });
          } else {
            // Create new message with tool_use block
            const newId = `assistant-${Date.now()}`;
            addMessage({
              id: newId,
              role: 'assistant',
              contentBlocks: [{
                type: 'tool_use',
                id: parsed.tool_id,
                name: parsed.tool_name,
                input: parsed.tool_input,
                status: 'running',
              }],
              timestamp: new Date(),
            });
          }
          setIsThinking(false);
          return;
        }
        if (parsed.type === 'tool_result') {
          console.log('[Chat] Tool result:', parsed.tool_name, parsed.tool_id);
          updateToolMessage(parsed.tool_id, {
            result: parsed.result,
            status: 'complete',
          });
          return;
        }
        if (parsed.type === 'crm_approval_result') {
          console.log('[Chat] CRM approval result:', parsed.operation_id, parsed.status);
          setCrmApprovals((prev) => {
            const newMap = new Map(prev);
            const existing = newMap.get(parsed.operation_id);
            if (existing) {
              newMap.set(parsed.operation_id, {
                ...existing,
                isProcessing: false,
                result: parsed,
              });
            }
            return newMap;
          });
          return;
        }
      }
    } catch {
      // Not JSON, treat as text chunk
    }

    // Text chunk from assistant
    console.log('[Chat] Received text chunk:', message.substring(0, 30) + '...');
    
    // Get current streaming state from store
    const currentStreamingId = useAppStore.getState().streamingMessageId;
    
    if (currentStreamingId) {
      // Append to existing streaming message
      appendToStreamingMessage(message);
    } else {
      // First chunk - create new assistant message
      const newId = `assistant-${Date.now()}`;
      console.log('[Chat] Starting streaming message:', newId);
      startStreamingMessage(newId, message);
    }
  }, [addMessage, appendToStreamingMessage, startStreamingMessage, markMessageComplete, setChatTitle, setIsThinking, setConversationId, addConversation, updateToolMessage]);

  const { sendMessage, isConnected, connectionState } = useWebSocket(
    `/ws/chat/${userId}`,
    { onMessage: handleWebSocketMessage }
  );

  // Handle CRM approval
  const handleCrmApprove = useCallback((operationId: string, skipDuplicates: boolean) => {
    console.log('[Chat] Approving CRM operation:', operationId);
    setCrmApprovals((prev) => {
      const newMap = new Map(prev);
      newMap.set(operationId, {
        operationId,
        isProcessing: true,
        result: null,
      });
      return newMap;
    });
    // Include conversation_id so backend can send errors back to the agent
    const currentConversationId = conversationId || chatId;
    sendMessage(JSON.stringify({
      type: 'crm_approval',
      operation_id: operationId,
      approved: true,
      skip_duplicates: skipDuplicates,
      conversation_id: currentConversationId,
    }));
  }, [sendMessage, conversationId, chatId]);

  // Handle CRM cancel
  const handleCrmCancel = useCallback((operationId: string) => {
    console.log('[Chat] Canceling CRM operation:', operationId);
    setCrmApprovals((prev) => {
      const newMap = new Map(prev);
      newMap.set(operationId, {
        operationId,
        isProcessing: true,
        result: null,
      });
      return newMap;
    });
    const currentConversationId = conversationId || chatId;
    sendMessage(JSON.stringify({
      type: 'crm_approval',
      operation_id: operationId,
      approved: false,
      conversation_id: currentConversationId,
    }));
  }, [sendMessage, conversationId, chatId]);

  // Reset state when chatId becomes null (New Chat clicked)
  useEffect(() => {
    if (chatId === null) {
      console.log('[Chat] New chat started, clearing state');
      clearChat();
      notifiedConversationRef.current = null;
    }
  }, [chatId, clearChat]);

  // Auto-focus input when on a new empty chat
  useEffect(() => {
    if (chatId === null && messages.length === 0 && !isLoading && isConnected) {
      // Small delay to ensure component is fully rendered
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

    // If chatId matches our current conversation, don't reload
    if (chatId === conversationId) {
      console.log('[Chat] Skipping load - already loaded this conversation');
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
          setConversationId(chatId);
          setChatTitle(data.title ?? 'New Chat');
          notifiedConversationRef.current = chatId;
          
          // Convert API messages to store format (content_blocks)
          const loadedMessages: ChatMessage[] = data.messages.map((msg) => ({
            id: msg.id,
            role: msg.role as 'user' | 'assistant',
            contentBlocks: msg.content_blocks,
            timestamp: new Date(msg.created_at),
          }));
          
          setMessages(loadedMessages);
          console.log('[Chat] Loaded', loadedMessages.length, 'messages');
          
          // Scroll to bottom immediately after loading (use timeout to ensure DOM updated)
          setTimeout(() => {
            messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
          }, 50);
        } else {
          console.error('[Chat] Failed to load conversation:', error);
          clearChat();
        }
      } catch (err) {
        console.error('[Chat] Exception loading conversation:', err);
        if (!cancelled) {
          clearChat();
        }
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
  }, [chatId, userId, conversationId, setConversationId, setChatTitle, setMessages, clearChat]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  const handleSend = useCallback((): void => {
    if (!input.trim() || !isConnected) {
      console.log('[Chat] handleSend blocked - input empty or not connected');
      return;
    }

    console.log('[Chat] Sending message:', input.substring(0, 30) + '...');

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      contentBlocks: [{ type: 'text', text: input }],
      timestamp: new Date(),
    };

    // If this is a new conversation, store the title for when conversation_created arrives
    const currentConvId = useAppStore.getState().conversationId;
    if (!currentConvId) {
      pendingTitleRef.current = generateTitle(input);
    }

    addMessage(userMessage);
    setIsThinking(true);

    // Send message with conversation context and timezone info
    const now = new Date();
    const payload = JSON.stringify({
      message: input,
      conversation_id: currentConvId,
      local_time: now.toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      timezone_offset_minutes: now.getTimezoneOffset(),
    });
    console.log('[Chat] Sending to WebSocket:', payload);
    sendMessage(payload);
    setInput('');
    
    // Reset textarea height to default
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
    }
  }, [input, isConnected, sendMessage, addMessage, setIsThinking]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSuggestionClick = (text: string): void => {
    setInput(text);
    inputRef.current?.focus();
  };

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
      {/* Header */}
      <header className="h-14 border-b border-surface-800 flex items-center justify-between px-6 flex-shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-surface-100 truncate max-w-md">
            {chatTitle}
          </h1>
        </div>
        <ConnectionStatus state={connectionState} />
      </header>

      {/* Content area with messages and optional artifact sidebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-6">
          {messages.length === 0 && !isThinking ? (
            <EmptyState onSuggestionClick={handleSuggestionClick} />
          ) : (
            <div className="max-w-3xl mx-auto space-y-4">
              {messages.map((msg) => (
                <MessageWithBlocks
                  key={msg.id}
                  message={msg}
                  crmApprovals={crmApprovals}
                  onArtifactClick={setCurrentArtifact}
                  onCrmApprove={handleCrmApprove}
                  onCrmCancel={handleCrmCancel}
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

        {/* Artifact sidebar */}
        {currentArtifact && (
          <div className="w-96 border-l border-surface-800 bg-surface-900 p-4 overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-surface-100">
                {currentArtifact.title}
              </h2>
              <button
                onClick={() => setCurrentArtifact(null)}
                className="text-surface-400 hover:text-surface-200"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <ArtifactViewer artifact={currentArtifact} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-surface-800 p-3">
        <div className="max-w-3xl mx-auto">
          <div className="flex items-end gap-2">
            {/* Attach button */}
            <button
              type="button"
              className="flex-shrink-0 w-8 h-8 mb-0.5 rounded-full border border-surface-600 text-surface-400 hover:text-surface-200 hover:border-surface-500 flex items-center justify-center transition-colors"
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
              placeholder="Ask about your pipeline..."
              className="flex-1 resize-none bg-surface-900 text-surface-100 rounded-2xl border border-surface-700 px-4 py-2 text-sm placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all duration-150 leading-5 scrollbar-none"
              style={{ minHeight: '36px', maxHeight: '240px' }}
              rows={1}
              disabled={!isConnected}
              autoFocus={chatId === null}
            />
            
            {/* Send button - circle with up arrow */}
            <button
              onClick={handleSend}
              disabled={!input.trim() || !isConnected}
              className="flex-shrink-0 w-8 h-8 mb-0.5 rounded-full bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 10l7-7m0 0l7 7m-7-7v18" />
              </svg>
            </button>
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
  crmApprovals,
  onArtifactClick,
  onCrmApprove,
  onCrmCancel,
  onToolClick,
}: {
  message: ChatMessage;
  crmApprovals: Map<string, { operationId: string; isProcessing: boolean; result: unknown }>;
  onArtifactClick: (artifact: { id: string; type: string; title: string; data: Record<string, unknown> }) => void;
  onCrmApprove: (operationId: string, skipDuplicates: boolean) => void;
  onCrmCancel: (operationId: string) => void;
  onToolClick: (block: ToolUseBlock) => void;
}): JSX.Element {
  // Safely get content blocks (handle undefined for backwards compat)
  const blocks = message.contentBlocks ?? [];
  
  // Debug logging
  if (blocks.length === 0) {
    console.warn('[MessageWithBlocks] Empty contentBlocks for message:', message.id, message.role);
  }
  
  // Extract text content for the Message component
  const textContent = blocks
    .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
    .map((b) => b.text)
    .join('');

  // Get tool_use blocks
  const toolBlocks = blocks.filter(
    (b): b is ToolUseBlock => b.type === 'tool_use'
  );

  // If nothing to render, return empty fragment
  if (!textContent && toolBlocks.length === 0) {
    console.warn('[MessageWithBlocks] No content to render for message:', message.id, message);
    return <></>;
  }

  return (
    <div>
      {/* Tool calls first (agent runs tools before generating response) */}
      {toolBlocks.map((block) => {
        // CRM write gets special handling
        if (block.name === 'crm_write' && block.result) {
          const result = block.result as Record<string, unknown>;
          const operationId = result.operation_id as string;
          const approvalState = crmApprovals.get(operationId);
          
          // Check if stored result already has final state
          const storedStatus = result?.status as string | undefined;
          const isFinalState = storedStatus && ['completed', 'failed', 'canceled', 'expired'].includes(storedStatus);
          
          const finalResult = isFinalState
            ? (result as { status: string; message?: string; success_count?: number; failure_count?: number; skipped_count?: number; error?: string })
            : (approvalState?.result as { status: string; message?: string; success_count?: number; failure_count?: number; skipped_count?: number; error?: string } | null) ?? null;

          if (result?.preview || finalResult) {
            return (
              <div key={block.id} className="pl-8">
                <CrmApprovalCard
                  data={result as {
                    operation_id: string;
                    target_system: string;
                    record_type: string;
                    operation: string;
                    preview: {
                      records: Record<string, unknown>[];
                      record_count: number;
                      will_create: number;
                      will_skip: number;
                      will_update: number;
                      duplicate_warnings: Array<{
                        record: Record<string, unknown>;
                        existing_id: string;
                        existing: Record<string, unknown>;
                        match_field: string;
                        match_value: string;
                      }>;
                    };
                    message: string;
                  }}
                  onApprove={onCrmApprove}
                  onCancel={onCrmCancel}
                  isProcessing={approvalState?.isProcessing ?? false}
                  result={finalResult}
                />
              </div>
            );
          }
        }

        // Regular tool indicator
        return (
          <ToolBlockIndicator
            key={block.id}
            block={block}
            onClick={() => onToolClick(block)}
          />
        );
      })}

      {/* Text content (agent response after running tools) */}
      {textContent.length > 0 && (
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
      className="flex items-center gap-1.5 py-0.5 ml-8 text-xs text-surface-500 hover:text-surface-300 transition-colors cursor-pointer group text-left"
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
      const knownTables = ['deals', 'accounts', 'contacts', 'activities', 'integrations', 'users'];
      for (const table of knownTables) {
        if (query.includes(table)) {
          tableNames.push(table);
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
    <div className="h-full flex items-center justify-center">
      <div className="text-center max-w-lg">
        <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-500/20 to-primary-700/20 flex items-center justify-center mx-auto mb-6">
          <img 
            src="/logo.svg" 
            alt="Revtops" 
            className="w-10 h-10" 
            style={{ filter: 'invert(67%) sepia(51%) saturate(439%) hue-rotate(108deg) brightness(92%) contrast(88%)' }} 
          />
        </div>
        <h2 className="text-2xl font-bold text-surface-50 mb-2">
          Ask anything about your revenue
        </h2>
        <p className="text-surface-400 mb-8">
          Get instant insights from your connected data sources
        </p>
        <div className="flex flex-wrap gap-2 justify-center">
          {suggestions.map((text) => (
            <button
              key={text}
              onClick={() => onSuggestionClick(text)}
              className="px-4 py-2 rounded-full bg-surface-800 hover:bg-surface-700 text-surface-300 text-sm transition-colors border border-surface-700"
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
