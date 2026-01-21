/**
 * Chat interface component.
 *
 * Features:
 * - WebSocket connection to backend
 * - Message history display
 * - Input for user messages
 * - Streaming response display
 * - Artifact viewer for dashboards/reports
 * - Auto-generates chat title from first message
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import { Message } from './Message';
import { ArtifactViewer } from './ArtifactViewer';
import type { ChatMessage as APIChatMessage } from '../api/client';
import { getChatHistory } from '../api/client';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
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
  onChatCreated?: (id: string, title: string) => void;
}

export function Chat({ userId, organizationId, chatId, onChatCreated }: ChatProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>('');
  const [currentArtifact, setCurrentArtifact] = useState<Artifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [chatTitle, setChatTitle] = useState<string>('New Chat');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const streamingMessageIdRef = useRef<string | null>(null);
  const hasCreatedChat = useRef<boolean>(false);

  const { sendMessage, lastMessage, isConnected, connectionState } = useWebSocket(
    `/ws/chat/${userId}`
  );

  // Load chat history
  useEffect(() => {
    const loadHistory = async (): Promise<void> => {
      setIsLoading(true);
      hasCreatedChat.current = false;

      if (chatId) {
        // Load existing chat
        const { data, error } = await getChatHistory();
        if (data && !error) {
          const loadedMessages: ChatMessage[] = data.messages.map(
            (msg: APIChatMessage) => ({
              id: msg.id,
              role: msg.role,
              content: msg.content,
              timestamp: new Date(msg.created_at),
            })
          );
          setMessages(loadedMessages);

          // Set title from first message
          if (loadedMessages.length > 0) {
            const firstUserMsg = loadedMessages.find((m) => m.role === 'user');
            if (firstUserMsg) {
              setChatTitle(generateTitle(firstUserMsg.content));
            }
          }
        }
      } else {
        // New chat
        setMessages([]);
        setChatTitle('New Chat');
      }
      setIsLoading(false);
    };

    void loadHistory();
  }, [chatId]);

  // Handle incoming WebSocket messages
  useEffect(() => {
    if (!lastMessage) return;

    setMessages((prev) => {
      if (streamingMessageIdRef.current) {
        return prev.map((msg) => {
          if (msg.id === streamingMessageIdRef.current) {
            return { ...msg, content: msg.content + lastMessage };
          }
          return msg;
        });
      } else {
        const newId = `assistant-${Date.now()}`;
        streamingMessageIdRef.current = newId;
        return [
          ...prev,
          {
            id: newId,
            role: 'assistant' as const,
            content: lastMessage,
            timestamp: new Date(),
            isStreaming: true,
          },
        ];
      }
    });
  }, [lastMessage]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = useCallback((): void => {
    if (!input.trim() || !isConnected) return;

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: input,
      timestamp: new Date(),
    };

    // If this is the first message, generate title and notify parent
    if (messages.length === 0 && !hasCreatedChat.current) {
      hasCreatedChat.current = true;
      const title = generateTitle(input);
      setChatTitle(title);

      // Generate a new chat ID
      const newChatId = `chat-${Date.now()}`;
      onChatCreated?.(newChatId, title);
    }

    setMessages((prev) => [...prev, userMessage]);
    streamingMessageIdRef.current = null;
    sendMessage(input);
    setInput('');
  }, [input, isConnected, sendMessage, messages.length, onChatCreated]);

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
      <div className="flex-1 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-surface-400">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col">
      {/* Header */}
      <header className="h-14 border-b border-surface-800 flex items-center justify-between px-6">
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
          {messages.length === 0 ? (
            <EmptyState onSuggestionClick={handleSuggestionClick} />
          ) : (
            <div className="max-w-3xl mx-auto space-y-6">
              {messages.map((msg) => (
                <Message
                  key={msg.id}
                  message={msg}
                  onArtifactClick={setCurrentArtifact}
                />
              ))}
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
              disabled={!isConnected}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || !isConnected}
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
 * Generate a chat title from the first message.
 * This is a simple client-side version; in production, use LLM.
 */
function generateTitle(message: string): string {
  // Clean and truncate the message
  const cleaned = message.trim().replace(/\n/g, ' ');

  // If it's a question, use it as-is (truncated)
  if (cleaned.endsWith('?') && cleaned.length <= 50) {
    return cleaned;
  }

  // Otherwise, create a summary
  const words = cleaned.split(' ').slice(0, 6);
  let title = words.join(' ');

  if (title.length > 40) {
    title = title.slice(0, 40);
  }

  // Add ellipsis if truncated
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
