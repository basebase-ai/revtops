/**
 * Full-screen app view at /apps/:id.
 *
 * Shows the Sandpack-rendered app with a header bar containing:
 * - App title
 * - "Copy link" / "Embed" in options menu
 * - Back to gallery button
 */

import { useState, useEffect, useCallback } from "react";
import { SandpackAppRenderer } from "./SandpackAppRenderer";
import { apiRequest } from "../../lib/api";
import { useAppStore } from "../../store";
import type { VisibilityLevel } from "../VisibilitySelector";
import { DetailViewHeader } from "../shared/DetailViewHeader";

interface AppDetail {
  id: string;
  title: string | null;
  description: string | null;
  frontend_code: string;
  frontend_code_compiled?: string | null;
  query_names: string[];
  conversation_id: string | null;
  created_at: string | null;
  user_id: string;
  widget_config?: Record<string, unknown> | null;
  visibility: string;
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
  const [previewMode, setPreviewMode] = useState<string>("auto");
  const [detailLevel, setDetailLevel] = useState<string>("standard");
  const [visBusy, setVisBusy] = useState<boolean>(false);

  const setCurrentView = useAppStore((s) => s.setCurrentView);
  const user = useAppStore((s) => s.user);

  const fetchApp = useCallback(async (): Promise<void> => {
    setLoading(true);
    const resp = await apiRequest<AppDetail>(`/apps/${appId}`);
    if (resp.error || !resp.data) {
      setError(resp.error ?? "Failed to load app");
    } else {
      setApp({
        ...resp.data,
        visibility: resp.data.visibility ?? "team",
      });
    }
    setLoading(false);
  }, [appId]);

  useEffect(() => {
    void fetchApp();
  }, [fetchApp]);

  // Sync preview mode / detail level from widget_config when app loads
  useEffect(() => {
    if (app?.widget_config) {
      if (app.widget_config.preferred_mode) setPreviewMode(app.widget_config.preferred_mode as string);
      if (app.widget_config.detail_level) setDetailLevel(app.widget_config.detail_level as string);
    }
  }, [app?.widget_config]);

  const handlePreviewSettingsChange = async (
    newMode?: string,
    newDetail?: string,
  ): Promise<void> => {
    const payload: Record<string, string> = {};
    if (newMode !== undefined) payload.preferred_mode = newMode === "auto" ? "" : newMode;
    if (newDetail !== undefined) payload.detail_level = newDetail;

    // Optimistic update
    if (newMode !== undefined) setPreviewMode(newMode);
    if (newDetail !== undefined) setDetailLevel(newDetail);

    await apiRequest(`/apps/${appId}/preview-settings`, {
      method: "PATCH",
      body: JSON.stringify(
        Object.fromEntries(
          Object.entries(payload).filter(([, v]) => v !== ""),
        ),
      ),
    });
  };

  const organization = useAppStore((s) => s.organization);
  const organizations = useAppStore((s) => s.organizations);
  const orgHandle: string | null =
    organization?.handle ??
    (organization?.id ? organizations.find((o) => o.id === organization.id)?.handle ?? null : null) ??
    null;
  const prefix: string = orgHandle ? `/${orgHandle}` : "";

  const isOwner: boolean =
    Boolean(user?.id) && Boolean(app?.user_id) && user?.id === app?.user_id;

  const handleCopyLink = async (): Promise<void> => {
    const isPublic: boolean = app?.visibility === "public";
    const url: string = isPublic
      ? `${window.location.origin}/api/public/share/apps/${appId}`
      : `${window.location.origin}${prefix}/apps/${appId}`;
    await navigator.clipboard.writeText(url);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  };

