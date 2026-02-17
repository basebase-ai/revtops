/**
 * App viewer component for displaying an interactive Penny App in the side panel.
 *
 * Wraps SandpackAppRenderer with:
 * - Header showing title and type badge
 * - Share / Embed action buttons
 * - Error feedback ("Fix it" prompt sent back to Penny)
 */

import { useState, useCallback } from "react";
import { SandpackAppRenderer } from "./SandpackAppRenderer";
import { apiRequest } from "../../lib/api";
import type { AppBlock } from "../../store";

interface AppViewerProps {
  app: AppBlock["app"];
  /** Called when the user clicks "Fix it" on a runtime/compile error. */
  onAppError?: (errorMessage: string) => void;
}

export function AppViewer({ app, onAppError }: AppViewerProps): JSX.Element {
  const [linkCopied, setLinkCopied] = useState<boolean>(false);
  const [embedCopied, setEmbedCopied] = useState<boolean>(false);

  const handleCopyLink = useCallback(async (): Promise<void> => {
    const url: string = `${window.location.origin}/apps/${app.id}`;
    await navigator.clipboard.writeText(url);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  }, [app.id]);

  const handleEmbed = useCallback(async (): Promise<void> => {
    const resp = await apiRequest<{ embed_url: string }>(
      `/apps/${app.id}/embed-token`,
      { method: "POST" },
    );
    if (resp.data) {
      const snippet: string = `<iframe src="${resp.data.embed_url}" width="100%" height="600" frameborder="0"></iframe>`;
      await navigator.clipboard.writeText(snippet);
      setEmbedCopied(true);
      setTimeout(() => setEmbedCopied(false), 2000);
    }
  }, [app.id]);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-surface-700">
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-primary-900/40 text-primary-300">
          Interactive App
        </span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => void handleCopyLink()}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-surface-700 hover:bg-surface-600 text-surface-300 text-xs font-medium transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            {linkCopied ? "Copied!" : "Share"}
          </button>
          <button
            onClick={() => void handleEmbed()}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-surface-700 hover:bg-surface-600 text-surface-300 text-xs font-medium transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
            </svg>
            {embedCopied ? "Copied!" : "Embed"}
          </button>
        </div>
      </div>

      {/* Renderer */}
      <div className="flex-1 overflow-auto">
        <SandpackAppRenderer
          appId={app.id}
          frontendCode={app.frontendCode}
          onError={onAppError}
        />
      </div>
    </div>
  );
}
