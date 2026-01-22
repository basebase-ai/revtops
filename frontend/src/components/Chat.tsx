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
import { getConversation } from '../api/client';
import { 
  useAppStore, 
  useMessages, 
  useChatTitle, 
  useIsThinking,
  type ChatMessage,
  type ToolCallData,
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

type WsControlMessage = WsConversationCreated | WsMessageComplete | WsToolCall | WsToolResult | WsTextBlockComplete;

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
          const toolCallData: ToolCallData = {
            toolName: parsed.tool_name,
            toolId: parsed.tool_id,
            input: parsed.tool_input,
            status: 'running',
          };
          addMessage({
            id: `tool-${parsed.tool_id}`,
            role: 'tool',
            content: `Querying ${parsed.tool_name}`,
            toolName: parsed.tool_name,
            toolCall: toolCallData,
            timestamp: new Date(),
          });
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

  // Reset state when chatId becomes null (New Chat clicked)
  useEffect(() => {
    if (chatId === null) {
      console.log('[Chat] New chat started, clearing state');
      clearChat();
      notifiedConversationRef.current = null;
    }
  }, [chatId, clearChat]);

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
          
          const loadedMessages: ChatMessage[] = data.messages.map((msg) => ({
            id: msg.id,
            role: msg.role as 'user' | 'assistant',
            content: msg.content,
            timestamp: new Date(msg.created_at),
          }));
          setMessages(loadedMessages);
          console.log('[Chat] Loaded', loadedMessages.length, 'messages');
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
      content: input,
      timestamp: new Date(),
    };

    // If this is a new conversation, store the title for when conversation_created arrives
    const currentConvId = useAppStore.getState().conversationId;
    if (!currentConvId) {
      pendingTitleRef.current = generateTitle(input);
    }

    addMessage(userMessage);
    setIsThinking(true);

    // Send message with conversation context
    const payload = JSON.stringify({
      message: input,
      conversation_id: currentConvId,
    });
    console.log('[Chat] Sending to WebSocket:', payload);
    sendMessage(payload);
    setInput('');
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
                msg.role === 'tool' ? (
                  <ToolCallIndicator 
                    key={msg.id} 
                    toolCall={msg.toolCall}
                    onClick={() => msg.toolCall && setSelectedToolCall(msg.toolCall)}
                  />
                ) : (
                  <Message
                    key={msg.id}
                    message={{ ...msg, role: msg.role as 'user' | 'assistant' }}
                    onArtifactClick={setCurrentArtifact}
                  />
                )
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
      <div className="border-t border-surface-800 p-4">
        <div className="max-w-3xl mx-auto">
          <div className="flex gap-3">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your pipeline..."
              className="input-field resize-none min-h-[52px] max-h-32 text-[13px]"
              rows={1}
              disabled={!isConnected || isThinking}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || !isConnected || isThinking}
              className="btn-primary px-6 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                />
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

// Map tool names to user-friendly descriptions
const TOOL_FRIENDLY_NAMES: Record<string, string> = {
  run_sql_query: 'synced data',
  create_artifact: 'artifact',
};

function getToolDisplayName(toolName: string): string {
  return TOOL_FRIENDLY_NAMES[toolName] ?? toolName;
}

/**
 * Tool call indicator - clickable to show details
 */
function ToolCallIndicator({ 
  toolCall, 
  onClick 
}: { 
  toolCall?: ToolCallData; 
  onClick: () => void;
}): JSX.Element {
  const isComplete = toolCall?.status === 'complete';
  const displayName = getToolDisplayName(toolCall?.toolName ?? 'unknown');
  
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 py-1 pl-8 text-sm text-surface-500 hover:text-surface-300 transition-colors cursor-pointer group"
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
      <span className="text-surface-500 italic group-hover:text-surface-300">
        {isComplete ? `Queried ${displayName}` : `Querying ${displayName}...`}
      </span>
      <svg className="w-3 h-3 text-surface-600 opacity-0 group-hover:opacity-100 transition-opacity" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    </button>
  );
}

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
        <svg className="w-3 h-3 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
        </svg>
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
          <svg
            className="w-10 h-10 text-primary-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
            />
          </svg>
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
