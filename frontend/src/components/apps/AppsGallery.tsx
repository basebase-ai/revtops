/**
 * Apps gallery page – lists all Penny apps for the current org.
 *
 * Accessible via the "Apps" nav item in the sidebar.
 */

import { useState, useEffect, useCallback } from "react";
import { apiRequest } from "../../lib/api";
import { useAppStore } from "../../store";

interface AppItem {
  id: string;
  title: string | null;
  description: string | null;
  created_at: string | null;
  creator_name: string | null;
  creator_email: string | null;
  conversation_id: string | null;
  archived_at: string | null;
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

  const setCurrentView = useAppStore((s) => s.setCurrentView);
  const setCurrentAppId = useAppStore((s) => s.setCurrentAppId);

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
  }, [fetchApps]);

  const openApp = (appId: string): void => {
    setCurrentAppId(appId);
    setCurrentView("app-view" as never);
    window.history.pushState(null, "", `/apps/${appId}`);
  };

  const handleArchive = async (appId: string): Promise<void> => {
    const resp = await apiRequest<{ status: string }>(`/apps/${appId}/archive`, { method: "POST" });
    if (!resp.error) {
      setApps((prev) => prev.filter((a) => a.id !== appId));
      // Reset archived cache so next expand re-fetches
      setArchivedFetched(false);
      if (showArchived) {
        void fetchArchivedApps();
      }
    }
  };

  const handleUnarchive = async (appId: string): Promise<void> => {
    const resp = await apiRequest<{ status: string }>(`/apps/${appId}/unarchive`, { method: "POST" });
    if (!resp.error) {
      setArchivedApps((prev) => prev.filter((a) => a.id !== appId));
      void fetchApps();
    }
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
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-surface-100">Apps</h1>
          <p className="text-sm text-surface-400 mt-1">
            Interactive dashboards and data views created by Penny
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
            Ask Penny to create an interactive chart or dashboard in chat
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {apps.map((app) => (
            <div
              key={app.id}
              role="button"
              tabIndex={0}
              onClick={() => openApp(app.id)}
              onKeyDown={(e) => { if (e.key === "Enter") openApp(app.id); }}
              className="relative text-left p-4 rounded-lg bg-surface-800 border border-surface-700 hover:border-primary-500/50 hover:bg-surface-800/80 transition-all group cursor-pointer"
            >
              {/* Archive button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  void handleArchive(app.id);
                }}
                title="Archive app"
                className="absolute top-2 right-2 p-1.5 rounded-md text-surface-500 opacity-0 group-hover:opacity-100 hover:text-surface-200 hover:bg-surface-700 transition-all"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"
                  />
                </svg>
              </button>

              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded-lg bg-primary-500/15 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <svg
                    className="w-5 h-5 text-primary-400"
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
                  <h3 className="text-sm font-medium text-surface-100 group-hover:text-primary-300 transition-colors truncate max-w-[35ch]">
                    {app.title ?? "Untitled App"}
                  </h3>
                  {app.description && (
                    <p className="text-xs text-surface-400 mt-1 line-clamp-2">
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
                        <h3 className="text-sm font-medium text-surface-300 group-hover:text-surface-100 transition-colors truncate max-w-[35ch]">
                          {app.title ?? "Untitled App"}
                        </h3>
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
