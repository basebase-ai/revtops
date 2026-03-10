/**
 * App viewer component for displaying an interactive Basebase App in the side panel.
 *
 * Wraps SandpackAppRenderer with:
 * - Header showing title and type badge
 * - Share / Embed action buttons
 * - Error feedback ("Fix it" prompt sent back to Basebase)
 */

import { useState, useCallback } from "react";
import { SandpackAppRenderer } from "./SandpackAppRenderer";
import { apiRequest } from "../../lib/api";
import { useAppStore } from "../../store";
import type { AppBlock } from "../../store";

interface AppViewerProps {
  app: AppBlock["app"];
  /** Called when the user clicks "Fix it" on a runtime/compile error. */
  onAppError?: (errorMessage: string) => void;
}

export function AppViewer({ app, onAppError }: AppViewerProps): JSX.Element {
  const [linkCopied, setLinkCopied] = useState<boolean>(false);
  const [embedCopied, setEmbedCopied] = useState<boolean>(false);
  const [reloadKey, setReloadKey] = useState<number>(0);

  const organization = useAppStore((s) => s.organization);
  const organizations = useAppStore((s) => s.organizations);
  const orgHandle: string | null =
    organization?.handle ??
    (organization?.id ? organizations.find((o) => o.id === organization.id)?.handle ?? null : null) ??
    null;
  const prefix: string = orgHandle ? `/${orgHandle}` : "";

  const handleCopyLink = useCallback(async (): Promise<void> => {
    const url: string = `${window.location.origin}${prefix}/apps/${app.id}`;
    await navigator.clipboard.writeText(url);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  }, [app.id, prefix]);

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
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="text-sm font-medium text-surface-100 truncate">
            {app.title ?? "Untitled App"}
          </h2>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setReloadKey((k: number) => k + 1)}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-surface-700 hover:bg-surface-600 text-surface-300 text-xs font-medium transition-colors"
            title="Reload app"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Reload
          </button>
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
      <div key={reloadKey} className="flex-1 overflow-auto">
        <SandpackAppRenderer
          appId={app.id}
          onError={onAppError}
        />
      </div>
    </div>
  );
}
