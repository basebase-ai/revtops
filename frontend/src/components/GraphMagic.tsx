import { useEffect, useMemo, useState } from 'react';
import { Cosmograph } from '@cosmograph/react';
import { apiRequest } from '../lib/api';
import { useAuthStore, type UserOrganization } from '../store';

const MAX_RANGE_DAYS = 30;
const ROYGBIV = ['#e11d48', '#f97316', '#facc15', '#22c55e', '#3b82f6', '#6366f1', '#a855f7'];
const GRAPH_SIMULATION = {
  linkDistance: 3.5,
  linkSpring: 0.7,
} as const;
const REPULSION_LEVELS = {
  weak: 0.24,
  medium: 1.1,
  strong: 2.7,
} as const;

type GraphNode = {
  id: string;
  label: string;
  heat: number;
  mention_count?: number;
  source?: string;
  centrality?: number;
  source_diversity?: number;
  momentum?: number;
  color?: string;
};
type GraphEdge = { source: string; target: string; weight: number };
type NodeSizeMode = 'mentions' | 'centrality' | 'composite';
type RepulsionLevel = keyof typeof REPULSION_LEVELS;

type GraphNodeWithVisuals = GraphNode & {
  mention_count: number;
  centrality: number;
  heat: number;
  importance_score: number;
  color: string;
};

const hashToColor = (value: string): string => {
  let hash = 0;
  for (let idx = 0; idx < value.length; idx += 1) {
    hash = ((hash << 5) - hash) + value.charCodeAt(idx);
    hash |= 0;
  }
  const colorIndex = Math.abs(hash) % ROYGBIV.length;
  return ROYGBIV[colorIndex] ?? '#a855f7';
};

type GraphResponse = {
  organization_id: string;
  graph_date: string;
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  run_metadata: { coverage?: { partial?: boolean; warning_text?: string } };
};

type GraphSnapshotDatesResponse = {
  organization_id: string;
  dates: string[];
};

type AdminOrganization = {
  id: string;
  name: string;
};

const GRAPH_MAGIC_QUERY_KEYS = {
  orgId: 'gm_org',
  startDate: 'gm_start',
  endDate: 'gm_end',
  selectedDate: 'gm_selected',
  sizeMode: 'gm_size',
  repulsionLevel: 'gm_repulsion',
} as const;

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const getTodayIsoDate = (): string => new Date().toISOString().slice(0, 10);

const readGraphMagicStateFromUri = (): {
  orgId: string;
  startDate: string;
  endDate: string;
  selectedDate: string;
  sizeMode: NodeSizeMode;
  repulsionLevel: RepulsionLevel;
} => {
  if (typeof window === 'undefined') {
    const today = getTodayIsoDate();
    return {
      orgId: '',
      startDate: today,
      endDate: today,
      selectedDate: today,
      sizeMode: 'composite',
      repulsionLevel: 'weak',
    };
  }

  const params = new URLSearchParams(window.location.search);
  const today = getTodayIsoDate();
  const rawSizeMode = params.get(GRAPH_MAGIC_QUERY_KEYS.sizeMode);
  const rawRepulsionLevel = params.get(GRAPH_MAGIC_QUERY_KEYS.repulsionLevel);
  const validStartDate = params.get(GRAPH_MAGIC_QUERY_KEYS.startDate);
  const validEndDate = params.get(GRAPH_MAGIC_QUERY_KEYS.endDate);
  const validSelectedDate = params.get(GRAPH_MAGIC_QUERY_KEYS.selectedDate);

  return {
    orgId: params.get(GRAPH_MAGIC_QUERY_KEYS.orgId) ?? '',
    startDate: validStartDate && DATE_PATTERN.test(validStartDate) ? validStartDate : today,
    endDate: validEndDate && DATE_PATTERN.test(validEndDate) ? validEndDate : today,
    selectedDate: validSelectedDate && DATE_PATTERN.test(validSelectedDate) ? validSelectedDate : today,
    sizeMode: rawSizeMode === 'mentions' || rawSizeMode === 'centrality' || rawSizeMode === 'composite'
      ? rawSizeMode
      : 'composite',
    repulsionLevel: rawRepulsionLevel === 'weak' || rawRepulsionLevel === 'medium' || rawRepulsionLevel === 'strong'
      ? rawRepulsionLevel
      : 'weak',
  };
};

