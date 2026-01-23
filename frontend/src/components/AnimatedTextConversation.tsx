/**
 * AnimatedTextConversation component.
 *
 * Displays an animated chat conversation that reveals messages one by one
 * with a scrolling effect.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Message {
  sender: string;
  content: string;
}

interface ConversationData {
  messages: Message[];
}

interface AnimatedTextConversationProps {
  conversation: ConversationData;
  messageDelayMs?: number;
  autoStart?: boolean;
}

export function AnimatedTextConversation({
  conversation,
  messageDelayMs = 3000,
  autoStart = true,
}: AnimatedTextConversationProps): JSX.Element {
  const [visibleMessages, setVisibleMessages] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(autoStart);
  const containerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback((): void => {
    if (containerRef.current) {
      containerRef.current.scrollTo({
        top: containerRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, []);

  useEffect(() => {
    if (!isPlaying) return;

    if (visibleMessages >= conversation.messages.length) {
      // Reset and loop
      const resetTimer = setTimeout(() => {
        setVisibleMessages(0);
      }, 5000);
      return () => clearTimeout(resetTimer);
    }

    const timer = setTimeout(() => {
      setVisibleMessages((prev) => prev + 1);
    }, visibleMessages === 0 ? 500 : messageDelayMs);

    return () => clearTimeout(timer);
  }, [visibleMessages, isPlaying, conversation.messages.length, messageDelayMs]);

  useEffect(() => {
    scrollToBottom();
  }, [visibleMessages, scrollToBottom]);

  const isAgent = (sender: string): boolean => {
    return sender.toLowerCase().includes('agent') || sender.toLowerCase().includes('revtops');
  };

  return (
    <div className="relative w-full max-w-4xl mx-auto text-left">
      {/* Chat window frame */}
      <div className="rounded-xl border border-surface-800 bg-surface-900/50 backdrop-blur-sm overflow-hidden shadow-2xl">
        {/* Window header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-800 bg-surface-900/80">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-red-500" />
            <div className="w-3 h-3 rounded-full bg-yellow-500" />
            <div className="w-3 h-3 rounded-full bg-green-500" />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-surface-500">Live Demo</span>
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
          </div>
        </div>

        {/* Messages container with scroll */}
        <div
          ref={containerRef}
          className="h-[400px] overflow-y-auto p-4 space-y-4 scroll-smooth"
          style={{
            scrollbarWidth: 'thin',
            scrollbarColor: 'rgb(63 63 70) transparent',
          }}
        >
          {conversation.messages.slice(0, visibleMessages).map((message, index) => {
            const agent = isAgent(message.sender);
            return (
              <div
                key={index}
                className={`flex gap-3 animate-fade-in-up ${agent ? 'flex-row justify-start' : 'flex-row-reverse justify-start'}`}
              >
                {/* Avatar */}
                <div
                  className={`w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center ${
                    agent
                      ? 'bg-gradient-to-br from-primary-500 to-primary-700'
                      : 'bg-surface-700'
                  }`}
                >
                  {agent ? (
                    <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                  )}
                </div>

                {/* Message bubble */}
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                    agent
                      ? 'bg-primary-600/20 border border-primary-500/30 rounded-tl-sm'
                      : 'bg-surface-800 rounded-tr-sm'
                  }`}
                >
                  {/* Sender label */}
                  <div className={`text-xs font-medium mb-1.5 ${agent ? 'text-primary-400' : 'text-surface-500'}`}>
                    {message.sender}
                  </div>

                  {/* Message content with markdown */}
                  <div className="text-surface-200 text-sm prose prose-invert prose-sm max-w-none text-left prose-table:text-xs prose-th:text-surface-300 prose-th:text-left prose-td:text-surface-400 prose-td:text-left prose-th:p-2 prose-td:p-2 prose-table:border-collapse prose-th:border prose-th:border-surface-700 prose-td:border prose-td:border-surface-700 prose-strong:text-surface-100 prose-headings:text-surface-100 prose-headings:text-left prose-headings:mt-3 prose-headings:mb-2 prose-p:my-1.5 prose-p:text-left prose-ul:my-1.5 prose-ul:text-left prose-ol:my-1.5 prose-ol:text-left prose-li:my-0.5 prose-li:text-left prose-hr:my-3 prose-hr:border-surface-700 prose-blockquote:border-primary-500 prose-blockquote:text-surface-400 prose-blockquote:bg-surface-800/50 prose-blockquote:py-2 prose-blockquote:px-3 prose-blockquote:rounded prose-blockquote:text-left">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {message.content}
                    </ReactMarkdown>
                  </div>
                </div>
              </div>
            );
          })}

          {/* Typing indicator when loading next message */}
          {isPlaying && visibleMessages < conversation.messages.length && visibleMessages > 0 && (
            <div className="flex gap-3 animate-fade-in">
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-500 to-primary-700 flex-shrink-0 flex items-center justify-center">
                <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                </svg>
              </div>
              <div className="bg-primary-600/20 border border-primary-500/30 rounded-2xl rounded-tl-sm px-4 py-3">
                <div className="flex gap-1.5">
                  <div className="w-2 h-2 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                  <div className="w-2 h-2 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                  <div className="w-2 h-2 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Control bar */}
        <div className="flex items-center justify-between px-4 py-2 border-t border-surface-800 bg-surface-900/60">
          <button
            onClick={() => setIsPlaying(!isPlaying)}
            className="flex items-center gap-2 text-xs text-surface-400 hover:text-surface-200 transition-colors"
          >
            {isPlaying ? (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                Pause
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                Play
              </>
            )}
          </button>
          <div className="text-xs text-surface-500">
            {visibleMessages} / {conversation.messages.length} messages
          </div>
          <button
            onClick={() => {
              setVisibleMessages(0);
              setIsPlaying(true);
            }}
            className="flex items-center gap-2 text-xs text-surface-400 hover:text-surface-200 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Restart
          </button>
        </div>
      </div>
    </div>
  );
}
