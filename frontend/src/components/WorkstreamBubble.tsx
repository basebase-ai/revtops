/**
 * Single workstream bubble: label, optional expanded conversation list.
 */

import type { WorkstreamConversation, WorkstreamItem } from "../store/types";

interface WorkstreamBubbleProps {
  workstream: WorkstreamItem;
  left: number;
  top: number;
  radius: number;
  isExpanded: boolean;
  onToggle: () => void;
  onSelectConversation: (conversationId: string) => void;
}

export function WorkstreamBubble({
  workstream,
  left,
  top,
  radius,
  isExpanded,
  onToggle,
  onSelectConversation,
}: WorkstreamBubbleProps): JSX.Element {
  const totalMessages = workstream.conversations.reduce(
    (sum, c) => sum + c.messages_in_window,
    0
  );
  const sizeLabel = totalMessages > 0 ? `${totalMessages} msg` : `${workstream.conversations.length} chats`;

  return (
    <div
      className="absolute cursor-pointer transition-all duration-200 rounded-full border-2 border-slate-300 dark:border-slate-600 bg-slate-100/90 dark:bg-slate-800/90 hover:ring-2 hover:ring-blue-400 dark:hover:ring-blue-500 flex flex-col items-center justify-center overflow-visible"
      style={{
        left: left - radius,
        top: top - radius,
        width: radius * 2,
        height: radius * 2,
        minWidth: 80,
        minHeight: 80,
      }}
      onClick={onToggle}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggle();
        }
      }}
    >
      <span className="text-xs font-medium text-slate-700 dark:text-slate-200 px-2 text-center leading-tight">
        {workstream.label}
      </span>
      <span className="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5">
        {sizeLabel}
      </span>
      {isExpanded && (
        <div
          className="absolute z-10 top-full left-1/2 -translate-x-1/2 mt-2 w-64 max-h-72 overflow-y-auto rounded-lg shadow-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 py-2"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="text-xs text-slate-500 dark:text-slate-400 px-3 mb-2">
            {workstream.description}
          </p>
          <ul className="list-none">
            {workstream.conversations.map((conv: WorkstreamConversation) => (
              <li key={conv.id}>
                <button
                  type="button"
                  className="w-full text-left px-3 py-2 hover:bg-slate-100 dark:hover:bg-slate-700 text-sm text-slate-800 dark:text-slate-200 truncate"
                  onClick={() => onSelectConversation(conv.id)}
                >
                  {conv.title || "Untitled"}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