export function GraphMagic(): JSX.Element {
  const orgMemberships: UserOrganization[] = useAuthStore((state) => state.organizations);
  const uriState = useMemo(() => readGraphMagicStateFromUri(), []);
  const [orgId, setOrgId] = useState(uriState.orgId);
  const [startDate, setStartDate] = useState(uriState.startDate);
  const [endDate, setEndDate] = useState(uriState.endDate);
  const [selectedDate, setSelectedDate] = useState(uriState.selectedDate);
  const [availableSnapshotDates, setAvailableSnapshotDates] = useState<string[]>([]);
  const [isLoadingSnapshotDates, setIsLoadingSnapshotDates] = useState(false);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nodeId, setNodeId] = useState<string | null>(null);
  const [sizeMode, setSizeMode] = useState<NodeSizeMode>(uriState.sizeMode);
  const [repulsionLevel, setRepulsionLevel] = useState<RepulsionLevel>(uriState.repulsionLevel);
  const [snippets, setSnippets] = useState<Array<{ ref: string; snippet: string; event_time: string; source_display?: string }>>([]);
  const [availableOrgs, setAvailableOrgs] = useState<AdminOrganization[]>([]);

  const partialWarning = graph?.run_metadata?.coverage?.partial ? 'Partial data: some sources failed' : null;

  useEffect(() => {
    const fetchOrganizations = async (): Promise<void> => {
      const { data, error: requestError } = await apiRequest<{ organizations: AdminOrganization[] }>(
        '/waitlist/admin/organizations?limit=1000',
      );
      if (requestError || !data?.organizations?.length) {
        console.debug('[Graph Magic] Falling back to org memberships for org dropdown', {
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
    if (!orgId || !selectedDate) return;
    console.debug('[Graph Magic] Fetching graph snapshot', { orgId, selectedDate });
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

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const params = new URLSearchParams(window.location.search);
    const nextValues: Record<string, string> = {
      [GRAPH_MAGIC_QUERY_KEYS.orgId]: orgId,
      [GRAPH_MAGIC_QUERY_KEYS.startDate]: startDate,
      [GRAPH_MAGIC_QUERY_KEYS.endDate]: endDate,
      [GRAPH_MAGIC_QUERY_KEYS.selectedDate]: selectedDate,
      [GRAPH_MAGIC_QUERY_KEYS.sizeMode]: sizeMode,
      [GRAPH_MAGIC_QUERY_KEYS.repulsionLevel]: repulsionLevel,
    };

    Object.entries(nextValues).forEach(([key, value]) => {
      if (value) {
        params.set(key, value);
        return;
      }
      params.delete(key);
    });

    const nextQueryString = params.toString();
    const nextUri = `${window.location.pathname}${nextQueryString ? `?${nextQueryString}` : ''}${window.location.hash}`;
    const currentUri = `${window.location.pathname}${window.location.search}${window.location.hash}`;

    if (nextUri !== currentUri) {
      window.history.replaceState(window.history.state, '', nextUri);
      console.debug('[Graph Magic] Updated URI selection state', {
        orgId,
        startDate,
        endDate,
        selectedDate,
        sizeMode,
        repulsionLevel,
      });
    }
  }, [orgId, startDate, endDate, selectedDate, sizeMode, repulsionLevel]);

  useEffect(() => {
    const fetchSnapshotDates = async (): Promise<void> => {
      if (!orgId) {
        setAvailableSnapshotDates([]);
        setIsLoadingSnapshotDates(false);
        return;
      }
      setIsLoadingSnapshotDates(true);
      console.debug('[Graph Magic] Fetching available snapshot dates', { orgId });
      const { data, error: reqErr } = await apiRequest<GraphSnapshotDatesResponse>(`/admin-topic-graph/${orgId}/dates`);
      if (reqErr || !data) {
        console.debug('[Graph Magic] Failed to fetch snapshot dates', { orgId, reqErr });
        setAvailableSnapshotDates([]);
        setError(reqErr ?? 'Failed to load available snapshot dates');
        setIsLoadingSnapshotDates(false);
        return;
      }
      const dates = data.dates ?? [];
      setAvailableSnapshotDates(dates);
      if (dates.length === 0) {
        setSelectedDate('');
        setGraph(null);
        setSnippets([]);
        setNodeId(null);
        setError('No graph snapshots available for this organization.');
        setIsLoadingSnapshotDates(false);
        return;
      }
      setError(null);
      setSelectedDate((currentSelectedDate) => {
        if (dates.includes(currentSelectedDate)) return currentSelectedDate;
        return dates[0] ?? currentSelectedDate;
      });
      setIsLoadingSnapshotDates(false);
    };

    void fetchSnapshotDates();
  }, [orgId]);

  const rebuild = async (): Promise<void> => {
    if (!canRebuild) return;
    console.debug('[Graph Magic] Rebuilding graphs for range', { orgId, startDate, endDate });
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
    const mentionCounts = graph.graph.nodes.map((node) => Math.max(1, Math.round(node.mention_count ?? 1)));
    const centralities = graph.graph.nodes.map((node) => Math.max(0, node.centrality ?? 0));
    const heats = graph.graph.nodes.map((node) => Math.max(0, node.heat ?? 0));

    const minMentions = Math.min(...mentionCounts);
    const maxMentions = Math.max(...mentionCounts);
    const minCentrality = Math.min(...centralities);
    const maxCentrality = Math.max(...centralities);
    const minHeat = Math.min(...heats);
    const maxHeat = Math.max(...heats);

    const normalize = (value: number, min: number, max: number): number => {
      const range = max - min;
      if (range <= 0) return 1;
      return (value - min) / range;
    };

    const nodes: GraphNodeWithVisuals[] = graph.graph.nodes.map((node) => {
      const mentionCount = Math.max(1, Math.round(node.mention_count ?? 1));
      const centrality = Math.max(0, node.centrality ?? 0);
      const heat = Math.max(0, node.heat ?? 0);
      const mentionNorm = normalize(mentionCount, minMentions, maxMentions);
      const centralityNorm = normalize(centrality, minCentrality, maxCentrality);
      const heatNorm = normalize(heat, minHeat, maxHeat);
      const importanceScore = (mentionNorm * 0.5) + (centralityNorm * 0.35) + (heatNorm * 0.15);
      return {
        ...node,
        mention_count: mentionCount,
        centrality,
        heat,
        importance_score: importanceScore,
        color: hashToColor(node.id),
      };
    });

    console.debug('[Graph Magic] Computed node visuals and importance scores', {
      nodeCount: nodes.length,
      sizeMode,
      minMentions,
      maxMentions,
      minCentrality,
      maxCentrality,
      minHeat,
      maxHeat,
      simulation: GRAPH_SIMULATION,
    });

    return { ...graph.graph, nodes, edges: graph.graph.edges };
  }, [graph, sizeMode]);

  const selectedNode = useMemo(() => graphWithVisuals?.nodes.find((n) => n.id === nodeId) ?? null, [graphWithVisuals, nodeId]);

  const getNodeSize = (node: GraphNodeWithVisuals): number => {
    if (sizeMode === 'mentions') {
      return Math.max(2.5, Math.sqrt(node.mention_count) * 2);
    }
    if (sizeMode === 'centrality') {
      return Math.max(2.5, 2 + (Math.sqrt(Math.max(0, node.centrality)) * 2));
    }
    return Math.max(2.5, 2 + (node.importance_score * 12));
  };

  const onNodeClick = async (id: string): Promise<void> => {
    setNodeId(id);
    const { data } = await apiRequest<{ snippets: Array<{ ref: string; snippet: string; event_time: string; source_display?: string }> }>(
      `/admin-topic-graph/${orgId}/${selectedDate}/nodes/${encodeURIComponent(id)}/evidence`
    );
    setSnippets(data?.snippets ?? []);
  };

  const closeNodeDetails = (): void => {
    setNodeId(null);
    setSnippets([]);
  };

  return (
    <div className="h-full min-h-0 flex flex-col gap-4">
      <div className="grid grid-cols-1 md:grid-cols-8 gap-3 items-end">
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
          <select
            className="px-3 py-2 rounded bg-surface-800 text-surface-100"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            disabled={isLoadingSnapshotDates || availableSnapshotDates.length === 0}
          >
            {isLoadingSnapshotDates ? (
              <option value="">Loading snapshots...</option>
            ) : availableSnapshotDates.length === 0 ? (
              <option value="">No snapshots available</option>
            ) : (
              availableSnapshotDates.map((snapshotDate) => (
                <option key={snapshotDate} value={snapshotDate}>
                  {snapshotDate}
                </option>
              ))
            )}
          </select>
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
            Generate
          </button>
        </div>
        <label className="flex flex-col gap-1 text-xs text-surface-400">
          <span>Node size mode</span>
          <select
            className="w-full px-3 py-2 rounded bg-surface-800 text-surface-100"
            value={sizeMode}
            onChange={(e) => setSizeMode(e.target.value as NodeSizeMode)}
          >
            <option value="composite">Composite importance</option>
            <option value="mentions">Mentions</option>
            <option value="centrality">Centrality</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-surface-400">
          <span>Node repulsion</span>
          <select
            className="w-full px-3 py-2 rounded bg-surface-800 text-surface-100"
            value={repulsionLevel}
            onChange={(e) => setRepulsionLevel(e.target.value as RepulsionLevel)}
          >
            <option value="weak">Weak</option>
            <option value="medium">Medium</option>
            <option value="strong">Strong</option>
          </select>
        </label>
      </div>
      {partialWarning && <p className="text-xs text-amber-400">Partial data: some sources failed</p>}
      {error && <p className="text-sm text-red-400">{error}</p>}
      <div className="bg-surface-900 border border-surface-800 rounded-lg p-3 flex-1 min-h-[68vh] relative">
        {graphWithVisuals ? (
          <Cosmograph
            key={`graph-${orgId}-${selectedDate}-${repulsionLevel}`}
            nodes={graphWithVisuals.nodes}
            links={graphWithVisuals.edges}
            nodeLabelAccessor={(n: GraphNode) => n.label}
            nodeColor={(n: GraphNode) => n.color ?? '#a855f7'}
            nodeSize={(n: GraphNode) => getNodeSize(n as GraphNodeWithVisuals)}
            linkWidth={(link: GraphEdge) => Math.max(1, link.weight)}
            linkColor={(link: GraphEdge) => `rgba(148, 163, 184, ${Math.min(0.85, 0.2 + (link.weight / 8))})`}
            simulationRepulsion={REPULSION_LEVELS[repulsionLevel]}
            simulationLinkDistance={GRAPH_SIMULATION.linkDistance}
            simulationLinkSpring={GRAPH_SIMULATION.linkSpring}
            fitViewOnInit
            className="h-full w-full"
            onClick={(clickedNode: GraphNode | undefined) => {
              if (!clickedNode?.id) {
                closeNodeDetails();
                return;
              }
              void onNodeClick(clickedNode.id);
            }}
          />
        ) : (
          <div className="text-surface-400 text-sm">No graph data loaded.</div>
        )}
        {nodeId && (
          <>
            <button
              type="button"
              className="absolute inset-0 bg-black/50"
              aria-label="Close node details"
              onClick={closeNodeDetails}
            />
            <div className="absolute left-3 top-3 w-[calc(100%-1.5rem)] md:w-[min(738px,calc((100%-1.5rem)*0.9))] z-10 bg-surface-900 border border-surface-700 rounded-lg p-3 shadow-2xl max-h-[70vh] overflow-y-auto">
              <div className="flex items-start justify-between gap-3 mb-2">
                <h3 className="font-medium">Node details: {nodeId}</h3>
                <button
                  type="button"
                  className="px-2 py-1 rounded bg-surface-800 text-surface-300 hover:bg-surface-700 text-xs"
                  onClick={closeNodeDetails}
                >
                  Close
                </button>
              </div>
              {selectedNode && (
                <div className="mb-3 grid grid-cols-1 md:grid-cols-3 gap-2 text-xs text-surface-400">
                  <div>Source (oldest mention): <span className="text-surface-200">{selectedNode.source ?? 'Unknown'}</span></div>
                  <div>Mentions: <span className="text-surface-200">{selectedNode.mention_count ?? 0}</span></div>
                  <div>Centrality (edges): <span className="text-surface-200">{selectedNode.centrality ?? 0}</span></div>
                  <div>Heat: <span className="text-surface-200">{selectedNode.heat ?? 0}</span></div>
                  <div>Source diversity: <span className="text-surface-200">{selectedNode.source_diversity ?? 0}</span></div>
                  <div>Momentum (vs prior 7d): <span className="text-surface-200">{(selectedNode.momentum ?? 0).toFixed(2)}x</span></div>
                  <div>Importance score: <span className="text-surface-200">{(selectedNode.importance_score ?? 0).toFixed(3)}</span></div>
                  <div>Breakdown: <span className="text-surface-200">mentions 50% · centrality 35% · heat 15%</span></div>
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
          </>
        )}
        <p className="absolute right-3 bottom-2 text-xs text-surface-500">© Uncle Jethro</p>
      </div>
    </div>
  );
}
