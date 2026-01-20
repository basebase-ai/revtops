/**
 * Chat message component.
 *
 * Renders individual chat messages with different styles for user/assistant.
 */

interface MessageProps {
  message: {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    isStreaming?: boolean;
  };
  onArtifactClick?: (artifact: {
    id: string;
    type: string;
    title: string;
    data: Record<string, unknown>;
  }) => void;
}

export function Message({ message, onArtifactClick }: MessageProps): JSX.Element {
  const isUser = message.role === 'user';

  // Parse content for artifacts
  const { textContent, artifacts } = parseContent(message.content);

  return (
    <div
      className={`flex gap-3 animate-slide-up ${isUser ? 'flex-row-reverse' : 'flex-row'}`}
    >
      {/* Avatar */}
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${
          isUser
            ? 'bg-primary-600'
            : 'bg-gradient-to-br from-surface-700 to-surface-800'
        }`}
      >
        {isUser ? (
          <svg
            className="w-4 h-4 text-white"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
            />
          </svg>
        ) : (
          <svg
            className="w-4 h-4 text-primary-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
            />
          </svg>
        )}
      </div>

      {/* Content */}
      <div className={`flex-1 max-w-[85%] ${isUser ? 'text-right' : 'text-left'}`}>
        <div
          className={`inline-block px-4 py-3 rounded-2xl ${
            isUser
              ? 'bg-primary-600 text-white rounded-tr-md'
              : 'bg-surface-800 text-surface-100 rounded-tl-md'
          }`}
        >
          <div className="whitespace-pre-wrap break-words">
            {textContent}
            {message.isStreaming && (
              <span className="inline-block w-2 h-4 bg-current animate-pulse ml-1" />
            )}
          </div>
        </div>

        {/* Artifacts */}
        {artifacts.length > 0 && (
          <div className="mt-2 space-y-2">
            {artifacts.map((artifact, index) => (
              <button
                key={index}
                onClick={() => onArtifactClick?.(artifact)}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-surface-300 text-sm transition-colors"
              >
                <ArtifactIcon type={artifact.type} />
                <span>{artifact.title}</span>
              </button>
            ))}
          </div>
        )}

        {/* Timestamp */}
        <div className="mt-1">
          <span className="text-xs text-surface-500">
            {formatTime(message.timestamp)}
          </span>
        </div>
      </div>
    </div>
  );
}

interface ParsedContent {
  textContent: string;
  artifacts: Array<{
    id: string;
    type: string;
    title: string;
    data: Record<string, unknown>;
  }>;
}

function parseContent(content: string): ParsedContent {
  // Look for artifact markers like [ARTIFACT:{"id":"...", "type":"...", "title":"..."}]
  const artifactRegex = /\[ARTIFACT:(.*?)\]/g;
  const artifacts: ParsedContent['artifacts'] = [];

  let textContent = content;
  let match: RegExpExecArray | null;

  while ((match = artifactRegex.exec(content)) !== null) {
    try {
      const artifactData = JSON.parse(match[1] ?? '{}') as {
        id?: string;
        type?: string;
        title?: string;
        data?: Record<string, unknown>;
      };
      artifacts.push({
        id: artifactData.id ?? `artifact-${Date.now()}`,
        type: artifactData.type ?? 'analysis',
        title: artifactData.title ?? 'Untitled',
        data: artifactData.data ?? {},
      });
      textContent = textContent.replace(match[0], '');
    } catch {
      // Invalid JSON, skip
    }
  }

  return { textContent: textContent.trim(), artifacts };
}

function ArtifactIcon({ type }: { type: string }): JSX.Element {
  switch (type) {
    case 'dashboard':
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
          />
        </svg>
      );
    case 'report':
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
      );
    default:
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
          />
        </svg>
      );
  }
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
}
