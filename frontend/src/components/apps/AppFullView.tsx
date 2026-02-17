/**
 * Full-screen app view at /apps/:id.
 *
 * Shows the Sandpack-rendered app with a header bar containing:
 * - App title
 * - "Copy link" button
 * - "Embed" button (generates tokenized embed URL)
 * - Back to gallery button
 */

import { useState, useEffect, useCallback } from "react";
import { SandpackAppRenderer } from "./SandpackAppRenderer";
import { apiRequest } from "../../lib/api";
import { useAppStore } from "../../store";

interface AppDetail {
  id: string;
  title: string | null;
  description: string | null;
  frontend_code: string;
  query_names: string[];
  conversation_id: string | null;
  created_at: string | null;
  user_id: string;
}

interface EmbedTokenData {
  embed_url: string;
  token: string;
  expires_at: string;
}

interface AppFullViewProps {
  appId: string;
}

export function AppFullView({ appId }: AppFullViewProps): JSX.Element {
  const [app, setApp] = useState<AppDetail | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState<boolean>(false);
  const [embedUrl, setEmbedUrl] = useState<string | null>(null);
  const [embedCopied, setEmbedCopied] = useState<boolean>(false);

  const setCurrentView = useAppStore((s) => s.setCurrentView);

  const fetchApp = useCallback(async (): Promise<void> => {
    setLoading(true);
    const resp = await apiRequest<AppDetail>(`/apps/${appId}`);
    if (resp.error || !resp.data) {
      setError(resp.error ?? "Failed to load app");
    } else {
      setApp(resp.data);
    }
    setLoading(false);
  }, [appId]);

  useEffect(() => {
    void fetchApp();
  }, [fetchApp]);

  const handleCopyLink = async (): Promise<void> => {
    const url: string = `${window.location.origin}/apps/${appId}`;
    await navigator.clipboard.writeText(url);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  };

  const handleEmbed = async (): Promise<void> => {
    if (embedUrl) {
      await navigator.clipboard.writeText(
        `<iframe src="${embedUrl}" width="100%" height="600" frameborder="0"></iframe>`
      );
      setEmbedCopied(true);
      setTimeout(() => setEmbedCopied(false), 2000);
      return;
    }

    const resp = await apiRequest<EmbedTokenData>(`/apps/${appId}/embed-token`, {
      method: "POST",
    });
    if (resp.data) {
      setEmbedUrl(resp.data.embed_url);
      const snippet: string = `<iframe src="${resp.data.embed_url}" width="100%" height="600" frameborder="0"></iframe>`;
      await navigator.clipboard.writeText(snippet);
      setEmbedCopied(true);
      setTimeout(() => setEmbedCopied(false), 2000);
    }
  };

  const goBack = (): void => {
    setCurrentView("apps" as never);
    window.history.pushState(null, "", "/apps");
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" />
      </div>
    );
  }

  if (error || !app) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="p-4 rounded-lg bg-red-900/20 border border-red-700 text-red-300 text-sm max-w-md text-center">
          {error ?? "App not found"}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700 bg-surface-900 flex-shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={goBack}
            className="text-surface-400 hover:text-surface-200 transition-colors"
            title="Back to Apps"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div>
            <h1 className="text-base font-semibold text-surface-100">
              {app.title ?? "Untitled App"}
            </h1>
            {app.description && (
              <p className="text-xs text-surface-400 mt-0.5 truncate max-w-md">
                {app.description}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => void handleCopyLink()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-surface-700 hover:bg-surface-600 text-surface-300 text-xs font-medium transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            {linkCopied ? "Copied!" : "Copy link"}
          </button>
          <button
            onClick={() => void handleEmbed()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
            </svg>
            {embedCopied ? "Copied!" : "Embed"}
          </button>
        </div>
      </div>

      {/* App renderer */}
      <div className="flex-1 overflow-hidden">
        <SandpackAppRenderer
          appId={appId}
          frontendCode={app.frontend_code}
        />
      </div>
    </div>
  );
}
