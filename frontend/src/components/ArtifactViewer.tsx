/**
 * Artifact viewer component for displaying dashboards/reports.
 *
 * For MVP, displays JSON data in a formatted view.
 */

interface ArtifactViewerProps {
  artifact: {
    id: string;
    type: string;
    title: string;
    data: Record<string, unknown>;
  };
}

export function ArtifactViewer({ artifact }: ArtifactViewerProps): JSX.Element {
  return (
    <div className="h-full overflow-auto">
      {/* Type badge */}
      <div className="mb-4">
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-primary-900 text-primary-200">
          {artifact.type}
        </span>
      </div>

      {/* Data display */}
      <div className="space-y-4">
        {renderArtifactData(artifact.data)}
      </div>
    </div>
  );
}

function renderArtifactData(data: Record<string, unknown>): JSX.Element {
  // Check if this is a deals summary
  if ('count' in data && 'deals' in data) {
    return <DealsView data={data as unknown as DealsData} />;
  }

  // Check if this is an accounts summary
  if ('count' in data && 'accounts' in data) {
    return <AccountsView data={data as unknown as AccountsData} />;
  }

  // Check if this is a pipeline summary
  if ('by_stage' in data) {
    return <PipelineView data={data as unknown as PipelineData} />;
  }

  // Default: JSON view
  return <JsonView data={data} />;
}

// Deal types
interface Deal {
  id: string;
  name: string;
  amount: number | null;
  stage: string | null;
  close_date: string | null;
}

interface DealsData {
  count: number;
  deals: Deal[];
}

function DealsView({ data }: { data: DealsData }): JSX.Element {
  return (
    <div>
      <div className="text-sm text-surface-400 mb-3">
        {data.count} deal{data.count !== 1 ? 's' : ''} found
      </div>
      <div className="space-y-2">
        {data.deals.map((deal) => (
          <div
            key={deal.id}
            className="p-3 rounded-lg bg-surface-800 border border-surface-700"
          >
            <div className="font-medium text-surface-100">{deal.name}</div>
            <div className="flex items-center gap-4 mt-1 text-sm">
              {deal.amount && (
                <span className="text-green-400">
                  ${deal.amount.toLocaleString()}
                </span>
              )}
              {deal.stage && (
                <span className="text-surface-400">{deal.stage}</span>
              )}
              {deal.close_date && (
                <span className="text-surface-500">
                  Closes {new Date(deal.close_date).toLocaleDateString()}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Account types
interface Account {
  id: string;
  name: string;
  industry: string | null;
  annual_revenue: number | null;
}

interface AccountsData {
  count: number;
  accounts: Account[];
}

function AccountsView({ data }: { data: AccountsData }): JSX.Element {
  return (
    <div>
      <div className="text-sm text-surface-400 mb-3">
        {data.count} account{data.count !== 1 ? 's' : ''} found
      </div>
      <div className="space-y-2">
        {data.accounts.map((account) => (
          <div
            key={account.id}
            className="p-3 rounded-lg bg-surface-800 border border-surface-700"
          >
            <div className="font-medium text-surface-100">{account.name}</div>
            <div className="flex items-center gap-4 mt-1 text-sm">
              {account.industry && (
                <span className="text-surface-400">{account.industry}</span>
              )}
              {account.annual_revenue && (
                <span className="text-green-400">
                  ${(account.annual_revenue / 1000000).toFixed(1)}M ARR
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Pipeline types
interface StageData {
  count: number;
  total_amount: number;
  avg_amount: number;
}

interface PipelineData {
  by_stage: Record<string, StageData>;
  total_deals: number;
  total_pipeline_value: number;
}

function PipelineView({ data }: { data: PipelineData }): JSX.Element {
  const stages = Object.entries(data.by_stage);

  return (
    <div>
      {/* Summary */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="p-4 rounded-lg bg-surface-800 border border-surface-700">
          <div className="text-2xl font-bold text-surface-100">
            {data.total_deals}
          </div>
          <div className="text-sm text-surface-400">Total Deals</div>
        </div>
        <div className="p-4 rounded-lg bg-surface-800 border border-surface-700">
          <div className="text-2xl font-bold text-green-400">
            ${(data.total_pipeline_value / 1000000).toFixed(1)}M
          </div>
          <div className="text-sm text-surface-400">Pipeline Value</div>
        </div>
      </div>

      {/* By stage */}
      <div className="space-y-2">
        {stages.map(([stage, stageData]) => (
          <div
            key={stage}
            className="p-3 rounded-lg bg-surface-800 border border-surface-700"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-surface-100">{stage}</span>
              <span className="text-surface-400">{stageData.count} deals</span>
            </div>
            <div className="mt-1 text-sm text-green-400">
              ${stageData.total_amount.toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function JsonView({ data }: { data: Record<string, unknown> }): JSX.Element {
  return (
    <pre className="p-4 rounded-lg bg-surface-800 text-surface-300 text-sm overflow-auto font-mono">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
