/**
 * Full-screen detail view for a single workstream.
 * Shows title, description, chats sorted by recency, participants, and activity.
 */

import { useMemo } from "react";
import type { WorkstreamConversation, WorkstreamItem } from "../store/types";

interface WorkstreamDetailViewProps {
  workstream: WorkstreamItem;
  onBack: () => void;
  onSelectConversation: (conversationId: string) => void;
}

function timeAgo(iso: string): string {
  const diff: number = Date.now() - new Date(iso).getTime();
  const mins: number = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs: number = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days: number = Math.floor(hrs / 24);
  return `${days}d ago`;
}

interface AggregatedParticipant {
  id: string;
  name: string | null;
  avatar_url: string | null;
  chatCount: number;
}

function aggregateParticipants(
  ws: WorkstreamItem
): AggregatedParticipant[] {
  const map = new Map<string, AggregatedParticipant>();
  for (const conv of ws.conversations) {
    const seen = new Set<string>();
    for (const p of conv.participants) {
      if (seen.has(p.id)) continue;
      seen.add(p.id);
      const existing: AggregatedParticipant | undefined = map.get(p.id);
      if (existing) {
        existing.chatCount += 1;
      } else {
        map.set(p.id, {
          id: p.id,
          name: p.name,
          avatar_url: p.avatar_url,
          chatCount: 1,
        });
      }
    }
  }
  return Array.from(map.values()).sort((a, b) => b.chatCount - a.chatCount);
}

export function WorkstreamDetailView({
  workstream,
  onBack,
  onSelectConversation,
}: WorkstreamDetailViewProps): JSX.Element {
  const sortedChats: WorkstreamConversation[] = useMemo(
    () =>
      [...workstream.conversations].sort((a, b) => {
        const aTime: number = a.last_message_at
          ? new Date(a.last_message_at).getTime()
          : 0;
        const bTime: number = b.last_message_at
          ? new Date(b.last_message_at).getTime()
          : 0;
        return bTime - aTime;
      }),
    [workstream.conversations]
  );

  const participants: AggregatedParticipant[] = useMemo(
    () => aggregateParticipants(workstream),
    [workstream]
  );

  const totalMessages: number = workstream.conversations.reduce(
    (sum, c) => sum + (c.messages_in_window ?? 0),
    0
  );

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-surface-700">
        <button
          type="button"
          onClick={onBack}
          className="p-1.5 rounded-lg hover:bg-surface-700 transition-colors text-surface-400 hover:text-surface-200"
        >
          <svg
            className="w-5 h-5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15 19l-7-7 7-7"
            />
          </svg>
        </button>
        <div className="flex-1 min-w-0">
          <h2 className="text-lg font-semibold text-surface-100 truncate">
            {workstream.label}
          </h2>
          {workstream.description && (
            <p className="text-sm text-surface-400 truncate">
              {workstream.description}
            </p>
          )}
        </div>
        <div className="flex items-center gap-4 text-xs text-surface-500 flex-shrink-0">
          <span>{workstream.conversations.length} chats</span>
          <span>{totalMessages} messages</span>
          <span>{participants.length} people</span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-5 py-6 space-y-8">
          {/* Participants */}
          <section>
            <h3 className="text-xs font-medium text-surface-500 uppercase tracking-wider mb-3">
              People ({participants.length})
            </h3>
            <div className="flex flex-wrap gap-3">
              {participants.map((p) => (
                <div
                  key={p.id}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-800 border border-surface-700"
                >
                  <div className="w-7 h-7 rounded-full bg-surface-600 flex items-center justify-center overflow-hidden flex-shrink-0">
                    {p.avatar_url ? (
                      <img
                        src={p.avatar_url}
                        alt={p.name ?? ""}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <span className="text-xs font-medium text-surface-300">
                        {(p.name ?? "?")[0]?.toUpperCase()}
                      </span>
                    )}
                  </div>
                  <div className="min-w-0">
                    <span className="text-sm text-surface-200 truncate block">
                      {p.name ?? "Unknown"}
                    </span>
                    <span className="text-[10px] text-surface-500">
                      {p.chatCount} chat{p.chatCount !== 1 ? "s" : ""}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Conversations */}
          <section>
            <h3 className="text-xs font-medium text-surface-500 uppercase tracking-wider mb-3">
              Chats ({sortedChats.length})
            </h3>
            <div className="space-y-2">
              {sortedChats.map((conv) => (
                <button
                  key={conv.id}
                  type="button"
                  className="w-full text-left px-4 py-3 rounded-lg border border-surface-700 bg-surface-850 hover:bg-surface-800 hover:border-surface-600 transition-colors group"
                  onClick={() => onSelectConversation(conv.id)}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <h4 className="text-sm font-medium text-surface-200 group-hover:text-surface-100 truncate">
                        {conv.title || "Untitled"}
                      </h4>
                      <div className="flex items-center gap-3 mt-1 text-xs text-surface-500">
                        <span>{conv.message_count} messages</span>
                        {conv.messages_in_window > 0 && (
                          <>
                            <span className="w-px h-3 bg-surface-700" />
                            <span className="text-primary-400">
                              {conv.messages_in_window} recent
                            </span>
                          </>
                        )}
                        <span className="w-px h-3 bg-surface-700" />
                        <span>{conv.participants.length} participants</span>
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1 flex-shrink-0">
                      {conv.last_message_at && (
                        <span className="text-xs text-surface-500">
                          {timeAgo(conv.last_message_at)}
                        </span>
                      )}
                      {/* Participant avatars */}
                      <div className="flex -space-x-1.5">
                        {conv.participants.slice(0, 4).map((p) => (
                          <div
                            key={p.id}
                            className="w-5 h-5 rounded-full border border-surface-850 bg-surface-600 flex items-center justify-center overflow-hidden"
                            title={p.name ?? "Unknown"}
                          >
                            {p.avatar_url ? (
                              <img
                                src={p.avatar_url}
                                alt={p.name ?? ""}
                                className="w-full h-full object-cover"
                              />
                            ) : (
                              <span className="text-[8px] font-medium text-surface-300">
                                {(p.name ?? "?")[0]?.toUpperCase()}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
