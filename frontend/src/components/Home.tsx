/**
 * Home view - VP Sales lens: total pipeline value first, deal details below.
 * Open deals only (excludes closed won/lost). Deal rows link to details (chat or CRM).
 */

import { useEffect, useState, useMemo, useCallback } from 'react';
import { API_BASE } from '../lib/api';
import { formatDateOnly } from '../lib/dates';
import { useAppStore, useIntegrations } from '../store';

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

export function Home(): JSX.Element {
  const organization = useAppStore((state) => state.organization);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const setPendingChatInput = useAppStore((state) => state.setPendingChatInput);
  const setPendingChatAutoSend = useAppStore((state) => state.setPendingChatAutoSend);
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Check if the organization has any connected data sources (from Zustand store)
  const integrations = useIntegrations();
  const hasConnectedSources = integrations.some((i) => i.isActive);

  useEffect(() => {
    if (!organization?.id) return;

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);

      try {
        // Fetch all pipelines
        const pipelinesRes = await fetch(
          `${API_BASE}/deals/pipelines?organization_id=${organization.id}`,
          { credentials: 'include' }
        );

        if (pipelinesRes.ok) {
          const pipelinesData = await pipelinesRes.json() as PipelinesApiResponse;
          setPipelines(pipelinesData.pipelines);
        }

        // Fetch all open deals (not just default pipeline, exclude closed won/lost)
        const dealsRes = await fetch(
          `${API_BASE}/deals?organization_id=${organization.id}&limit=200&open_only=true`,
          { credentials: 'include' }
        );

        if (!dealsRes.ok) {
          throw new Error('Failed to fetch deals');
        }

        const dealsData = await dealsRes.json() as DealsApiResponse;
        setDeals(dealsData.deals);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };

    void fetchData();
  }, [organization?.id]);

  // Group deals by pipeline and compute value totals (all vs probability-adjusted)
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
      return {
        pipeline,
        deals: pipelineDeals,
        totalAll,
        totalProbAdjusted,
      };
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

  const totalDeals = deals.length;
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
    console.log('[Home] Starting deal summary chat for:', {
      dealId: deal.id,
      dealName: deal.name,
      pipelineName: deal.pipeline_name,
    });
    setPendingChatInput(question);
    setPendingChatAutoSend(true);
    startNewChat();
    setCurrentView('chat');
  }, [setPendingChatAutoSend, setPendingChatInput, setCurrentView, startNewChat]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex items-center gap-3 text-surface-400">
          <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span>Loading deals...</span>
        </div>
      </div>
    );
  }

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
      <header className="hidden md:flex h-14 border-b border-surface-800 items-center px-4 md:px-6">
        <h1 className="text-lg font-semibold text-surface-100">Pipelines</h1>
      </header>

      <div className="flex-1 overflow-auto p-4 md:p-6">
        {/* Connect data sources banner - only shown when no sources connected */}
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
                    // Navigate to data sources - dispatch a custom event that Sidebar listens to
                    window.dispatchEvent(new CustomEvent('navigate', { detail: 'data-sources' }));
                  }}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium rounded-lg transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                  </svg>
                  Connect Data Sources
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

            {/* Deal list: details below, with link to view details (chat) */}
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
    </div>
  );
}
