/**
 * Home view - shows all pipelines with their open deals.
 * Excludes deals that are closed won or closed lost.
 * Shows a prominent banner to connect data sources if none are connected.
 */

import { useEffect, useState, useMemo, useCallback } from 'react';
import { API_BASE } from '../lib/api';
import { useAppStore } from '../store';
import { useIntegrations } from '../hooks/useIntegrations';

interface Deal {
  id: string;
  name: string;
  amount: number | null;
  stage: string | null;
  close_date: string | null;
  pipeline_id: string | null;
  pipeline_name: string | null;
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
    close_date: string | null;
    pipeline_id: string | null;
    pipeline_name: string | null;
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

interface PipelineWithDeals {
  pipeline: Pipeline;
  deals: Deal[];
}

export function Home(): JSX.Element {
  const organization = useAppStore((state) => state.organization);
  const user = useAppStore((state) => state.user);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const setPendingChatInput = useAppStore((state) => state.setPendingChatInput);
  const setPendingChatAutoSend = useAppStore((state) => state.setPendingChatAutoSend);
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Check if the organization has any connected data sources
  const { data: integrations } = useIntegrations(organization?.id ?? null, user?.id ?? null);
  const hasConnectedSources = integrations?.some((i) => i.isActive) ?? false;

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

  // Group deals by pipeline, with default pipeline first
  const pipelinesWithDeals = useMemo((): PipelineWithDeals[] => {
    // Sort pipelines: default first, then alphabetically
    const sortedPipelines = [...pipelines].sort((a, b) => {
      if (a.is_default && !b.is_default) return -1;
      if (!a.is_default && b.is_default) return 1;
      return a.name.localeCompare(b.name);
    });

    const pipelineIds = new Set(pipelines.map((p) => p.id));
    
    const result: PipelineWithDeals[] = sortedPipelines.map((pipeline) => ({
      pipeline,
      deals: deals.filter((deal) => deal.pipeline_id === pipeline.id),
    }));

    // Add orphaned deals (deals with no matching pipeline) to an "Unassigned" section
    const orphanedDeals = deals.filter((deal) => !deal.pipeline_id || !pipelineIds.has(deal.pipeline_id));
    if (orphanedDeals.length > 0) {
      result.push({
        pipeline: { id: '__unassigned__', name: 'Unassigned', is_default: false },
        deals: orphanedDeals,
      });
    }

    return result;
  }, [pipelines, deals]);

  const totalDeals = deals.length;

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
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
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
      {/* Header - hidden on mobile since AppLayout has mobile header */}
      <header className="hidden md:flex h-14 border-b border-surface-800 items-center px-4 md:px-6">
        <div className="flex items-center gap-3">
          <svg className="w-5 h-5 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
          </svg>
          <h1 className="text-lg font-semibold text-surface-100">
            Pipelines
          </h1>
          <span className="px-2 py-0.5 text-xs font-medium bg-surface-800 text-surface-400 rounded-full">
            {totalDeals} open deal{totalDeals !== 1 ? 's' : ''}
          </span>
        </div>
      </header>

      {/* Content */}
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
          <div className="space-y-6">
            {pipelinesWithDeals.map(({ pipeline, deals: pipelineDeals }) => (
              <div key={pipeline.id} className="bg-surface-900 border border-surface-800 rounded-xl overflow-hidden">
                {/* Pipeline header */}
                <div className="px-4 py-3 border-b border-surface-800 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h2 className="text-base font-semibold text-surface-100">{pipeline.name}</h2>
                    {pipeline.is_default && (
                      <span className="px-1.5 py-0.5 text-[10px] font-medium bg-primary-500/20 text-primary-400 rounded">
                        Default
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-surface-500">
                    {pipelineDeals.length} deal{pipelineDeals.length !== 1 ? 's' : ''}
                  </span>
                </div>

                {pipelineDeals.length === 0 ? (
                  <div className="px-4 py-8 text-center">
                    <p className="text-surface-500 text-sm">No deals in this pipeline</p>
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[400px]">
                      <thead>
                        <tr className="border-b border-surface-800">
                          <th className="text-left px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">
                            Deal Name
                          </th>
                          <th className="text-left px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider hidden sm:table-cell">
                            Stage
                          </th>
                          <th className="text-right px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">
                            Amount
                          </th>
                          <th className="text-right px-3 md:px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider hidden sm:table-cell">
                            Close Date
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-surface-800">
                        {pipelineDeals.map((deal) => (
                          <tr
                            key={deal.id}
                            className="hover:bg-surface-800/50 transition-colors cursor-pointer"
                            onClick={() => handleDealClick(deal)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                handleDealClick(deal);
                              }
                            }}
                            role="button"
                            tabIndex={0}
                          >
                            <td className="px-3 md:px-4 py-3">
                              <div className="font-medium text-surface-200 truncate max-w-[200px] md:max-w-none">{deal.name}</div>
                            </td>
                            <td className="px-3 md:px-4 py-3 hidden sm:table-cell">
                              {deal.stage ? (
                                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-primary-500/20 text-primary-400">
                                  {deal.stage}
                                </span>
                              ) : (
                                <span className="text-surface-500">—</span>
                              )}
                            </td>
                            <td className="px-3 md:px-4 py-3 text-right text-surface-300 tabular-nums">
                              {formatCurrency(deal.amount)}
                            </td>
                            <td className="px-3 md:px-4 py-3 text-right text-surface-400 text-sm hidden sm:table-cell">
                              {formatDate(deal.close_date)}
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
        )}
      </div>
    </div>
  );
}
