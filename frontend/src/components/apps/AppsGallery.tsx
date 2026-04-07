/**
 * Apps gallery page – lists all Basebase apps for the current org.
 *
 * Accessible via the "Apps" nav item in the sidebar.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { apiRequest } from "../../lib/api";
import { useAppStore } from "../../store";
import type { WidgetConfig } from "../../store/types";
import { AppPreview } from "../widgets/AppPreview";
import { VisibilityBadge } from "../VisibilitySelector";

/** Preload CDN libraries used by SandpackAppRenderer so they're browser-cached before user opens an app. */
const CDN_PRELOADS = [
  "https://unpkg.com/react@18/umd/react.production.min.js",
  "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js",
  "https://cdn.plot.ly/plotly-2.35.3.min.js",
  "https://unpkg.com/@babel/standalone@7/babel.min.js",
];
let _preloaded = false;
function preloadAppCdnLibs(): void {
  if (_preloaded) return;
  _preloaded = true;
  for (const url of CDN_PRELOADS) {
    const link = document.createElement("link");
    link.rel = "prefetch";
    link.href = url;
    link.as = "script";
    document.head.appendChild(link);
  }
}

interface AppItem {
  id: string;
  title: string | null;
  description: string | null;
  created_at: string | null;
  creator_name: string | null;
  creator_email: string | null;
  conversation_id: string | null;
  archived_at: string | null;
  widget_config: WidgetConfig | null;
  visibility?: string;
}

interface AppsListResponse {
  apps: AppItem[];
  total: number;
}

