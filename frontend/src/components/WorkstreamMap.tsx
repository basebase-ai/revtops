/**
 * 2D map of workstream bubbles (semantic Home). Positions from API (UMAP); bubbles sized by activity.
 */

import { useCallback, useState } from "react";
import type { WorkstreamConversation, WorkstreamItem } from "../store/types";
import { WorkstreamBubble } from "./WorkstreamBubble";

interface WorkstreamMapProps {
  workstreams: WorkstreamItem[];
  unclustered: WorkstreamConversation[];
  onSelectConversation: (conversationId: string) => void;
  width: number;
  height: number;
}

const MIN_RADIUS = 44;
const MAX_RADIUS = 90;
const RADIUS_SCALE = 8;

function totalMessagesInWindow(ws: WorkstreamItem): number {
  return ws.conversations.reduce((sum, c) => sum + (c.messages_in_window ?? 0), 0);
}

export function WorkstreamMap({
  workstreams,
  unclustered,
  onSelectConversation,
  width,
  height,
}: WorkstreamMapProps): JSX.Element {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const padding = 100;

  const getRadius = useCallback(
    (ws: WorkstreamItem): number => {
      const total = totalMessagesInWindow(ws);
      const r = MIN_RADIUS + Math.sqrt(total) * RADIUS_SCALE;
      return Math.min(MAX_RADIUS, Math.max(MIN_RADIUS, r));
    },
    []
  );

  const toggleExpanded = useCallback((id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  const innerWidth = Math.max(1, width - 2 * padding);
  const innerHeight = Math.max(1, height - 2 * padding);

  return (
    <div className="relative w-full h-full min-h-[400px]" style={{ width, height }}>
      {workstreams.map((ws) => {
        const [px, py] = ws.position;
        const x = padding + px * innerWidth;
        const y = padding + py * innerHeight;
        const radius = getRadius(ws);
        return (
          <WorkstreamBubble
            key={ws.id}
            workstream={ws}
            left={x}
            top={y}
            radius={radius}
            isExpanded={expandedId === ws.id}
            onToggle={() => toggleExpanded(ws.id)}
            onSelectConversation={onSelectConversation}
          />
        );
      })}
      {unclustered.length > 0 && (
        <div
          className="absolute bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg bg-slate-200/80 dark:bg-slate-700/80 text-sm text-slate-700 dark:text-slate-200"
          role="region"
          aria-label="Unclustered conversations"
        >
          {unclustered.length} unclustered chat{unclustered.length !== 1 ? "s" : ""}
        </div>
      )}
    </div>
  );
}
