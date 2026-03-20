/**
 * Grid of workstream cards replacing the old bubble map.
 * Sorted by activity (messages + participants in window). Each card shows
 * a human-readable label, scrollable recent chats, and overlapping avatars.
 */

import { useMemo, useState } from "react";
import type { WorkstreamConversation, WorkstreamItem } from "../store/types";
import { WorkstreamDetailView } from "./WorkstreamDetailView";

interface WorkstreamGridProps {
  workstreams: WorkstreamItem[];
  unclustered: WorkstreamConversation[];
  onSelectConversation: (conversationId: string) => void;
}

function activityScore(ws: WorkstreamItem): number {
  const msgCount: number = ws.conversations.reduce(
    (sum, c) => sum + (c.messages_in_window ?? 0),
    0
  );
  const participantIds = new Set<string>();
  for (const c of ws.conversations) {
    for (const p of c.participants) {
      participantIds.add(p.id);
    }
  }
  return msgCount * 2 + ws.conversations.length * 3 + participantIds.size * 5;
}

function collectParticipants(
  ws: WorkstreamItem
): { id: string; name: string | null; avatar_url: string | null }[] {
  const seen = new Map<
    string,
    { id: string; name: string | null; avatar_url: string | null }
  >();
  for (const c of ws.conversations) {
    for (const p of c.participants) {
      if (!seen.has(p.id)) {
        seen.set(p.id, {
          id: p.id,
          name: p.name,
          avatar_url: p.avatar_url,
        });
      }
    }
  }
  return Array.from(seen.values());
}

function sortedConversations(ws: WorkstreamItem): WorkstreamConversation[] {
  return [...ws.conversations].sort((a, b) => {
    const aTime: number = a.last_message_at
      ? new Date(a.last_message_at).getTime()
      : 0;
    const bTime: number = b.last_message_at
      ? new Date(b.last_message_at).getTime()
      : 0;
    return bTime - aTime;
  });
}

function WorkstreamCard({
  workstream,
  onSelectConversation,
  onOpenDetail,
}: {
  workstream: WorkstreamItem;
  onSelectConversation: (id: string) => void;
  onOpenDetail: (ws: WorkstreamItem) => void;
}): JSX.Element {
  const participants = useMemo(
    () => collectParticipants(workstream),
    [workstream]
  );
  const recentChats: WorkstreamConversation[] = useMemo(
    () => sortedConversations(workstream),
    [workstream]
  );
  const totalMessages: number = workstream.conversations.reduce(
    (sum, c) => sum + (c.messages_in_window ?? 0),
    0
  );

  return (
    <div
      className="group flex flex-col rounded-xl border border-surface-700 bg-surface-850 hover:border-surface-500 hover:bg-surface-800 transition-all cursor-pointer overflow-hidden"
      onClick={() => onOpenDetail(workstream)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpenDetail(workstream);
        }
      }}
    >
      {/* Header */}
      <div className="px-4 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-surface-100 leading-tight truncate">
          {workstream.label}
        </h3>
        {workstream.description && (
          <p className="text-xs text-surface-400 mt-1 line-clamp-2">
            {workstream.description}
          </p>
        )}
        <div className="flex items-center gap-3 mt-2 text-xs text-surface-500">
          <span>{workstream.conversations.length} chats</span>
          <span className="w-px h-3 bg-surface-700" />
          <span>{totalMessages} messages</span>
          <span className="w-px h-3 bg-surface-700" />
          <span>{participants.length} people</span>
        </div>
      </div>

      {/* Recent chats (scrollable) */}
      <div className="flex-1 px-2 py-1 max-h-[140px] overflow-y-auto scrollbar-thin">
        {recentChats.map((conv) => (
          <button
            key={conv.id}
            type="button"
            className="w-full text-left px-2 py-1.5 rounded-md hover:bg-surface-700/60 transition-colors group/chat"
            onClick={(e) => {
              e.stopPropagation();
              onSelectConversation(conv.id);
            }}
          >
            <span className="text-xs text-surface-300 group-hover/chat:text-surface-100 truncate block">
              {conv.title || "Untitled"}
            </span>
          </button>
        ))}
      </div>

      {/* Participant avatars */}
      {participants.length > 0 && (
        <div className="px-4 py-3 border-t border-surface-700/50">
          <div className="flex items-center">
            <div className="flex -space-x-2">
              {participants.slice(0, 5).map((p) => (
                <div
                  key={p.id}
                  className="w-6 h-6 rounded-full border-2 border-surface-850 bg-surface-600 flex items-center justify-center overflow-hidden flex-shrink-0"
                  title={p.name ?? "Unknown"}
                >
                  {p.avatar_url ? (
                    <img
                      src={p.avatar_url}
                      alt={p.name ?? ""}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <span className="text-[10px] font-medium text-surface-300">
                      {(p.name ?? "?")[0]?.toUpperCase()}
                    </span>
                  )}
                </div>
              ))}
              {participants.length > 5 && (
                <div className="w-6 h-6 rounded-full border-2 border-surface-850 bg-surface-700 flex items-center justify-center flex-shrink-0">
                  <span className="text-[9px] font-medium text-surface-300">
                    +{participants.length - 5}
                  </span>
                </div>
              )}
            </div>
            <span className="ml-2 text-xs text-surface-500 truncate">
              {participants
                .slice(0, 3)
                .map((p) => p.name?.split(" ")[0] ?? "Unknown")
                .join(", ")}
              {participants.length > 3 ? ` +${participants.length - 3}` : ""}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export function WorkstreamGrid({
  workstreams,
  unclustered,
  onSelectConversation,
}: WorkstreamGridProps): JSX.Element {
  const [selectedWorkstream, setSelectedWorkstream] =
    useState<WorkstreamItem | null>(null);

  const sorted: WorkstreamItem[] = useMemo(
    () => [...workstreams].sort((a, b) => activityScore(b) - activityScore(a)),
    [workstreams]
  );

  if (selectedWorkstream) {
    return (
      <WorkstreamDetailView
        workstream={selectedWorkstream}
        onBack={() => setSelectedWorkstream(null)}
        onSelectConversation={onSelectConversation}
      />
    );
  }

  return (
    <div className="p-4 h-full overflow-y-auto">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {sorted.map((ws) => (
          <WorkstreamCard
            key={ws.id}
            workstream={ws}
            onSelectConversation={onSelectConversation}
            onOpenDetail={setSelectedWorkstream}
          />
        ))}
      </div>

      {unclustered.length > 0 && (
        <div className="mt-6">
          <h4 className="text-xs font-medium text-surface-500 uppercase tracking-wider mb-3 px-1">
            Other conversations ({unclustered.length})
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {unclustered.map((conv) => (
              <button
                key={conv.id}
                type="button"
                className="text-left px-3 py-2 rounded-lg border border-surface-700/50 bg-surface-850 hover:bg-surface-800 hover:border-surface-600 transition-colors"
                onClick={() => onSelectConversation(conv.id)}
              >
                <span className="text-xs text-surface-300 truncate block">
                  {conv.title || "Untitled"}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
