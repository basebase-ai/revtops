/**
 * Artifact tile component for displaying artifacts inline in chat.
 *
 * Renders as a clickable card showing artifact type icon, title, and filename.
 * Clicking opens the artifact in the side panel viewer.
 */

interface ArtifactData {
  id: string;
  title: string;
  filename: string;
  contentType: "text" | "markdown" | "pdf" | "chart";
  mimeType: string;
}

interface ArtifactTileProps {
  artifact: ArtifactData;
  onClick: () => void;
}

export function ArtifactTile({ artifact, onClick }: ArtifactTileProps): JSX.Element {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-3 p-3 rounded-lg bg-surface-800/80 hover:bg-surface-700/80 border border-surface-700 hover:border-surface-600 transition-all duration-150 text-left w-full max-w-sm group"
    >
      {/* Icon */}
      <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-surface-700 group-hover:bg-surface-600 flex items-center justify-center transition-colors">
        <ArtifactIcon contentType={artifact.contentType} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="font-medium text-surface-100 text-sm truncate">
          {artifact.title}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-xs text-surface-400 truncate">
            {artifact.filename}
          </span>
          <span className="text-xs text-surface-500">
            {getContentTypeLabel(artifact.contentType)}
          </span>
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

function ArtifactIcon({ contentType }: { contentType: string }): JSX.Element {
  switch (contentType) {
    case "text":
      return (
        <svg
          className="w-5 h-5 text-surface-300"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
      );

    case "markdown":
      return (
        <svg
          className="w-5 h-5 text-blue-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
          />
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 13l2 2 4-4"
          />
        </svg>
      );

    case "pdf":
      return (
        <svg
          className="w-5 h-5 text-red-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
          />
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 14h.01M12 14h.01M15 14h.01"
          />
        </svg>
      );

    case "chart":
      return (
        <svg
          className="w-5 h-5 text-green-400"
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
      );

    default:
      return (
        <svg
          className="w-5 h-5 text-surface-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
      );
  }
}

function getContentTypeLabel(contentType: string): string {
  switch (contentType) {
    case "text":
      return "Text";
    case "markdown":
      return "Markdown";
    case "pdf":
      return "PDF";
    case "chart":
      return "Chart";
    default:
      return "File";
  }
}

export type { ArtifactData };
