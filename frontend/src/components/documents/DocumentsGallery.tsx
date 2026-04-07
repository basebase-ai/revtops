/**
 * Documents gallery – lists all artifacts (reports, charts, files) created by the agent.
 * Supports grid, list, and list+preview views with sortable columns.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { apiRequest } from "../../lib/api";
import { useAppStore, useUIStore } from "../../store";
import { ArtifactViewer } from "../ArtifactViewer";
import { VisibilityBadge } from "../VisibilitySelector";

interface ArtifactItem {
  id: string;
  type: string | null;
  title: string | null;
  description: string | null;
  content_type: string | null;
  mime_type: string | null;
  filename: string | null;
  conversation_id: string | null;
  message_id: string | null;
  created_at: string | null;
  user_id: string | null;
  creator_name: string | null;
  match_snippet: string | null;
  match_count: number;
  visibility?: string;
}

interface ArtifactsListResponse {
  artifacts: ArtifactItem[];
  total: number;
}

interface PreviewArtifact {
  id: string;
  title: string;
  filename: string;
  contentType: "text" | "markdown" | "pdf" | "chart";
  mimeType: string;
  content?: string;
}

type SortField = "title" | "creator_name" | "content_type" | "created_at";
type SortDir = "asc" | "desc";
type ViewMode = "grid" | "list";

const SEARCH_DEBOUNCE_MS = 300;

function contentTypeIcon(contentType: string | null): JSX.Element {
  const baseClass = "w-5 h-5 flex-shrink-0";
  switch (contentType) {
    case "chart":
      return (
        <svg className={baseClass} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
      );
    case "pdf":
      return (
        <svg className={baseClass} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
        </svg>
      );
    default:
      return (
        <svg className={baseClass} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      );
  }
}

function contentTypeLabel(contentType: string | null): string {
  switch (contentType) {
    case "chart": return "Chart";
    case "pdf": return "PDF";
    case "markdown": return "Markdown";
    case "text": return "Text";
    default: return contentType ?? "—";
  }
}

function SortHeader({ label, field, sortField, sortDir, onSort }: {
  label: string;
  field: SortField;
  sortField: SortField;
  sortDir: SortDir;
  onSort: (field: SortField) => void;
}): JSX.Element {
  const active = sortField === field;
  return (
    <button
      type="button"
      onClick={() => onSort(field)}
      className={`flex items-center gap-1 text-left text-xs font-medium uppercase tracking-wider ${
        active ? 'text-primary-400' : 'text-surface-500 hover:text-surface-300'
      } transition-colors`}
    >
      {label}
      {active && (
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d={sortDir === 'asc' ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7'} />
        </svg>
      )}
    </button>
  );
}

function MatchInfo({ doc }: { doc: ArtifactItem }): JSX.Element | null {
  if (!doc.match_count && !doc.match_snippet) return null;
  return (
    <>
      {doc.match_count > 0 && (
        <span className="text-primary-400 text-xs">{doc.match_count} match{doc.match_count !== 1 ? "es" : ""}</span>
      )}
      {doc.match_snippet && (
        <p className="text-xs text-surface-500 mt-1 line-clamp-2 italic">{doc.match_snippet}</p>
      )}
    </>
  );
}

export function DocumentsGallery(): JSX.Element {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState<string>("");
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [sortField, setSortField] = useState<SortField>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewArtifact, setPreviewArtifact] = useState<PreviewArtifact | null>(null);
  const [previewLoading, setPreviewLoading] = useState<boolean>(false);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const openArtifact = useUIStore((s) => s.openArtifact);
  const organization = useAppStore((s) => s.organization);
  const organizations = useAppStore((s) => s.organizations);

  const orgHandle: string | null =
    organization?.handle ??
    (organization?.id ? organizations.find((o) => o.id === organization.id)?.handle ?? null : null) ??
    null;
  const pathPrefix: string = orgHandle ? `/${orgHandle}` : "";

  const fetchArtifacts = useCallback(async (search: string): Promise<void> => {
    setLoading(true);
    setError(null);
    const qs: string = search.trim() ? `?search=${encodeURIComponent(search.trim())}` : "";
    const resp = await apiRequest<ArtifactsListResponse>(`/artifacts${qs}`);
    if (resp.error || !resp.data) {
      setError(resp.error ?? "Failed to load documents");
      setArtifacts([]);
    } else {
      setArtifacts(resp.data.artifacts);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      void fetchArtifacts(searchInput);
    }, SEARCH_DEBOUNCE_MS);
    return () => { if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current); };
  }, [searchInput, fetchArtifacts]);

  // Fetch preview content when previewId changes
  useEffect(() => {
    if (!previewId) { setPreviewArtifact(null); return; }
    let cancelled = false;
    setPreviewLoading(true);
    apiRequest<{
      id: string; title: string | null; filename: string | null;
      content_type: string | null; mime_type: string | null; content: string | null;
    }>(`/artifacts/${previewId}`).then(({ data }) => {
      if (cancelled || !data) return;
      setPreviewArtifact({
        id: data.id,
        title: data.title ?? "Untitled",
        filename: data.filename ?? "artifact.txt",
        contentType: (data.content_type as PreviewArtifact["contentType"]) ?? "text",
        mimeType: data.mime_type ?? "text/plain",
        content: data.content ?? undefined,
      });
      setPreviewLoading(false);
    });
    return () => { cancelled = true; };
  }, [previewId]);

  const handleOpen = (artifactId: string): void => {
    openArtifact(artifactId, searchInput.trim() || undefined);
    window.history.pushState({}, "", `${pathPrefix}/artifacts/${artifactId}`);
  };

  const handleSort = (field: SortField): void => {
    if (field === sortField) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir(field === "created_at" ? "desc" : "asc");
    }
  };

  const handleRowClick = (doc: ArtifactItem): void => {
    if (viewMode === "list") {
      setPreviewId((prev) => prev === doc.id ? null : doc.id);
    } else {
      handleOpen(doc.id);
    }
  };

  const sorted = useMemo(() => {
    const copy = [...artifacts];
    copy.sort((a, b) => {
      const av = (a[sortField] ?? "").toLowerCase();
      const bv = (b[sortField] ?? "").toLowerCase();
      if (sortField === "created_at") {
        const da = av ? new Date(av).getTime() : 0;
        const db = bv ? new Date(bv).getTime() : 0;
        return sortDir === "asc" ? da - db : db - da;
      }
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [artifacts, sortField, sortDir]);

  if (loading && artifacts.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-3xl mx-auto p-6">
        <div className="p-4 rounded-lg bg-red-900/20 border border-red-700 text-red-300 text-sm">{error}</div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 px-6 pt-6 pb-0">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold text-surface-100">Documents</h1>
            <p className="text-sm text-surface-400 mt-1">
              Reports, charts, and files created for you by Basebase
            </p>
          </div>
          <span className="text-sm text-surface-500">
            {artifacts.length} document{artifacts.length !== 1 ? "s" : ""}
          </span>
        </div>

        {/* Search + view toggle */}
        <div className="flex items-center gap-3 mb-4">
          <input
            type="search"
            placeholder="Search documents..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="flex-1 max-w-md px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500"
            aria-label="Search documents"
          />
          <div className="flex items-center border border-surface-700 rounded-lg overflow-hidden">
            <button
              type="button"
              onClick={() => { setViewMode("grid"); setPreviewId(null); }}
              className={`p-2 transition-colors ${viewMode === "grid" ? "bg-surface-700 text-surface-100" : "text-surface-500 hover:text-surface-300"}`}
              title="Grid view"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => setViewMode("list")}
              className={`p-2 transition-colors ${viewMode === "list" ? "bg-surface-700 text-surface-100" : "text-surface-500 hover:text-surface-300"}`}
              title="List view"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Content area */}
      {artifacts.length === 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <svg className="w-12 h-12 text-surface-600 mx-auto mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-surface-400 mb-2">No documents found</p>
            <p className="text-surface-500 text-sm">
              {searchInput.trim() ? "Try a different search term" : "Ask Basebase to create a report or analysis in chat"}
            </p>
          </div>
        </div>
      ) : viewMode === "grid" ? (
        /* ── Grid view ── */
        <div className="flex-1 overflow-y-auto px-6 pb-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {sorted.map((doc) => (
              <div
                key={doc.id}
                role="button"
                tabIndex={0}
                onClick={() => handleOpen(doc.id)}
                onKeyDown={(e) => { if (e.key === "Enter") handleOpen(doc.id); }}
                className="text-left p-4 rounded-lg bg-surface-800 border border-surface-700 hover:border-primary-500/50 hover:bg-surface-800/80 transition-all group cursor-pointer"
              >
                <div className="flex items-start gap-3">
                  <div className="w-9 h-9 rounded-lg bg-primary-500/15 flex items-center justify-center flex-shrink-0 mt-0.5 text-primary-400">
                    {contentTypeIcon(doc.content_type)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 min-w-0">
                      <h3 className="text-sm font-medium text-surface-100 group-hover:text-primary-300 transition-colors truncate">
                        {doc.title ?? doc.filename ?? "Untitled"}
                      </h3>
                      <VisibilityBadge visibility={doc.visibility ?? "team"} />
                    </div>
                    <div className="flex items-center gap-2 mt-1.5 text-xs text-surface-500">
                      {doc.creator_name && <span>{doc.creator_name}</span>}
                      {doc.created_at && (
                        <>
                          {doc.creator_name && <span className="text-surface-600">&middot;</span>}
                          <span>{new Date(doc.created_at).toLocaleDateString()}</span>
                        </>
                      )}
                    </div>
                    <MatchInfo doc={doc} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        /* ── List view with optional preview pane ── */
        <div className="flex-1 flex min-h-0">
          {/* File list */}
          <div className={`${previewId ? "w-2/5 border-r border-surface-700" : "flex-1"} flex flex-col min-h-0 overflow-hidden`}>
            {/* Column headers */}
            <div className={`grid ${previewId ? "grid-cols-[1fr_100px]" : "grid-cols-[1fr_140px_100px_120px]"} gap-4 px-4 py-2.5 bg-surface-800/50 border-b border-surface-700 flex-shrink-0`}>
              <SortHeader label="Name" field="title" sortField={sortField} sortDir={sortDir} onSort={handleSort} />
              {!previewId && <SortHeader label="Creator" field="creator_name" sortField={sortField} sortDir={sortDir} onSort={handleSort} />}
              {!previewId && <SortHeader label="Type" field="content_type" sortField={sortField} sortDir={sortDir} onSort={handleSort} />}
              <SortHeader label="Date" field="created_at" sortField={sortField} sortDir={sortDir} onSort={handleSort} />
            </div>
            {/* Rows */}
            <div className="flex-1 overflow-y-auto">
              {sorted.map((doc) => (
                <div
                  key={doc.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleRowClick(doc)}
                  onDoubleClick={() => handleOpen(doc.id)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleOpen(doc.id); }}
                  className={`grid ${previewId ? "grid-cols-[1fr_100px]" : "grid-cols-[1fr_140px_100px_120px]"} gap-4 px-4 py-3 border-b border-surface-800 cursor-pointer transition-colors group ${
                    previewId === doc.id ? "bg-primary-500/10 border-l-2 border-l-primary-500" : "hover:bg-surface-800/60"
                  }`}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-3">
                      <div className="text-primary-400 flex-shrink-0">
                        {contentTypeIcon(doc.content_type)}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-sm text-surface-100 group-hover:text-primary-300 truncate block transition-colors">
                            {doc.title ?? doc.filename ?? "Untitled"}
                          </span>
                          <VisibilityBadge visibility={doc.visibility ?? "team"} />
                        </div>
                        {doc.match_snippet && (
                          <p className="text-xs text-surface-500 truncate italic mt-0.5">{doc.match_snippet}</p>
                        )}
                      </div>
                    </div>
                    {doc.match_count > 0 && (
                      <span className="text-[10px] text-primary-400 ml-8 mt-0.5 block">{doc.match_count} match{doc.match_count !== 1 ? "es" : ""}</span>
                    )}
                  </div>
                  {!previewId && (
                    <div className="flex items-center">
                      <span className="text-sm text-surface-400 truncate">{doc.creator_name ?? "—"}</span>
                    </div>
                  )}
                  {!previewId && (
                    <div className="flex items-center">
                      <span className="text-xs text-surface-500">{contentTypeLabel(doc.content_type)}</span>
                    </div>
                  )}
                  <div className="flex items-center">
                    <span className="text-sm text-surface-500">
                      {doc.created_at ? new Date(doc.created_at).toLocaleDateString() : "—"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Preview pane */}
          {previewId && (
            <div className="w-3/5 flex flex-col min-h-0 overflow-hidden">
              {/* Preview header */}
              <div className="flex items-center justify-between px-4 py-2.5 bg-surface-800/50 border-b border-surface-700 flex-shrink-0">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm font-medium text-surface-100 truncate">
                    {previewArtifact?.title ?? "Loading..."}
                  </span>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    type="button"
                    onClick={() => handleOpen(previewId)}
                    className="px-2.5 py-1 text-xs font-medium bg-primary-600 hover:bg-primary-500 text-white rounded transition-colors"
                  >
                    Open
                  </button>
                  <button
                    type="button"
                    onClick={() => setPreviewId(null)}
                    className="p-1 text-surface-400 hover:text-surface-200 transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              </div>
              {/* Preview content */}
              <div className="flex-1 overflow-y-auto p-4">
                {previewLoading ? (
                  <div className="flex items-center justify-center h-32">
                    <div className="animate-spin w-6 h-6 border-2 border-surface-500 border-t-primary-500 rounded-full" />
                  </div>
                ) : previewArtifact ? (
                  <ArtifactViewer artifact={previewArtifact} />
                ) : null}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
