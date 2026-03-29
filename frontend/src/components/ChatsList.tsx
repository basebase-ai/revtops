/**
 * Full-page chats list view with search, infinite scroll, and paginated API loading.
 *
 * Accessible via "View all" in the sidebar chat sections.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChatSummary } from '../store/types';
import { useActiveTasksByConversation, useAppStore, useChatStore } from '../store';
import { listConversations, type ConversationSummary } from '../api/client';
import { Avatar } from './Avatar';

interface ChatsListProps {
  chats: ChatSummary[];
  onSelectChat: (id: string, searchTerm?: string, matchCount?: number) => void;
  onNewChat: () => void;
}

const PAGE_SIZE = 30;

function apiConvToChatSummary(conv: ConversationSummary): ChatSummary {
  return {
    id: conv.id,
    title: conv.title ?? 'New Chat',
    lastMessageAt: new Date(conv.updated_at),
    previewText: conv.last_message_preview ?? '',
    scope: conv.scope ?? 'shared',
    userId: conv.user_id ?? undefined,
    participants: conv.participants?.map((p) => ({
      id: p.id,
      name: p.name,
      email: p.email,
      avatarUrl: p.avatar_url,
    })),
    matchSnippet: conv.match_snippet,
    matchCount: conv.match_count ?? 0,
  };
}

/** Highlight search term in text by wrapping matches in <mark>. */
function HighlightText({ text, term }: { text: string; term: string }): JSX.Element {
  if (!term.trim()) return <>{text}</>;
  const regex = new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  const parts = text.split(regex);
  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <mark key={i} className="bg-amber-300 text-black rounded-sm px-0.5 font-semibold">{part}</mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

type ScopeFilter = 'all' | 'shared' | 'private' | 'mine';

export function ChatsList({ chats: sidebarChats, onSelectChat, onNewChat }: ChatsListProps): JSX.Element {
  const storedSearchTerm = useChatStore((s) => s.chatSearchTerm);
  const [searchQuery, setSearchQuery] = useState<string>(storedSearchTerm ?? '');
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>('all');
  const [allChats, setAllChats] = useState<ChatSummary[]>([]);
  const [isLoadingMore, setIsLoadingMore] = useState<boolean>(false);
  const [hasMore, setHasMore] = useState<boolean>(true);
  const [initialLoaded, setInitialLoaded] = useState<boolean>(false);
  const offsetRef = useRef<number>(0);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);

  const activeTasksByConversation = useActiveTasksByConversation();
  const pinnedChatIds = useAppStore((state) => state.pinnedChatIds);
  const togglePinChat = useAppStore((state) => state.togglePinChat);
  const currentUserId = useAppStore((state) => state.user?.id);

  const [committedSearch, setCommittedSearch] = useState<string>(storedSearchTerm ?? '');
  const searchVersionRef = useRef<number>(0);

  const handleSearchSubmit = useCallback(() => {
    setCommittedSearch(searchQuery.trim());
  }, [searchQuery]);

  const handleSearchClear = useCallback(() => {
    setSearchQuery('');
    setCommittedSearch('');
    useChatStore.setState({ chatSearchTerm: null, chatSearchMatchCount: 0 });
  }, []);

  const loadPage = useCallback(async (reset: boolean = false): Promise<void> => {
    if (isLoadingMore && !reset) return;
    setIsLoadingMore(true);
    if (reset) {
      setAllChats([]); // Clear immediately so stale results don't linger
      offsetRef.current = 0;
    }
    const version = ++searchVersionRef.current;
    const offset = reset ? 0 : offsetRef.current;
    const apiScope = scopeFilter === 'all' ? undefined : scopeFilter;
    try {
      const { data, error } = await listConversations(PAGE_SIZE, offset, apiScope, committedSearch);
      // Discard response if a newer search has started
      if (version !== searchVersionRef.current) return;
      if (error || !data) {
        setHasMore(false);
        return;
      }
      const mapped: ChatSummary[] = data.conversations.map(apiConvToChatSummary);
      if (reset) {
        setAllChats(mapped);
        offsetRef.current = mapped.length;
      } else {
        setAllChats((prev) => {
          const existingIds = new Set(prev.map((c) => c.id));
          const deduped = mapped.filter((c) => !existingIds.has(c.id));
          return [...prev, ...deduped];
        });
        offsetRef.current = offset + mapped.length;
      }
      setHasMore(mapped.length >= PAGE_SIZE);
    } finally {
      setIsLoadingMore(false);
      setInitialLoaded(true);
    }
  }, [isLoadingMore, scopeFilter, committedSearch]);

  // Initial load + reload on filter/search change
  useEffect(() => {
    void loadPage(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeFilter, committedSearch]);

  // Infinite scroll via IntersectionObserver
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasMore && !isLoadingMore) {
          void loadPage(false);
        }
      },
      { rootMargin: '200px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, isLoadingMore, loadPage]);

  // Merge sidebar chats with loaded chats (skip sidebar merge when searching
  // since sidebar chats aren't filtered by the search query)
  const isSearching: boolean = committedSearch.trim().length > 0;
  const mergedChats = useMemo((): ChatSummary[] => {
    if (isSearching) {
      return allChats.sort(
        (a, b) => b.lastMessageAt.getTime() - a.lastMessageAt.getTime(),
      );
    }
    const byId = new Map<string, ChatSummary>();
    for (const c of allChats) byId.set(c.id, c);
    for (const c of sidebarChats) byId.set(c.id, c);
    return Array.from(byId.values()).sort(
      (a, b) => b.lastMessageAt.getTime() - a.lastMessageAt.getTime(),
    );
  }, [allChats, sidebarChats, isSearching]);

  // Client-side scope filter for sidebar-sourced chats (API results already filtered)
  const filteredChats = useMemo((): ChatSummary[] => {
    if (isSearching) return mergedChats; // API already filtered

    let result = mergedChats;

    if (scopeFilter === 'shared') {
      result = result.filter((c) => c.scope === 'shared');
    } else if (scopeFilter === 'private') {
      result = result.filter((c) => c.scope === 'private');
    } else if (scopeFilter === 'mine') {
      result = result.filter((c) => c.userId === currentUserId);
    }

    return result;
  }, [mergedChats, scopeFilter, currentUserId, isSearching]);

  // Pinned first
  const orderedChats = useMemo((): ChatSummary[] => {
    if (pinnedChatIds.length === 0) return filteredChats;
    const pinnedSet = new Set(pinnedChatIds);
    const pinned = filteredChats.filter((c) => pinnedSet.has(c.id));
    const unpinned = filteredChats.filter((c) => !pinnedSet.has(c.id));
    return [...pinned, ...unpinned];
  }, [filteredChats, pinnedChatIds]);

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Header */}
      <header className="flex-shrink-0 border-b border-surface-800 px-6 md:px-8 py-5">
        <div className="flex items-center justify-between max-w-4xl mx-auto">
          <div>
            <h1 className="text-2xl font-bold text-surface-50">All Chats</h1>
            {initialLoaded && (
              <p className="text-surface-400 text-sm mt-0.5">
                {mergedChats.length} conversation{mergedChats.length !== 1 ? 's' : ''}
                {hasMore ? '+' : ''}
              </p>
            )}
          </div>
          <button onClick={onNewChat} className="btn-primary flex items-center gap-2">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Chat
          </button>
        </div>

        {/* Filters + Search */}
        <div className="max-w-4xl mx-auto mt-4 flex flex-col gap-3">
          <div className="flex items-center gap-1 rounded-lg border border-surface-700 p-0.5 w-fit bg-surface-900">
            {(['all', 'shared', 'private', 'mine'] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setScopeFilter(f)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  scopeFilter === f
                    ? 'bg-surface-700 text-surface-100'
                    : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'
                }`}
              >
                {f === 'all' ? 'All' : f === 'shared' ? 'Shared' : f === 'private' ? 'Private' : 'Mine'}
              </button>
            ))}
          </div>
          <div className="relative">
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
              placeholder="Search conversations... (press Enter)"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSearchSubmit();
                if (e.key === 'Escape') handleSearchClear();
              }}
              className="input-field pl-10 pr-8 w-full"
              autoFocus
            />
            {searchQuery && (
              <button
                type="button"
                onClick={handleSearchClear}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-surface-500 hover:text-surface-200"
                title="Clear search"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Scrollable list */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-6 md:px-8 py-4">
          {(!initialLoaded || (isLoadingMore && allChats.length === 0)) ? (
            <div className="space-y-3">
              {Array.from({ length: 8 }, (_, i) => (
                <div key={i} className="p-4 rounded-xl bg-surface-900 border border-surface-800 animate-pulse">
                  <div className="flex items-start gap-4">
                    <div className="w-5 h-5 rounded bg-surface-800 flex-shrink-0" />
                    <div className="flex-1 space-y-2">
                      <div className="h-4 rounded bg-surface-800 w-2/3" />
                      <div className="h-3 rounded bg-surface-800 w-1/2" />
                    </div>
                    <div className="h-3 rounded bg-surface-800 w-16" />
                  </div>
                </div>
              ))}
            </div>
          ) : orderedChats.length === 0 ? (
            <div className="text-center py-16">
              {searchQuery ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                    <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                  </div>
                  <h3 className="text-surface-200 font-medium mb-2">No matching chats</h3>
                  <p className="text-surface-400 text-sm">Try a different search term</p>
                </>
              ) : (
                <>
                  <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                    <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                    </svg>
                  </div>
                  <h3 className="text-surface-200 font-medium mb-2">No conversations yet</h3>
                  <p className="text-surface-400 text-sm mb-4">Start your first conversation</p>
                  <button onClick={onNewChat} className="btn-primary">Start a conversation</button>
                </>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              {orderedChats.map((chat) => (
                <ChatRow
                  key={chat.id}
                  chat={chat}
                  searchTerm={committedSearch}
                  hasActiveTask={chat.id in activeTasksByConversation}
                  isPinned={pinnedChatIds.includes(chat.id)}
                  onSelect={onSelectChat}
                  onTogglePin={togglePinChat}
                />
              ))}

              {/* Infinite scroll sentinel */}
              <div ref={sentinelRef} className="h-1" />

              {isLoadingMore && (
                <div className="flex justify-center py-4">
                  <div className="w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat row
// ---------------------------------------------------------------------------

function ChatRow({
  chat,
  searchTerm,
  hasActiveTask,
  isPinned,
  onSelect,
  onTogglePin,
}: {
  chat: ChatSummary;
  searchTerm: string;
  hasActiveTask: boolean;
  isPinned: boolean;
  onSelect: (id: string, searchTerm?: string, matchCount?: number) => void;
  onTogglePin: (id: string) => void;
}): JSX.Element {
  const isSearching = searchTerm.trim().length > 0;
  return (
    <button
      onClick={() => onSelect(chat.id, isSearching ? searchTerm : undefined, isSearching ? chat.matchCount : undefined)}
      className="relative w-full text-left p-4 rounded-xl bg-surface-900 hover:bg-surface-800 border border-surface-800 hover:border-surface-700 transition-colors group"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          <div className="flex-shrink-0 mt-0.5">
            {chat.scope === 'private' ? (
              <svg className="w-5 h-5 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            ) : (
              <svg className="w-5 h-5 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
            )}
          </div>
          <div className="flex items-center gap-2">
            <h3 className="font-medium text-surface-100 truncate group-hover:text-white transition-colors">
              {isSearching ? <HighlightText text={chat.title} term={searchTerm} /> : chat.title}
            </h3>
            {chat.type === 'workflow' && (
              <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-xs bg-amber-500/20 text-amber-400">Workflow</span>
            )}
            {hasActiveTask && (
              <span className="flex-shrink-0 flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-primary-500/20 text-primary-400 text-xs">
                <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Active
              </span>
            )}
            <div className={`ml-auto min-w-[9rem] flex-shrink-0 flex items-center gap-2 px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wide ${
              chat.scope === 'shared'
                ? 'bg-primary-500/20 text-primary-400'
                : 'bg-surface-700 text-surface-400'
            }`}>
              <span>{chat.scope}</span>
              <span className="ml-auto normal-case tracking-normal text-xs text-surface-300 whitespace-nowrap">
                {formatDate(chat.lastMessageAt)}
              </span>
            </div>
            {isSearching && (chat.matchCount ?? 0) > 0 && (
              <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-300 text-black">
                {chat.matchCount} {chat.matchCount === 1 ? 'match' : 'matches'}
              </span>
            )}
          </div>
          <div className="flex min-h-[1.25rem] items-center">
            {chat.participants && chat.participants.length > 0 && (
              <div className="flex -space-x-1.5">
                {chat.participants.slice(0, 3).map((p, idx) => (
                  <Avatar key={p.id} user={p} size="xs" bordered style={{ zIndex: 3 - idx }} />
                ))}
                {chat.participants.length > 3 && (
                  <div className="w-5 h-5 rounded-full border border-surface-700 dark:border-surface-600 bg-surface-700 flex items-center justify-center text-[10px] font-medium text-surface-300">
                    +{chat.participants.length - 3}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 min-w-0">
            <p className="text-sm text-surface-400 truncate">
              {isSearching && chat.matchSnippet ? (
                <HighlightText text={chat.matchSnippet} term={searchTerm} />
              ) : isSearching ? (
                <HighlightText text={chat.previewText} term={searchTerm} />
              ) : (
                chat.previewText
              )}
            </p>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          <button
            onClick={(e) => { e.stopPropagation(); onTogglePin(chat.id); }}
            className={`p-1.5 rounded ${
              isPinned
                ? 'opacity-100 text-primary-400'
                : 'opacity-100 md:opacity-0 text-surface-500'
            } md:group-hover:opacity-100 hover:bg-surface-700 hover:text-surface-200 transition-all`}
            title={isPinned ? 'Unpin conversation' : 'Pin conversation'}
          >
            <svg className={`w-4 h-4 ${isPinned ? 'text-primary-400' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
            </svg>
          </button>
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
