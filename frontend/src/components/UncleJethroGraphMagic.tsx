import { useEffect, useMemo, useState } from 'react';
import { Cosmograph } from '@cosmograph/react';
import { apiRequest } from '../lib/api';
import { useAuthStore, type UserOrganization } from '../store';

const MAX_RANGE_DAYS = 30;
const ROYGBIV = ['#e11d48', '#f97316', '#facc15', '#22c55e', '#3b82f6', '#6366f1', '#a855f7'];

type GraphNode = { id: string; label: string; heat: number; mention_count?: number; source?: string; centrality?: number; color?: string };
type GraphEdge = { source: string; target: string; weight: number };

type GraphResponse = {
  organization_id: string;
  graph_date: string;
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  run_metadata: { coverage?: { partial?: boolean; warning_text?: string } };
};

type AdminOrganization = {
  id: string;
  name: string;
};

export function UncleJethroGraphMagic(): JSX.Element {
  const orgMemberships: UserOrganization[] = useAuthStore((state) => state.organizations);
  const [orgId, setOrgId] = useState('');
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10));
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().slice(0, 10));
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nodeId, setNodeId] = useState<string | null>(null);
  const [snippets, setSnippets] = useState<Array<{ ref: string; snippet: string; event_time: string; source_display?: string }>>([]);
  const [availableOrgs, setAvailableOrgs] = useState<AdminOrganization[]>([]);

  const partialWarning = graph?.run_metadata?.coverage?.partial ? 'Partial data: some sources failed' : null;

  useEffect(() => {
    const fetchOrganizations = async (): Promise<void> => {
      const { data, error: requestError } = await apiRequest<{ organizations: AdminOrganization[] }>(
        '/waitlist/admin/organizations?limit=1000',
      );
      if (requestError || !data?.organizations?.length) {
        console.debug('[UJ Graph Magic] Falling back to org memberships for org dropdown', {
          requestError,
          membershipCount: orgMemberships.length,
        });
        const fallbackOrgs: AdminOrganization[] = orgMemberships.map((org) => ({ id: org.id, name: org.name }));
        setAvailableOrgs(fallbackOrgs);
        const firstFallbackOrg = fallbackOrgs[0];
        if (!orgId && firstFallbackOrg) {
          setOrgId(firstFallbackOrg.id);
        }
        return;
      }

      const sortedOrgs: AdminOrganization[] = [...data.organizations].sort((a, b) => a.name.localeCompare(b.name));
      setAvailableOrgs(sortedOrgs);
      const firstSortedOrg = sortedOrgs[0];
      if (!orgId && firstSortedOrg) {
        setOrgId(firstSortedOrg.id);
      }
    };

    void fetchOrganizations();
  }, [orgMemberships, orgId]);

  const canRebuild = useMemo(() => {
    if (!orgId) return false;
    const a = new Date(startDate);
    const b = new Date(endDate);
    const diff = Math.floor((b.getTime() - a.getTime()) / 86400000) + 1;
    return diff > 0 && diff <= MAX_RANGE_DAYS;
  }, [orgId, startDate, endDate]);

  const fetchGraph = async (): Promise<void> => {
    if (!orgId) return;
    console.debug('[UJ Graph Magic] Fetching graph snapshot', { orgId, selectedDate });
    const { data, error: reqErr } = await apiRequest<GraphResponse>(`/admin-topic-graph/${orgId}/${selectedDate}`);
    if (reqErr || !data) {
      setError(reqErr ?? 'Failed to load graph');
      return;
    }
    setError(null);
    setGraph(data);
  };

  useEffect(() => {
    void fetchGraph();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, selectedDate]);

  const rebuild = async (): Promise<void> => {
    if (!canRebuild) return;
    console.debug('[UJ Graph Magic] Rebuilding graphs for range', { orgId, startDate, endDate });
    const { error: reqErr } = await apiRequest('/admin-topic-graph/rebuild', {
      method: 'POST',
      body: JSON.stringify({ organization_id: orgId, start_date: startDate, end_date: endDate }),
    });
    if (reqErr) {
      setError(reqErr);
      return;
    }
    await fetchGraph();
  };


  const graphWithVisuals = useMemo(() => {
    if (!graph) return null;
    const nodes = graph.graph.nodes.map((node) => {
      const mentionCount = Math.max(1, Math.round(node.mention_count ?? 1));
      return {
        ...node,
        mention_count: mentionCount,
        color: ROYGBIV[Math.floor(Math.random() * ROYGBIV.length)],
      };
    });
    return { ...graph.graph, nodes, edges: graph.graph.edges };
  }, [graph]);

  const selectedNode = useMemo(() => graphWithVisuals?.nodes.find((n) => n.id === nodeId) ?? null, [graphWithVisuals, nodeId]);

  const onNodeClick = async (id: string): Promise<void> => {
    setNodeId(id);
    const { data } = await apiRequest<{ snippets: Array<{ ref: string; snippet: string; event_time: string; source_display?: string }> }>(
      `/admin-topic-graph/${orgId}/${selectedDate}/nodes/${encodeURIComponent(id)}/evidence`
    );
    setSnippets(data?.snippets ?? []);
  };

  return (
    <div className="h-full min-h-0 flex flex-col gap-4">
      <h2 className="text-xl font-semibold text-surface-50">UJ&apos;s Graph Magic</h2>
      <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
        <label className="flex flex-col gap-1 text-xs text-surface-400">
          <span>Organization</span>
          <select
            className="px-3 py-2 rounded bg-surface-800 text-surface-100"
            value={orgId}
            onChange={(e) => setOrgId(e.target.value)}
          >
            {availableOrgs.length === 0 && <option value="">No organizations available</option>}
            {availableOrgs.map((org) => (
              <option key={org.id} value={org.id}>{org.name}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-surface-400">
          <span>Selected date (graph view)</span>
          <input type="date" className="px-3 py-2 rounded bg-surface-800" value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-surface-400">
          <span>Generate start date</span>
          <input type="date" className="px-3 py-2 rounded bg-surface-800" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-surface-400">
          <span>Generate end date</span>
          <input type="date" className="px-3 py-2 rounded bg-surface-800" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <div className="flex items-end">
          <button disabled={!canRebuild} onClick={() => void rebuild()} className="w-full md:w-auto px-3 py-2 rounded bg-primary-600 disabled:opacity-40">
            Rebuild
          </button>
        </div>
      </div>
      {partialWarning && <p className="text-xs text-amber-400">Partial data: some sources failed</p>}
      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="bg-surface-900 border border-surface-800 rounded-lg p-3 flex-1 min-h-[75vh] relative">
        {graphWithVisuals ? (
          <Cosmograph
            nodes={graphWithVisuals.nodes}
            links={graphWithVisuals.edges}
            nodeLabelAccessor={(n: GraphNode) => n.label}
            nodeColor={(n: GraphNode) => n.color ?? '#a855f7'}
            nodeSize={(n: GraphNode) => Math.max(2, Math.sqrt(n.mention_count ?? 1) * 2)}
            linkWidth={(link: GraphEdge) => Math.max(1, link.weight)}
            linkColor={(link: GraphEdge) => `rgba(148, 163, 184, ${Math.min(0.85, 0.2 + (link.weight / 8))})`}
            fitViewOnInit
            className="h-full w-full"
            onClick={(clickedNode: GraphNode | undefined) => {
              if (!clickedNode?.id) return;
              void onNodeClick(clickedNode.id);
            }}
          />
        ) : (
          <div className="text-surface-400 text-sm">No graph data loaded.</div>
        )}
        <p className="absolute right-3 bottom-2 text-xs text-surface-500">© Uncle Jethro</p>
      </div>
      {nodeId && (
        <div className="bg-surface-900 border border-surface-800 rounded-lg p-3">
          <h3 className="font-medium mb-2">Node details: {nodeId}</h3>
          {selectedNode && (
            <div className="mb-3 grid grid-cols-1 md:grid-cols-3 gap-2 text-xs text-surface-400">
              <div>Source (oldest mention): <span className="text-surface-200">{selectedNode.source ?? 'Unknown'}</span></div>
              <div>Mentions: <span className="text-surface-200">{selectedNode.mention_count ?? 0}</span></div>
              <div>Centrality (edges): <span className="text-surface-200">{selectedNode.centrality ?? 0}</span></div>
            </div>
          )}
          <ul className="space-y-2">
            {snippets.map((s) => (
              <li key={s.ref} className="text-sm text-surface-300 border-b border-surface-800 pb-2">
                <div className="text-xs text-surface-500">{s.event_time} · {s.source_display ?? 'Unknown source'} · {s.ref}</div>
                <div>{s.snippet}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
