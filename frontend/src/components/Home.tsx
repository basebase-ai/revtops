/**
 * Home view - displays either a custom app (if configured for the org)
 * or the default VP Sales pipeline view.
 *
 * A gear icon in the header lets users pick which app to show.
 */

import { useEffect, useState, useMemo, useCallback } from 'react';
import { apiRequest, API_BASE } from '../lib/api';
import { formatDateOnly } from '../lib/dates';
import { useAppStore, useIntegrations } from '../store';
import { SandpackAppRenderer } from './apps/SandpackAppRenderer';
import { HomeAppPicker } from './apps/HomeAppPicker';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface HomeAppData {
  id: string;
  title: string;
  description: string | null;
  frontendCode: string;
}

interface Deal {
  id: string;
  name: string;
  amount: number | null;
  stage: string | null;
  stage_probability: number | null;
  close_date: string | null;
  pipeline_id: string | null;
  pipeline_name: string | null;
  source_system: string | null;
  source_id: string | null;
}

interface Pipeline {
  id: string;
  name: string;
  is_default: boolean;
}

interface DealsApiResponse {
  deals: Array<{
    id: string;
    name: string;
    amount: number | null;
    stage: string | null;
    stage_probability: number | null;
    close_date: string | null;
    pipeline_id: string | null;
    pipeline_name: string | null;
    source_system?: string | null;
    source_id?: string | null;
  }>;
  total: number;
}

interface PipelinesApiResponse {
  pipelines: Array<{
    id: string;
    name: string;
    is_default: boolean;
  }>;
  total: number;
}

const DEFAULT_STAGE_PROBABILITY = 50;

interface PipelineWithDeals {
  pipeline: Pipeline;
  deals: Deal[];
  totalAll: number;
  totalProbAdjusted: number;
}

// ---------------------------------------------------------------------------
// Home Component
// ---------------------------------------------------------------------------

