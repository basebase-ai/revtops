/**
 * Chat interface component.
 *
 * Features:
 * - WebSocket connection to backend with conversation support
 * - Message history display
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
import { useAppStore } from '../store';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  toolName?: string; // For tool messages
}

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

type WsControlMessage = WsConversationCreated | WsMessageComplete;

function isControlMessage(data: unknown): data is WsControlMessage {
  return typeof data === 'object' && data !== null && 'type' in data;
}

export function Chat({ userId, organizationId: _organizationId, chatId }: ChatProps): JSX.Element {
  // Get store action (stable reference)
  const addConversation = useAppStore((state) => state.addConversation);
  
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>('');
  const [currentArtifact, setCurrentArtifact] = useState<Artifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isThinking, setIsThinking] = useState<boolean>(false);
  const [chatTitle, setChatTitle] = useState<string>('New Chat');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const streamingMessageIdRef = useRef<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const notifiedConversationRef = useRef<string | null>(null);
  const pendingTitleRef = useRef<string | null>(null);

  // Store addConversation in ref so callback can access latest version
  const addConversationRef = useRef(addConversation);
  useEffect(() => {
    addConversationRef.current = addConversation;
  }, [addConversation]);

  // Handle WebSocket message - called directly from WebSocket event handler
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
          conversationIdRef.current = parsed.conversation_id;
          notifiedConversationRef.current = parsed.conversation_id;
          
          // Use pending title or generate from ref
          const title = pendingTitleRef.current ?? 'New Chat';
          setChatTitle(title);
          
          // Add to store via ref (stable reference)
          addConversationRef.current(parsed.conversation_id, title);
          pendingTitleRef.current = null;
          return;
        }
        if (parsed.type === 'message_complete') {
          console.log('[Chat] Message complete');
          // Mark streaming message as complete
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === streamingMessageIdRef.current
                ? { ...msg, isStreaming: false }
                : msg
            )
          );
          streamingMessageIdRef.current = null;
          return;
        }
      }
    } catch {
      // Not JSON, treat as text chunk
    }

    // Check if this is a tool call indicator (e.g., "*Querying query_deals...*")
    const trimmed = message.trim();
    const toolCallMatch = trimmed.match(/^\*([^*]+)\.\.\.\*$/);
    if (toolCallMatch) {
      const toolAction = toolCallMatch[1];
      console.log('[Chat] Tool call detected:', toolAction);
      
      // Extract tool name
      const toolNameMatch = toolAction.match(/(?:Querying|Calling|Running|Using)\s+(\w+)/i);
      const toolName = toolNameMatch ? toolNameMatch[1] : toolAction;
      
      // Add as a tool message
      setMessages((prev) => [
        ...prev,
        {
          id: `tool-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: 'tool' as const,
          content: toolAction,
          toolName,
          timestamp: new Date(),
        },
      ]);
      setIsThinking(false);
      return;
    }

    // Text chunk from assistant
    console.log('[Chat] Received text chunk:', message.substring(0, 30) + '...');
    setIsThinking(false);

    setMessages((prev) => {
      if (streamingMessageIdRef.current) {
        // Append to existing streaming message
        return prev.map((msg) => {
          if (msg.id === streamingMessageIdRef.current) {
            return { ...msg, content: msg.content + message };
          }
          return msg;
        });
      } else {
        // First chunk - create new assistant message
        const newId = `assistant-${Date.now()}`;
        streamingMessageIdRef.current = newId;
        console.log('[Chat] Creating new assistant message:', newId);
        return [
          ...prev,
          {
            id: newId,
            role: 'assistant' as const,
            content: message,
            timestamp: new Date(),
            isStreaming: true,
          },
        ];
      }
    });
  }, []);

  const { sendMessage, isConnected, connectionState } = useWebSocket(
    `/ws/chat/${userId}`,
    { onMessage: handleWebSocketMessage }
  );

  // Reset state when chatId becomes null (New Chat clicked)
  useEffect(() => {
    if (chatId === null) {
      console.log('[Chat] New chat started, clearing state');
      setMessages([]);
      setChatTitle('New Chat');
      conversationIdRef.current = null;
      streamingMessageIdRef.current = null;
      notifiedConversationRef.current = null;
      setIsThinking(false);
    }
  }, [chatId]);

  // Load conversation when selecting an existing chat from sidebar
  useEffect(() => {
    // If no chatId, this is a new chat - ensure loading is false
    if (!chatId) {
      setIsLoading(false);
      return;
    }

    // If chatId matches our current conversation, don't reload
    if (chatId === conversationIdRef.current) {
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
        
        // Don't update state if the effect was cancelled (chatId changed)
        if (cancelled) {
          console.log('[Chat] Load cancelled - chatId changed');
          return;
        }

        if (data && !error) {
          conversationIdRef.current = chatId;
          setChatTitle(data.title ?? 'New Chat');
          
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
          // Show empty state on error
          setMessages([]);
          setChatTitle('Chat');
        }
      } catch (err) {
        console.error('[Chat] Exception loading conversation:', err);
        if (!cancelled) {
          setMessages([]);
          setChatTitle('Chat');
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
  }, [chatId, userId]);

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
    if (!conversationIdRef.current) {
      pendingTitleRef.current = generateTitle(input);
    }

    setMessages((prev) => [...prev, userMessage]);
    streamingMessageIdRef.current = null;
    setIsThinking(true);

    // Send message with conversation context
    const payload = JSON.stringify({
      message: input,
      conversation_id: conversationIdRef.current,
    });
    console.log('[Chat] Sending to WebSocket:', payload);
    sendMessage(payload);
    setInput('');
  }, [input, isConnected, sendMessage]);

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
                  <ToolCallIndicator key={msg.id} action={msg.content} />
                ) : (
                  <Message
                    key={msg.id}
                    message={msg}
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
              className="input-field resize-none min-h-[52px] max-h-32"
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
    </div>
  );
}

/**
 * Tool call indicator - shows as a small, light note without speech bubble
 */
function ToolCallIndicator({ action }: { action: string }): JSX.Element {
  return (
    <div className="flex items-center gap-2 py-1 pl-11 text-sm text-surface-500">
      <svg className="w-3.5 h-3.5 text-surface-600 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
      </svg>
      <span className="text-surface-500 italic">{action}</span>
    </div>
  );
}

/**
 * Thinking indicator - shows while waiting for assistant response
 */
function ThinkingIndicator(): JSX.Element {
  return (
    <div className="flex gap-4">
      {/* Avatar */}
      <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center flex-shrink-0">
        <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
        </svg>
      </div>

      {/* Thinking dots */}
      <div className="bg-surface-800/50 rounded-2xl rounded-tl-sm px-4 py-3">
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 bg-surface-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <div className="w-2 h-2 bg-surface-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <div className="w-2 h-2 bg-surface-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
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
