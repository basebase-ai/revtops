/**
 * Home view - shows deals in the default pipeline.
 */

import { useEffect, useState } from 'react';
import { API_BASE } from '../lib/api';
import { useAppStore } from '../store';

interface Deal {
  id: string;
  name: string;
  amount: number | null;
  stage: string | null;
  close_date: string | null;
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

export function Home(): JSX.Element {
  const organization = useAppStore((state) => state.organization);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!organization?.id) return;

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);

      try {
        // First get pipelines to find the default one
        const pipelinesRes = await fetch(
          `${API_BASE}/deals/pipelines?organization_id=${organization.id}`,
          { credentials: 'include' }
        );

        if (pipelinesRes.ok) {
          const pipelinesData = await pipelinesRes.json() as PipelinesApiResponse;
          const defaultPipeline = pipelinesData.pipelines.find((p) => p.is_default);
          if (defaultPipeline) {
            setPipeline(defaultPipeline);
          }
        }

        // Fetch deals (default pipeline only)
        const dealsRes = await fetch(
          `${API_BASE}/deals?organization_id=${organization.id}&default_only=true&limit=50`,
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
      {/* Header */}
      <header className="h-14 border-b border-surface-800 flex items-center px-6">
        <div className="flex items-center gap-3">
          <svg className="w-5 h-5 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
          </svg>
          <h1 className="text-lg font-semibold text-surface-100">
            {pipeline?.name || 'Deals'}
          </h1>
          <span className="px-2 py-0.5 text-xs font-medium bg-surface-800 text-surface-400 rounded-full">
            {deals.length} deal{deals.length !== 1 ? 's' : ''}
          </span>
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {deals.length === 0 ? (
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
          <div className="bg-surface-900 border border-surface-800 rounded-xl overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-surface-800">
                  <th className="text-left px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">
                    Deal Name
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">
                    Stage
                  </th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">
                    Amount
                  </th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wider">
                    Close Date
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-800">
                {deals.map((deal) => (
                  <tr key={deal.id} className="hover:bg-surface-800/50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="font-medium text-surface-200">{deal.name}</div>
                    </td>
                    <td className="px-4 py-3">
                      {deal.stage ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-primary-500/20 text-primary-400">
                          {deal.stage}
                        </span>
                      ) : (
                        <span className="text-surface-500">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-surface-300 tabular-nums">
                      {formatCurrency(deal.amount)}
                    </td>
                    <td className="px-4 py-3 text-right text-surface-400 text-sm">
                      {formatDate(deal.close_date)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
