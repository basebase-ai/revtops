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

interface Integration {
  id: string;
  name: string;
  connected: boolean;
  icon: JSX.Element;
}

interface ChatProps {
  userId: string;
  organizationId?: string;
  onLogout: () => void;
}

export function Chat({ userId, organizationId, onLogout }: ChatProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>('');
  const [currentArtifact, setCurrentArtifact] = useState<Artifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const streamingMessageIdRef = useRef<string | null>(null);

  // Mock integrations state - in production, fetch from API
  const [integrations] = useState<Integration[]>([
    {
      id: 'hubspot',
      name: 'HubSpot',
      connected: true,
      icon: (
        <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M18.164 7.93V5.307a2.31 2.31 0 001.378-2.116 2.31 2.31 0 00-4.622 0c0 .953.588 1.775 1.416 2.116V7.93a6.144 6.144 0 00-3.398 1.606l-6.627-5.148A2.602 2.602 0 006.307 4.2a2.602 2.602 0 00-2.602-2.602 2.602 2.602 0 000 5.204c.497 0 .959-.144 1.354-.383l6.533 5.075a6.093 6.093 0 00-.702 2.863c0 1.024.255 1.988.702 2.837l-2.705 2.704a2.076 2.076 0 00-1.258-.428 2.077 2.077 0 100 4.153c1.147 0 2.078-.93 2.078-2.076 0-.461-.152-.886-.409-1.23l2.664-2.664a6.144 6.144 0 009.2-5.296 6.144 6.144 0 00-3.998-5.427z"/>
        </svg>
      ),
    },
    {
      id: 'slack',
      name: 'Slack',
      connected: true,
      icon: (
        <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M5.042 15.165a2.528 2.528 0 01-2.52 2.523A2.528 2.528 0 010 15.165a2.527 2.527 0 012.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 012.521-2.52 2.527 2.527 0 012.521 2.52v6.313A2.528 2.528 0 018.834 24a2.528 2.528 0 01-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 01-2.521-2.52A2.528 2.528 0 018.834 0a2.528 2.528 0 012.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 012.521 2.521 2.528 2.528 0 01-2.521 2.521H2.522A2.528 2.528 0 010 8.834a2.528 2.528 0 012.522-2.521h6.312zm10.122 2.521a2.528 2.528 0 012.522-2.521A2.528 2.528 0 0124 8.834a2.528 2.528 0 01-2.522 2.521h-2.522V8.834zm-1.268 0a2.528 2.528 0 01-2.523 2.521 2.527 2.527 0 01-2.52-2.521V2.522A2.527 2.527 0 0115.165 0a2.528 2.528 0 012.523 2.522v6.312zm-2.523 10.122a2.528 2.528 0 012.523 2.522A2.528 2.528 0 0115.165 24a2.527 2.527 0 01-2.52-2.522v-2.522h2.52zm0-1.268a2.527 2.527 0 01-2.52-2.523 2.526 2.526 0 012.52-2.52h6.313A2.527 2.527 0 0124 15.165a2.528 2.528 0 01-2.522 2.523h-6.313z"/>
        </svg>
      ),
    },
    {
      id: 'google_calendar',
      name: 'Calendar',
      connected: false,
      icon: (
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
        </svg>
      ),
    },
  ]);

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
    onLogout();
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
      {/* Left sidebar - Integrations */}
      <aside className="w-16 bg-surface-900 border-r border-surface-800 flex flex-col items-center py-4">
        {/* Logo */}
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center mb-6">
          <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
          </svg>
        </div>

        {/* Connected Integrations */}
        <div className="flex-1 flex flex-col gap-2">
          {integrations.map((integration) => (
            <button
              key={integration.id}
              className={`w-10 h-10 rounded-lg flex items-center justify-center transition-colors relative group ${
                integration.connected 
                  ? 'bg-surface-800 text-surface-200 hover:bg-surface-700' 
                  : 'bg-surface-800/50 text-surface-500 hover:bg-surface-800'
              }`}
              title={integration.name}
            >
              {integration.icon}
              {integration.connected && (
                <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-500 rounded-full border-2 border-surface-900" />
              )}
              {/* Tooltip */}
              <span className="absolute left-full ml-2 px-2 py-1 bg-surface-800 text-surface-200 text-xs rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap pointer-events-none">
                {integration.name}
                {!integration.connected && ' (not connected)'}
              </span>
            </button>
          ))}
          
          {/* Add integration button */}
          <button 
            className="w-10 h-10 rounded-lg bg-surface-800/50 text-surface-500 hover:bg-surface-800 hover:text-surface-300 flex items-center justify-center transition-colors"
            title="Add integration"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
          </button>
        </div>

        {/* Logout button at bottom */}
        <button
          onClick={handleLogout}
          className="w-10 h-10 rounded-lg text-surface-500 hover:bg-surface-800 hover:text-surface-300 flex items-center justify-center transition-colors"
          title="Logout"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
          </svg>
        </button>
      </aside>

      {/* Artifact sidebar */}
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
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <ArtifactViewer artifact={currentArtifact} />
        </div>
      )}

      {/* Main chat area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <header className="h-14 border-b border-surface-800 flex items-center justify-between px-6">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-surface-100">Revtops</h1>
          </div>

          <div className="flex items-center gap-4">
            <ConnectionStatus state={connectionState} />
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
