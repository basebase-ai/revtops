/**
 * App tile component for displaying apps inline in chat.
 *
 * Renders as a clickable card with a chart icon, title, and description.
 * Clicking opens the app in the side panel AppViewer.
 */

import type { AppBlock } from "../../store";

interface AppTileProps {
  app: AppBlock["app"];
  onClick: () => void;
}

export function AppTile({ app, onClick }: AppTileProps): JSX.Element {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-3 p-3 rounded-lg bg-surface-800/80 hover:bg-surface-700/80 border border-surface-700 hover:border-surface-600 transition-all duration-150 text-left w-full max-w-sm group"
    >
      {/* Icon */}
      <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-primary-900/40 group-hover:bg-primary-900/60 flex items-center justify-center transition-colors">
        <svg
          className="w-5 h-5 text-primary-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
          />
        </svg>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="font-medium text-surface-100 text-sm truncate">
          {app.title}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-xs text-primary-400 font-medium">
            Interactive App
          </span>
          {app.description && (
            <span className="text-xs text-surface-500 truncate">
              {app.description}
            </span>
          )}
        </div>
      </div>

      {/* Chevron */}
      <svg
        className="w-4 h-4 text-surface-500 group-hover:text-surface-300 transition-colors flex-shrink-0"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M9 5l7 7-7 7"
        />
      </svg>
    </button>
  );
}