export function AppsGallery(): JSX.Element {
  const [apps, setApps] = useState<AppItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Archived section state
  const [archivedApps, setArchivedApps] = useState<AppItem[]>([]);
  const [showArchived, setShowArchived] = useState<boolean>(false);
  const [archivedLoading, setArchivedLoading] = useState<boolean>(false);
  const [archivedFetched, setArchivedFetched] = useState<boolean>(false);
  const syncPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const storeOpenApp = useAppStore((s) => s.openApp);

  const fetchApps = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    const resp = await apiRequest<AppsListResponse>("/apps");
    if (resp.error || !resp.data) {
      setError(resp.error ?? "Failed to load apps");
    } else {
      setApps(resp.data.apps);
    }
    setLoading(false);
  }, []);

  const fetchArchivedApps = useCallback(async (): Promise<void> => {
    setArchivedLoading(true);
    const resp = await apiRequest<AppsListResponse>("/apps?archived=true");
    if (!resp.error && resp.data) {
      setArchivedApps(resp.data.apps);
    }
    setArchivedLoading(false);
    setArchivedFetched(true);
  }, []);

  useEffect(() => {
    void fetchApps();
    preloadAppCdnLibs();
  }, [fetchApps]);

  useEffect(() => () => {
    if (syncPollTimeoutRef.current) {
      clearTimeout(syncPollTimeoutRef.current);
    }
  }, []);

  const scheduleBackendSync = useCallback((): void => {
    if (syncPollTimeoutRef.current) {
      clearTimeout(syncPollTimeoutRef.current);
    }

    let remainingPolls = 3;
    const poll = async (): Promise<void> => {
      const [activeResp, archivedResp] = await Promise.all([
        apiRequest<AppsListResponse>("/apps"),
        apiRequest<AppsListResponse>("/apps?archived=true"),
      ]);

      if (!activeResp.error && activeResp.data) {
        setApps(activeResp.data.apps);
      }
      if (!archivedResp.error && archivedResp.data) {
        setArchivedApps(archivedResp.data.apps);
        setArchivedFetched(true);
      }

      remainingPolls -= 1;
      if (remainingPolls > 0) {
        syncPollTimeoutRef.current = setTimeout(() => {
          void poll();
        }, 3000);
      }
    };

    syncPollTimeoutRef.current = setTimeout(() => {
      void poll();
    }, 1500);
  }, []);

  const organization = useAppStore((s) => s.organization);
  const organizations = useAppStore((s) => s.organizations);
  const orgHandle: string | null =
    organization?.handle ??
    (organization?.id ? organizations.find((o) => o.id === organization.id)?.handle ?? null : null) ??
    null;
  const pathPrefix: string = orgHandle ? `/${orgHandle}` : "";

  const openApp = (appId: string): void => {
    storeOpenApp(appId);
    window.history.pushState(null, "", `${pathPrefix}/apps/${appId}`);
  };

  const handleArchive = async (appId: string): Promise<void> => {
    const resp = await apiRequest<{ status: string }>(`/apps/${appId}/archive`, { method: "POST" });
    if (resp.error) {
      setError(resp.error);
      return;
    }

    let archivedApp: AppItem | null = null;
    setApps((prev) => {
      const match = prev.find((a) => a.id === appId) ?? null;
      if (match) {
        archivedApp = {
          ...match,
          archived_at: new Date().toISOString(),
        };
      }
      return prev.filter((a) => a.id !== appId);
    });

    if (archivedApp) {
      setArchivedApps((prev) => [archivedApp as AppItem, ...prev.filter((a) => a.id !== appId)]);
      setArchivedFetched(true);
    } else {
      // Fallback when app was not in local state
      setArchivedFetched(false);
    }

    scheduleBackendSync();

  };

  const handleUnarchive = async (appId: string): Promise<void> => {
    const resp = await apiRequest<{ status: string }>(`/apps/${appId}/unarchive`, { method: "POST" });
    if (resp.error) {
      setError(resp.error);
      return;
    }

    let restoredApp: AppItem | null = null;
    setArchivedApps((prev) => {
      const match = prev.find((a) => a.id === appId) ?? null;
      if (match) {
        restoredApp = {
          ...match,
          archived_at: null,
        };
      }
      return prev.filter((a) => a.id !== appId);
    });

    if (restoredApp) {
      setApps((prev) => [restoredApp as AppItem, ...prev.filter((a) => a.id !== appId)]);
    }

    scheduleBackendSync();
  };

  const toggleArchived = (): void => {
    const next = !showArchived;
    setShowArchived(next);
    if (next && !archivedFetched) {
      void fetchArchivedApps();
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-3xl mx-auto p-6">
        <div className="p-4 rounded-lg bg-red-900/20 border border-red-700 text-red-300 text-sm">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-6 overflow-auto flex-1">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-surface-100">Apps</h1>
          <p className="text-sm text-surface-400 mt-1">
            Interactive dashboards and data views created by Basebase
          </p>
        </div>
        <span className="text-sm text-surface-500">
          {apps.length} app{apps.length !== 1 ? "s" : ""}
        </span>
      </div>

      {apps.length === 0 ? (
        <div className="text-center py-16">
          <svg
            className="w-12 h-12 text-surface-600 mx-auto mb-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2"
            />
          </svg>
          <p className="text-surface-400 mb-2">No apps yet</p>
          <p className="text-surface-500 text-sm">
            Ask Basebase to create an interactive chart or dashboard in chat
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {apps.map((app) => (
            <div key={app.id} className="relative group">
              {/* Archive button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  void handleArchive(app.id);
                }}
                title="Archive app"
                className="absolute top-2 right-2 z-10 p-1.5 rounded-md text-surface-500 opacity-0 group-hover:opacity-100 hover:text-surface-200 hover:bg-surface-700 transition-all"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
                </svg>
              </button>

              <AppPreview
                appId={app.id}
                appTitle={app.title ?? "Untitled App"}
                widgetConfig={app.widget_config}
                onClick={() => openApp(app.id)}
              />

              {/* Name and metadata below the card */}
              <div className="mt-1.5 px-1">
                <div className="flex items-center gap-2 min-w-0">
                  <div className="text-sm font-medium text-surface-200 truncate">
                    {app.title ?? "Untitled App"}
                  </div>
                  <VisibilityBadge visibility={app.visibility ?? "team"} />
                </div>
                <div className="flex items-center gap-1.5 text-xs text-surface-500">
                  {app.creator_name && <span>{app.creator_name}</span>}
                  {app.created_at && (
                    <>
                      <span className="text-surface-600">&middot;</span>
                      <span>{new Date(app.created_at).toLocaleDateString()}</span>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Archived section */}
      <div className="mt-8 border-t border-surface-700/50 pt-4">
        <button
          onClick={toggleArchived}
          className="flex items-center gap-2 text-sm text-surface-400 hover:text-surface-200 transition-colors"
        >
          <svg
            className={`w-4 h-4 transition-transform ${showArchived ? "rotate-90" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          Archived
          {archivedFetched && (
            <span className="text-surface-500">({archivedApps.length})</span>
          )}
        </button>

        {showArchived && (
          <div className="mt-4">
            {archivedLoading ? (
              <div className="flex items-center justify-center py-8">
                <div className="animate-spin w-6 h-6 border-2 border-surface-500 border-t-primary-500 rounded-full" />
              </div>
            ) : archivedApps.length === 0 ? (
              <p className="text-sm text-surface-500 py-4">No archived apps</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {archivedApps.map((app) => (
                  <div
                    key={app.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => openApp(app.id)}
                    onKeyDown={(e) => { if (e.key === "Enter") openApp(app.id); }}
                    className="relative text-left p-4 rounded-lg bg-surface-800/50 border border-surface-700/50 hover:border-surface-600 hover:bg-surface-800/70 transition-all group opacity-60 hover:opacity-90 cursor-pointer"
                  >
                    {/* Unarchive button */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleUnarchive(app.id);
                      }}
                      title="Unarchive app"
                      className="absolute top-2 right-2 p-1.5 rounded-md text-surface-500 opacity-0 group-hover:opacity-100 hover:text-surface-200 hover:bg-surface-700 transition-all"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"
                        />
                      </svg>
                    </button>

                    <div className="flex items-start gap-3">
                      <div className="w-9 h-9 rounded-lg bg-surface-700/50 flex items-center justify-center flex-shrink-0 mt-0.5">
                        <svg
                          className="w-5 h-5 text-surface-500"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                          />
                        </svg>
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 min-w-0">
                          <h3 className="text-sm font-medium text-surface-300 group-hover:text-surface-100 transition-colors truncate max-w-[35ch]">
                            {app.title ?? "Untitled App"}
                          </h3>
                          <VisibilityBadge visibility={app.visibility ?? "team"} />
                        </div>
                        {app.description && (
                          <p className="text-xs text-surface-500 mt-1 line-clamp-2">
                            {app.description}
                          </p>
                        )}
                        <div className="flex items-center gap-2 mt-2 text-xs text-surface-500">
                          {app.creator_name && <span>{app.creator_name}</span>}
                          {app.created_at && (
                            <>
                              <span className="text-surface-600">&middot;</span>
                              <span>
                                {new Date(app.created_at).toLocaleDateString()}
                              </span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
