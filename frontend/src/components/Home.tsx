/**
 * Home view - displays either a custom app (if configured for the org)
 * or the semantic workstream map (clusters of team conversations by topic).
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchWorkstreams } from '../api/workstreams';
import { apiRequest } from '../lib/api';
import { useAppStore, useIntegrations } from '../store';
import type { WidgetData, WorkstreamsResponse } from '../store/types';
import { SandpackAppRenderer } from './apps/SandpackAppRenderer';
import { HomeAppPicker } from './apps/HomeAppPicker';
import { WidgetGrid } from './widgets/WidgetGrid';
import { WorkstreamGrid } from './WorkstreamGrid';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface HomeAppData {
  id: string;
  title: string;
  description: string | null;
  frontendCode: string;
  frontendCodeCompiled?: string | null;
}

// ---------------------------------------------------------------------------
// Home Component
// ---------------------------------------------------------------------------

const WORKSTREAM_WINDOW_OPTIONS: { value: number; label: string }[] = [
  { value: 24, label: '24h' },
  { value: 168, label: '7d' },
  { value: 720, label: '30d' },
];

export function Home(): JSX.Element {
  const organization = useAppStore((state) => state.organization);
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const setCurrentChatId = useAppStore((state) => state.setCurrentChatId);

  // Home app state
  const [homeApp, setHomeApp] = useState<HomeAppData | null>(null);
  const [homeAppLoading, setHomeAppLoading] = useState<boolean>(true);
  const [showPicker, setShowPicker] = useState<boolean>(false);
  const [orgAppCount, setOrgAppCount] = useState<number>(0);

  // Widget state
  const [widgets, setWidgets] = useState<WidgetData[]>([]);

  // Workstream map state (default view when no custom app)
  const [workstreamWindow, setWorkstreamWindow] = useState<number>(24);
  const [workstreamData, setWorkstreamData] = useState<WorkstreamsResponse | null>(null);
  const [workstreamLoading, setWorkstreamLoading] = useState<boolean>(false);
  const [workstreamError, setWorkstreamError] = useState<string | null>(null);

  
  const integrations = useIntegrations();
  const hasConnectedSources: boolean = integrations.some((i) => i.isActive);

  // Fetch the home app preference + org app count (re-run on org switch)
  useEffect(() => {
    setHomeApp(null);
    setHomeAppLoading(true);
    setOrgAppCount(0);
    const fetchHomeApp = async (): Promise<void> => {
      const resp = await apiRequest<{ app: HomeAppData | null; app_count: number }>('/apps/home');
      if (resp.data) {
        setHomeApp(resp.data.app);
        setOrgAppCount(resp.data.app_count);
      }
      setHomeAppLoading(false);
    };
    void fetchHomeApp();
  }, [organization?.id]);

  // Fetch workstreams for map (when no custom home app)
  const fetchWorkstreamsData = useCallback(async (): Promise<void> => {
    if (!organization?.id) return;
    setWorkstreamLoading(true);
    setWorkstreamError(null);
    const { data, error: err } = await fetchWorkstreams(workstreamWindow);
    if (err) {
      setWorkstreamError(err);
      setWorkstreamData(null);
    } else if (data) {
      setWorkstreamData(data);
    }
    setWorkstreamLoading(false);
  }, [organization?.id, workstreamWindow]);

  useEffect(() => {
    if (!organization?.id || homeApp !== null || homeAppLoading) return;
    void fetchWorkstreamsData();
    // Fetch widgets + lazy-generate for apps that don't have one yet
    const loadWidgets = async (): Promise<void> => {
      // 1. Load existing widgets
      const widgetResp = await apiRequest<{ widgets: WidgetData[] }>('/apps/widgets/all');
      if (widgetResp.data) setWidgets(widgetResp.data.widgets);

      // 2. Find apps without widgets and generate in background
      const appsResp = await apiRequest<{ apps: Array<{ id: string; title: string; widget_config: unknown | null; archived_at: string | null }> }>('/apps');
      if (!appsResp.data) return;
      const appsWithoutWidgets = appsResp.data.apps.filter(
        (a) => !a.archived_at && !a.widget_config
      );
      // Generate widgets one at a time to avoid hammering the DB and LLM
      for (const app of appsWithoutWidgets) {
        try {
          const resp = await apiRequest<{ widget_config: WidgetData['widget_config'] }>(
            `/apps/${app.id}/widget`,
            { method: 'POST', body: JSON.stringify({}) }
          );
          if (resp.data?.widget_config) {
            setWidgets((prev) => [
              ...prev,
              { id: app.id, title: app.title, widget_config: resp.data!.widget_config },
            ]);
          }
        } catch {
          // Skip apps that fail to generate
        }
      }
    };
    void loadWidgets();
  }, [organization?.id, homeApp, homeAppLoading, fetchWorkstreamsData]);

  // Re-fetch workstreams when backend broadcasts workstreams_stale (e.g. after embedding update)
  useEffect(() => {
    const handler = (): void => {
      void fetchWorkstreamsData();
    };
    window.addEventListener('workstreams-stale', handler);
    return () => window.removeEventListener('workstreams-stale', handler);
  }, [fetchWorkstreamsData]);

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      setCurrentChatId(conversationId);
      setCurrentView('chat');
    },
    [setCurrentChatId, setCurrentView]
  );

  const handleWidgetClick = useCallback(
    (appId: string) => {
      // Navigate to the app view
      useAppStore.getState().openApp(appId);
      setCurrentView('app-view');
    },
    [setCurrentView]
  );

  const handleAppSelected = useCallback((appId: string | null) => {
    if (appId === null) {
      setHomeApp(null);
    }
    setShowPicker(false);
    const reload = async (): Promise<void> => {
      const resp = await apiRequest<{ app: HomeAppData | null; app_count: number }>('/apps/home');
      if (resp.data) {
        setHomeApp(resp.data.app);
        setOrgAppCount(resp.data.app_count);
      }
    };
    void reload();
  }, []);

  // ---------------------------------------------------------------------------
  // Loading state
  // ---------------------------------------------------------------------------

  if (homeAppLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex items-center gap-3 text-surface-400">
          <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span>Loading...</span>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Custom Home App view
  // ---------------------------------------------------------------------------

  if (homeApp !== null) {
    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="hidden md:flex h-14 border-b border-surface-800 items-center justify-between px-4 md:px-6">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold text-surface-100">{homeApp.title}</h1>
            <span className="px-1.5 py-0.5 text-[10px] font-medium bg-primary-500/20 text-primary-400 rounded">
              Home App
            </span>
          </div>
          <button
            onClick={() => setShowPicker(true)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md hover:bg-surface-800 text-surface-400 hover:text-surface-200 transition-colors text-xs"
            title="Customize Home"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Customize
          </button>
        </header>

        <div className="flex-1 overflow-hidden">
          <SandpackAppRenderer
            appId={homeApp.id}
            frontendCode={homeApp.frontendCode}
            frontendCodeCompiled={homeApp.frontendCodeCompiled}
          />
        </div>

        {showPicker && (
          <HomeAppPicker
            currentAppId={homeApp.id}
            onSelect={handleAppSelected}
            onClose={() => setShowPicker(false)}
          />
        )}
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Default: Workstream Map (semantic Home)
  // ---------------------------------------------------------------------------

  if (workstreamError && !workstreamData) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="text-red-400 mb-2">Failed to load workstreams</div>
          <div className="text-surface-500 text-sm">{workstreamError}</div>
          <button
            onClick={() => void fetchWorkstreamsData()}
            className="btn-secondary mt-4"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <header className="hidden md:flex h-14 border-b border-surface-800 items-center justify-between px-4 md:px-6">
        <h1 className="text-lg font-semibold text-surface-100">Home</h1>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setCurrentView('chats')}
            className="text-sm text-primary-400 hover:text-primary-300 font-medium"
          >
            View as list
          </button>
          <select
            value={workstreamWindow}
            onChange={(e) => setWorkstreamWindow(Number(e.target.value))}
            className="bg-surface-800 border border-surface-600 rounded-md px-2.5 py-1.5 text-sm text-surface-200"
            aria-label="Activity time window"
          >
            {WORKSTREAM_WINDOW_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          {orgAppCount > 0 && (
            <button
              onClick={() => setShowPicker(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md hover:bg-surface-800 text-surface-400 hover:text-surface-200 transition-colors text-xs"
              title="Customize Home"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              Customize
            </button>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-auto flex flex-col">
        <div className="pt-4 pl-2 md:pl-4 flex flex-col min-h-full">
        {/* Connect data sources banner */}
        {!hasConnectedSources && (
          <div className="mb-4 md:mb-6 mr-2 md:mr-4 bg-gradient-to-r from-primary-500/20 to-primary-600/10 border border-primary-500/30 rounded-xl p-4 md:p-5">
            <div className="flex flex-col sm:flex-row items-start gap-3 md:gap-4">
              <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-primary-500/20 flex items-center justify-center">
                <svg className="w-5 h-5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-base md:text-lg font-semibold text-surface-100 mb-1">
                  Connect your data sources to get started
                </h3>
                <p className="text-surface-400 text-sm mb-3 md:mb-4">
                  Link your CRM, calendar, and email to unlock AI-powered insights about your revenue pipeline.
                </p>
                <button
                  onClick={() => {
                    window.dispatchEvent(new CustomEvent('navigate', { detail: 'data-sources' }));
                  }}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium rounded-lg transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                  </svg>
                  Connect Integrations
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Widget grid */}
        <WidgetGrid widgets={widgets} onWidgetClick={handleWidgetClick} />

        {/* Workstream grid */}
        <div className="flex-1 min-h-[400px] overflow-hidden">
          {workstreamLoading && !workstreamData ? (
            <div className="flex items-center justify-center h-full min-h-[400px]">
              <div className="flex items-center gap-3 text-surface-400">
                <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                <span>Loading workstreams…</span>
              </div>
            </div>
          ) : workstreamData && (workstreamData.workstreams.length > 0 || workstreamData.unclustered.length > 0) ? (
            <WorkstreamGrid
              workstreams={workstreamData.workstreams}
              unclustered={workstreamData.unclustered}
              onSelectConversation={handleSelectConversation}
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center px-4">
              <svg className="w-12 h-12 text-surface-600 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <h3 className="text-surface-300 font-medium mb-1">No shared conversations yet</h3>
              <p className="text-surface-500 text-sm mb-4 max-w-sm">
                Start a shared chat with your team to see workstreams here.
              </p>
            </div>
          )}
        </div>

        {workstreamData?.computed_at && (
          <div className="mt-4 pr-2 md:pr-4 flex justify-end">
            <span className="text-xs text-surface-500">
              Updated {new Date(workstreamData.computed_at).toLocaleTimeString()}
            </span>
          </div>
        )}
        </div>
      </div>

      {showPicker && (
        <HomeAppPicker
          currentAppId={null}
          onSelect={handleAppSelected}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  );
}