  const handleVisibilityChange = async (next: VisibilityLevel): Promise<void> => {
    if (next === "public") {
      const ok: boolean = window.confirm(
        "Anyone on the internet can view this app without signing in. Continue?",
      );
      if (!ok) return;
    }
    if (app) setApp({ ...app, visibility: next });
    setVisBusy(true);
    const resp = await apiRequest<{ visibility: string }>(`/apps/${appId}/visibility`, {
      method: "PATCH",
      body: JSON.stringify({ visibility: next }),
    });
    setVisBusy(false);
    if (resp.error && app) {
      setApp({ ...app, visibility: app.visibility });
    }
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
    window.history.pushState(null, "", `${prefix}/apps`);
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
      <DetailViewHeader
        onBack={goBack}
        backButtonLabel="Back to Apps"
        title={app.title ?? "Untitled App"}
        subtitle={app.description ?? null}
        menuContent={(closeMenu) => (
          <>
            {isOwner ? (
              <>
                <div className="px-3 py-1.5 text-[10px] font-semibold text-surface-500 uppercase tracking-wider">
                  Visibility
                </div>
                {(["private", "team", "public"] as const).map((lvl) => {
                  const label: string =
                    lvl === "private" ? "Only me" : lvl === "team" ? "Team" : "Public";
                  const active: boolean = (app.visibility as VisibilityLevel) === lvl;
                  return (
                    <button
                      key={lvl}
                      type="button"
                      disabled={visBusy}
                      onClick={() => void handleVisibilityChange(lvl)}
                      className={`w-full text-left px-3 py-1.5 flex items-center gap-2 transition-colors ${
                        active
                          ? "text-primary-400"
                          : "text-surface-300 hover:bg-surface-700"
                      } disabled:opacity-50`}
                    >
                      <span className="w-4 text-center">
                        {active ? "✓" : ""}
                      </span>
                      {label}
                    </button>
                  );
                })}
                <div className="my-1 border-t border-surface-700" />
              </>
            ) : null}

            <div className="px-3 py-1.5 text-[10px] font-semibold text-surface-500 uppercase tracking-wider">
              Preview
            </div>
            {([
              ["auto", "Auto"],
              ["screenshot", "Screenshot"],
              ["widget", "Widget"],
              ["mini_app", "Mini App"],
              ["icon", "Icon"],
            ] as const).map(([val, label]) => (
              <button
                key={val}
                type="button"
                onClick={() => void handlePreviewSettingsChange(val, undefined)}
                className={`w-full text-left px-3 py-1.5 flex items-center gap-2 transition-colors ${
                  previewMode === val
                    ? "text-primary-400"
                    : "text-surface-300 hover:bg-surface-700"
                }`}
              >
                <span className="w-4 text-center">
                  {previewMode === val ? "✓" : ""}
                </span>
                {label}
              </button>
            ))}

            {previewMode === "widget" ? (
              <>
                <div className="my-1 border-t border-surface-700" />
                <div className="px-3 py-1.5 text-[10px] font-semibold text-surface-500 uppercase tracking-wider">
                  Detail level
                </div>
                {([
                  ["minimal", "Minimal"],
                  ["standard", "Standard"],
                  ["detailed", "Detailed"],
                ] as const).map(([val, label]) => (
                  <button
                    key={val}
                    type="button"
                    onClick={() => void handlePreviewSettingsChange(undefined, val)}
                    className={`w-full text-left px-3 py-1.5 flex items-center gap-2 transition-colors ${
                      detailLevel === val
                        ? "text-primary-400"
                        : "text-surface-300 hover:bg-surface-700"
                    }`}
                  >
                    <span className="w-4 text-center">
                      {detailLevel === val ? "✓" : ""}
                    </span>
                    {label}
                  </button>
                ))}
              </>
            ) : null}

            <div className="my-1 border-t border-surface-700" />

            <button
              type="button"
              onClick={() => {
                void handleCopyLink();
                closeMenu();
              }}
              className="w-full text-left px-3 py-1.5 text-surface-300 hover:bg-surface-700 transition-colors flex items-center gap-2"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              {linkCopied
                ? (app.visibility === "public" ? "Public link copied!" : "Link copied!")
                : "Copy link"}
            </button>
            <button
              type="button"
              onClick={() => {
                void handleEmbed();
                closeMenu();
              }}
              className="w-full text-left px-3 py-1.5 text-surface-300 hover:bg-surface-700 transition-colors flex items-center gap-2"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
              </svg>
              {embedCopied ? "Embed code copied!" : "Copy embed code"}
            </button>
          </>
        )}
      />

      <div className="flex-1 overflow-hidden">
        <SandpackAppRenderer
          appId={appId}
        />
      </div>
    </div>
  );
}