export function Home(): JSX.Element {
  const organization = useAppStore((state) => state.organization);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const setPendingChatInput = useAppStore((state) => state.setPendingChatInput);
  const setPendingChatAutoSend = useAppStore((state) => state.setPendingChatAutoSend);
  const setCurrentView = useAppStore((state) => state.setCurrentView);

  // Home app state
  const [homeApp, setHomeApp] = useState<HomeAppData | null>(null);
  const [homeAppLoading, setHomeAppLoading] = useState<boolean>(true);
  const [showPicker, setShowPicker] = useState<boolean>(false);
  const [orgAppCount, setOrgAppCount] = useState<number>(0);

  // Pipeline view state
  const [deals, setDeals] = useState<Deal[]>([]);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
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

  // Fetch pipeline data (only when no home app is set)
  useEffect(() => {
    if (!organization?.id) return;
    if (homeAppLoading) return;
    if (homeApp !== null) {
      setLoading(false);
      return;
    }

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);

      try {
        const pipelinesRes = await fetch(
          `${API_BASE}/deals/pipelines?organization_id=${organization.id}`,
          { credentials: 'include' }
        );

        if (pipelinesRes.ok) {
          const pipelinesData = await pipelinesRes.json() as PipelinesApiResponse;
          setPipelines(pipelinesData.pipelines);
        }

        const dealsRes = await fetch(
          `${API_BASE}/deals?organization_id=${organization.id}&limit=200&open_only=true`,
          { credentials: 'include' }
        );

        if (!dealsRes.ok) {
          throw new Error('Failed to fetch deals');
        }

        const dealsData = await dealsRes.json() as DealsApiResponse;
        setDeals(
          dealsData.deals.map(
            (d): Deal => ({
              ...d,
              source_system: d.source_system ?? null,
              source_id: d.source_id ?? null,
            })
          )
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };

    void fetchData();
  }, [organization?.id, homeAppLoading, homeApp]);

  // Group deals by pipeline
  const pipelinesWithDeals = useMemo((): PipelineWithDeals[] => {
    const sortedPipelines = [...pipelines].sort((a, b) => {
      if (a.is_default && !b.is_default) return -1;
      if (!a.is_default && b.is_default) return 1;
      return a.name.localeCompare(b.name);
    });

    const pipelineIds = new Set(pipelines.map((p) => p.id));

    const result: PipelineWithDeals[] = sortedPipelines.map((pipeline) => {
      const pipelineDeals = deals.filter((deal) => deal.pipeline_id === pipeline.id);
      const totalAll = pipelineDeals.reduce((sum, d) => sum + (d.amount ?? 0), 0);
      const totalProbAdjusted = pipelineDeals.reduce(
        (sum, d) =>
          sum +
          (d.amount ?? 0) * ((d.stage_probability ?? DEFAULT_STAGE_PROBABILITY) / 100),
        0
      );
      return { pipeline, deals: pipelineDeals, totalAll, totalProbAdjusted };
    });

    const orphanedDeals = deals.filter(
      (deal) => !deal.pipeline_id || !pipelineIds.has(deal.pipeline_id)
    );
    if (orphanedDeals.length > 0) {
      const totalAll = orphanedDeals.reduce((sum, d) => sum + (d.amount ?? 0), 0);
      const totalProbAdjusted = orphanedDeals.reduce(
        (sum, d) =>
          sum +
          (d.amount ?? 0) * ((d.stage_probability ?? DEFAULT_STAGE_PROBABILITY) / 100),
        0
      );
      result.push({
        pipeline: { id: '__unassigned__', name: 'Unassigned', is_default: false },
        deals: orphanedDeals,
        totalAll,
        totalProbAdjusted,
      });
    }

    return result;
  }, [pipelines, deals]);

  const totalDeals: number = deals.length;
  const grandTotalAll = useMemo(
    () => pipelinesWithDeals.reduce((sum, p) => sum + p.totalAll, 0),
    [pipelinesWithDeals]
  );
  const grandTotalProbAdjusted = useMemo(
    () => pipelinesWithDeals.reduce((sum, p) => sum + p.totalProbAdjusted, 0),
    [pipelinesWithDeals]
  );

  const formatCurrency = (amount: number | null): string => {
    if (amount === null) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount);
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '—';
    return formatDateOnly(dateStr);
  };

  const handleDealClick = useCallback((deal: Deal) => {
    const question = `Summarize the "${deal.name}" deal${deal.pipeline_name ? ` in the ${deal.pipeline_name} pipeline` : ''}. Include current stage, next steps, and recent activity.`;
    setPendingChatInput(question);
    setPendingChatAutoSend(true);
    startNewChat();
    setCurrentView('chat');
  }, [setPendingChatAutoSend, setPendingChatInput, setCurrentView, startNewChat]);

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

  if (homeAppLoading || loading) {
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
  // Default Pipeline View
  // ---------------------------------------------------------------------------

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="text-red-400 mb-2">Failed to load deals</div>
          <div className="text-surface-500 text-sm">{error}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <header className="hidden md:flex h-14 border-b border-surface-800 items-center justify-between px-4 md:px-6">
        <h1 className="text-lg font-semibold text-surface-100">Pipelines</h1>
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
      </header>

      <div className="flex-1 overflow-auto p-4 md:p-6">
        {/* Banner: "Choose App" when org has apps, "Ask Penny" when none */}
        {orgAppCount > 0 ? (
          <div className="mb-4 md:mb-6 bg-surface-800/60 border border-surface-700 rounded-xl p-4">
            <div className="flex items-center gap-3">
              <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-primary-500/20 flex items-center justify-center">
                <svg className="w-4.5 h-4.5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-surface-300 text-sm">
                  <span className="font-medium text-surface-200">Customize your Home tab</span>
                  {' '}— replace this view with any app from the Apps gallery.
                </p>
              </div>
              <button
                onClick={() => setShowPicker(true)}
                className="flex-shrink-0 px-3 py-1.5 bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium rounded-lg transition-colors"
              >
                Choose App
              </button>
            </div>
          </div>
        ) : (
          <div className="mb-4 md:mb-6 bg-surface-800/60 border border-surface-700 rounded-xl p-4">
            <div className="flex items-center gap-3">
              <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-primary-500/20 flex items-center justify-center">
                <svg className="w-4.5 h-4.5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-surface-300 text-sm">
                  <span className="font-medium text-surface-200">Make this Home tab your own</span>
                  {' '}— ask Penny to build a custom dashboard and it will appear here for your whole team.
                </p>
              </div>
              <button
                onClick={() => {
                  setPendingChatInput('Create a dashboard app for our Home tab with key pipeline metrics, deals by stage, and upcoming closes.');
                  setPendingChatAutoSend(false);
                  startNewChat();
                  setCurrentView('chat');
                }}
                className="flex-shrink-0 px-3 py-1.5 bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium rounded-lg transition-colors"
              >
                Ask Penny
              </button>
            </div>
          </div>
        )}

        {/* Connect data sources banner */}
        {!hasConnectedSources && (
          <div className="mb-4 md:mb-6 bg-gradient-to-r from-primary-500/20 to-primary-600/10 border border-primary-500/30 rounded-xl p-4 md:p-5">
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

        {pipelinesWithDeals.length === 0 && totalDeals === 0 ? (
          <div className="text-center py-12">
            <svg className="w-12 h-12 text-surface-600 mx-auto mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
            </svg>
            <h3 className="text-surface-300 font-medium mb-1">No deals yet</h3>
            <p className="text-surface-500 text-sm">
              Connect your CRM and sync data to see deals here.
            </p>
          </div>
        ) : (
          <>
            {/* VP-first: total pipeline value at top */}
            {totalDeals > 0 && (
              <section className="mb-6 md:mb-8">
                <h2 className="text-sm font-medium text-surface-500 uppercase tracking-wider mb-3">Pipeline value (open deals)</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                  <div className="bg-surface-900 border border-surface-800 rounded-xl p-5">
                    <div className="text-xs text-surface-500 mb-1">If all close</div>
                    <div className="text-2xl md:text-3xl font-semibold text-surface-100 tabular-nums">{formatCurrency(grandTotalAll)}</div>
                  </div>
                  <div className="bg-surface-900 border border-surface-800 rounded-xl p-5">
                    <div className="text-xs text-surface-500 mb-1">Probability-adjusted</div>
                    <div className="text-2xl md:text-3xl font-semibold text-surface-100 tabular-nums">{formatCurrency(grandTotalProbAdjusted)}</div>
                  </div>
                </div>
                <div className="flex flex-wrap gap-3">
                  {pipelinesWithDeals
                    .filter((p) => p.deals.length > 0)
                    .map(({ pipeline, deals: pipelineDeals, totalAll: pipeTotalAll, totalProbAdjusted: pipeProbAdjusted }) => (
                      <div
                        key={pipeline.id}
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-800/80 border border-surface-700 text-sm"
                      >
                        <span className="font-medium text-surface-200">{pipeline.name}</span>
                        <span className="text-surface-500">
                          {pipelineDeals.length} deal{pipelineDeals.length !== 1 ? 's' : ''}
                        </span>
                        <span className="text-surface-400 tabular-nums">{formatCurrency(pipeTotalAll)}</span>
                        <span className="text-surface-500">/</span>
                        <span className="text-surface-300 tabular-nums">{formatCurrency(pipeProbAdjusted)}</span>
                      </div>
                    ))}
                </div>
              </section>
            )}

            {/* Deal list */}
            <section>
              <h2 className="text-sm font-medium text-surface-500 uppercase tracking-wider mb-3">
                Deal list — {totalDeals} open deal{totalDeals !== 1 ? 's' : ''}
              </h2>
              <div className="space-y-6">
                {pipelinesWithDeals.map(({ pipeline, deals: pipelineDeals, totalAll: pipeTotalAll, totalProbAdjusted: pipeProbAdjusted }) => (
                  <div key={pipeline.id} className="bg-surface-900 border border-surface-800 rounded-xl overflow-hidden">
                    <div className="px-4 py-3 border-b border-surface-800 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <h3 className="text-base font-semibold text-surface-100">{pipeline.name}</h3>
                        {pipeline.is_default && (
                          <span className="px-1.5 py-0.5 text-[10px] font-medium bg-primary-500/20 text-primary-400 rounded">Default</span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-surface-400">
                        <span>{pipelineDeals.length} deal{pipelineDeals.length !== 1 ? 's' : ''}</span>
                        {pipelineDeals.length > 0 && (
                          <>
                            <span className="tabular-nums">{formatCurrency(pipeTotalAll)}</span>
                            <span className="text-surface-500">/</span>
                            <span className="tabular-nums">{formatCurrency(pipeProbAdjusted)}</span>
                          </>
                        )}
                      </div>
                    </div>

                    {pipelineDeals.length === 0 ? (
                      <div className="px-4 py-8 text-center text-surface-500 text-sm">No deals in this pipeline</div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full min-w-[400px]">
                          <thead>
                            <tr className="border-b border-surface-800">
                              <th className="text-left px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">Deal</th>
                              <th className="text-left px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider hidden sm:table-cell">Stage</th>
                              <th className="text-right px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">Amount</th>
                              <th className="text-right px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider hidden sm:table-cell">Close</th>
                              <th className="w-24 px-3 md:px-4 py-3 text-right text-xs font-medium text-surface-500 uppercase tracking-wider" />
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-surface-800">
                            {pipelineDeals.map((deal) => (
                              <tr key={deal.id} className="hover:bg-surface-800/50 transition-colors">
                                <td className="px-3 md:px-4 py-3">
                                  <div className="font-medium text-surface-200 truncate max-w-[200px] md:max-w-none">{deal.name}</div>
                                </td>
                                <td className="px-3 md:px-4 py-3 hidden sm:table-cell">
                                  {deal.stage ? (
                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-primary-500/20 text-primary-400">{deal.stage}</span>
                                  ) : (
                                    <span className="text-surface-500">—</span>
                                  )}
                                </td>
                                <td className="px-3 md:px-4 py-3 text-right text-surface-300 tabular-nums">{formatCurrency(deal.amount)}</td>
                                <td className="px-3 md:px-4 py-3 text-right text-surface-400 text-sm hidden sm:table-cell">{formatDate(deal.close_date)}</td>
                                <td className="px-3 md:px-4 py-3 text-right">
                                  <button
                                    type="button"
                                    onClick={() => handleDealClick(deal)}
                                    className="text-xs font-medium text-primary-400 hover:text-primary-300"
                                  >
                                    View details
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
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
