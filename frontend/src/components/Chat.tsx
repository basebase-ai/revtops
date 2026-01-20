/**
 * Main chat interface component.
 *
 * Features:
 * - WebSocket connection to backend
 * - Message history display
 * - Input for user messages
 * - Streaming response display
 * - Artifact viewer for dashboards/reports
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import { Message } from './Message';
import { ArtifactViewer } from './ArtifactViewer';
import { IntegrationsPanel } from './IntegrationsPanel';
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
  customerId?: string;
}

export function Chat({ userId, customerId }: ChatProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>('');
  const [currentArtifact, setCurrentArtifact] = useState<Artifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [showIntegrations, setShowIntegrations] = useState<boolean>(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const streamingMessageIdRef = useRef<string | null>(null);

  const { sendMessage, lastMessage, isConnected, connectionState } = useWebSocket(
    `/ws/chat/${userId}`
  );

  // Load chat history on mount
  useEffect(() => {
    const loadHistory = async (): Promise<void> => {
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
      }
      setIsLoading(false);
    };

    void loadHistory();
  }, []);

  // Handle incoming WebSocket messages
  useEffect(() => {
    if (!lastMessage) return;

    setMessages((prev) => {
      // Check if we're currently streaming
      if (streamingMessageIdRef.current) {
        // Find and update the streaming message
        return prev.map((msg) => {
          if (msg.id === streamingMessageIdRef.current) {
            return { ...msg, content: msg.content + lastMessage };
          }
          return msg;
        });
      } else {
        // Start a new assistant message
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

    // Add user message
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: input,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);

    // Reset streaming state for new response
    streamingMessageIdRef.current = null;

    // Send to backend
    sendMessage(input);

    // Clear input
    setInput('');
  }, [input, isConnected, sendMessage]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleLogout = (): void => {
    localStorage.removeItem('user_id');
    window.location.reload();
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-surface-400">Loading chat history...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex">
      {/* Sidebar for artifacts */}
      {currentArtifact && (
        <div className="w-96 border-r border-surface-800 bg-surface-900 p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-surface-100">
              {currentArtifact.title}
            </h2>
            <button
              onClick={() => setCurrentArtifact(null)}
              className="text-surface-400 hover:text-surface-200"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </div>
          <ArtifactViewer artifact={currentArtifact} />
        </div>
      )}

      {/* Main chat area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <header className="h-16 border-b border-surface-800 flex items-center justify-between px-6">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <svg
                className="w-4 h-4 text-white"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
                />
              </svg>
            </div>
            <h1 className="text-lg font-semibold text-surface-100">Revenue Copilot</h1>
          </div>

          <div className="flex items-center gap-4">
            <ConnectionStatus state={connectionState} />
            <button
              onClick={() => setShowIntegrations(true)}
              className="text-surface-400 hover:text-surface-200 text-sm flex items-center gap-1"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101" />
              </svg>
              Integrations
            </button>
            <button
              onClick={handleLogout}
              className="text-surface-400 hover:text-surface-200 text-sm"
            >
              Logout
            </button>
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-6">
          {messages.length === 0 ? (
            <EmptyState />
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

      {/* Integrations Panel */}
      {showIntegrations && customerId && (
        <IntegrationsPanel
          customerId={customerId}
          onClose={() => setShowIntegrations(false)}
        />
      )}
    </div>
  );
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

function EmptyState(): JSX.Element {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center max-w-md">
        <div className="w-16 h-16 rounded-2xl bg-surface-800 flex items-center justify-center mx-auto mb-6">
          <svg
            className="w-8 h-8 text-surface-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
            />
          </svg>
        </div>
        <h2 className="text-xl font-semibold text-surface-100 mb-2">
          Start a conversation
        </h2>
        <p className="text-surface-400 mb-6">
          Ask questions about your sales pipeline, deals, or accounts.
        </p>
        <div className="space-y-2 text-left">
          <SuggestionChip text="What deals are closing this month?" />
          <SuggestionChip text="Show me my pipeline by stage" />
          <SuggestionChip text="Which accounts need attention?" />
        </div>
      </div>
    </div>
  );
}

function SuggestionChip({ text }: { text: string }): JSX.Element {
  return (
    <button className="w-full text-left px-4 py-3 rounded-lg bg-surface-800 hover:bg-surface-700 text-surface-300 text-sm transition-colors">
      {text}
    </button>
  );
}
