/**
 * Chats list view showing all historical conversations.
 * 
 * Features:
 * - Search/filter chats
 * - Sort by date
 * - Delete chats
 * - Click to resume conversation
 * - Visual indicators for chats with active background tasks
 */

import { useState } from 'react';
import type { ChatSummary } from './AppLayout';
import { useActiveTasksByConversation } from '../store';

interface ChatsListProps {
  chats: ChatSummary[];
  onSelectChat: (id: string) => void;
  onNewChat: () => void;
}

export function ChatsList({ chats, onSelectChat, onNewChat }: ChatsListProps): JSX.Element {
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<'recent' | 'oldest'>('recent');
  const activeTasksByConversation = useActiveTasksByConversation();

  const filteredChats = chats
    .filter((chat) =>
      chat.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      chat.previewText.toLowerCase().includes(searchQuery.toLowerCase())
    )
    .sort((a, b) => {
      if (sortBy === 'recent') {
        return b.lastMessageAt.getTime() - a.lastMessageAt.getTime();
      }
      return a.lastMessageAt.getTime() - b.lastMessageAt.getTime();
    });

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Header */}
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-surface-50">Chats</h1>
            <p className="text-surface-400 mt-1">
              {chats.length} conversation{chats.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button
            onClick={onNewChat}
            className="btn-primary flex items-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Chat
          </button>
        </div>

        {/* Search & Filter */}
        <div className="flex items-center gap-4 mt-4">
          <div className="relative flex-1">
            <svg
              className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-surface-500"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="text"
              placeholder="Search conversations..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="input-field pl-10"
            />
          </div>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as 'recent' | 'oldest')}
            className="input-field w-auto"
          >
            <option value="recent">Most Recent</option>
            <option value="oldest">Oldest First</option>
          </select>
        </div>
      </header>

      {/* Chats List */}
      <div className="max-w-4xl mx-auto px-8 py-6">
        {filteredChats.length === 0 ? (
          <div className="text-center py-16">
            {searchQuery ? (
              <>
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                </div>
                <h3 className="text-surface-200 font-medium mb-2">No matching chats</h3>
                <p className="text-surface-400 text-sm">
                  Try a different search term
                </p>
              </>
            ) : (
              <>
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                  </svg>
                </div>
                <h3 className="text-surface-200 font-medium mb-2">No conversations yet</h3>
                <p className="text-surface-400 text-sm mb-4">
                  Start your first conversation to get insights from your data
                </p>
                <button onClick={onNewChat} className="btn-primary">
                  Start a conversation
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {filteredChats.map((chat) => {
              const hasActiveTask = chat.id in activeTasksByConversation;
              return (
                <button
                  key={chat.id}
                  onClick={() => onSelectChat(chat.id)}
                  className="w-full text-left p-4 rounded-xl bg-surface-900 hover:bg-surface-800 border border-surface-800 hover:border-surface-700 transition-colors group"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <h3 className="font-medium text-surface-100 truncate group-hover:text-white transition-colors">
                          {chat.title}
                        </h3>
                        {hasActiveTask && (
                          <span className="flex-shrink-0 flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-primary-500/20 text-primary-400 text-xs">
                            <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                            Active
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-surface-400 truncate mt-1">
                        {chat.previewText}
                      </p>
                    </div>
                    <div className="text-xs text-surface-500 whitespace-nowrap">
                      {formatDate(chat.lastMessageAt)}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function formatDate(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  if (diffDays === 1) {
    return 'Yesterday';
  }
  if (diffDays < 7) {
    return date.toLocaleDateString([], { weekday: 'long' });
  }
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}
