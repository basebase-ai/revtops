/**
 * Documents gallery – lists all artifacts (reports, charts, files) created by the agent.
 * Accessible via the "Documents" nav item. Search at top, default sort recent first.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { apiRequest } from "../../lib/api";
import { useAppStore, useUIStore } from "../../store";

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
}

interface ArtifactsListResponse {
  artifacts: ArtifactItem[];
  total: number;
}

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
    case "markdown":
      return (
        <svg className={baseClass} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
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

export function DocumentsGallery(): JSX.Element {
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState<string>("");
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
    if (searchDebounceRef.current) {
      clearTimeout(searchDebounceRef.current);
    }
    searchDebounceRef.current = setTimeout(() => {
      void fetchArtifacts(searchInput);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (searchDebounceRef.current) {
        clearTimeout(searchDebounceRef.current);
      }
    };
  }, [searchInput, fetchArtifacts]);

  const handleOpen = (artifactId: string): void => {
    openArtifact(artifactId);
    window.history.pushState({}, "", `${pathPrefix}/artifacts/${artifactId}`);
  };

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
          <h1 className="text-xl font-bold text-surface-100">Documents</h1>
          <p className="text-sm text-surface-400 mt-1">
            Reports, charts, and files created for you by Basebase
          </p>
        </div>
        <span className="text-sm text-surface-500">
          {artifacts.length} document{artifacts.length !== 1 ? "s" : ""}
        </span>
      </div>

      <div className="mb-4">
        <input
          type="search"
          placeholder="Search documents..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          className="w-full max-w-md px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500"
          aria-label="Search documents"
        />
      </div>

      {artifacts.length === 0 ? (
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
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
          <p className="text-surface-400 mb-2">No documents yet</p>
          <p className="text-surface-500 text-sm">
            Ask Basebase to create a report or analysis in chat
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {artifacts.map((doc) => (
            <div
              key={doc.id}
              role="button"
              tabIndex={0}
              onClick={() => handleOpen(doc.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleOpen(doc.id);
              }}
              className="text-left p-4 rounded-lg bg-surface-800 border border-surface-700 hover:border-primary-500/50 hover:bg-surface-800/80 transition-all group cursor-pointer"
            >
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded-lg bg-primary-500/15 flex items-center justify-center flex-shrink-0 mt-0.5 text-primary-400">
                  {contentTypeIcon(doc.content_type)}
                </div>
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-medium text-surface-100 group-hover:text-primary-300 transition-colors truncate max-w-[35ch]">
                    {doc.title ?? doc.filename ?? "Untitled"}
                  </h3>
                  {doc.description && (
                    <p className="text-xs text-surface-400 mt-1 line-clamp-2">
                      {doc.description}
                    </p>
                  )}
                  <div className="flex items-center gap-2 mt-2 text-xs text-surface-500">
                    {doc.creator_name && <span>{doc.creator_name}</span>}
                    {doc.created_at && (
                      <>
                        {doc.creator_name && <span className="text-surface-600">&middot;</span>}
                        <span>{new Date(doc.created_at).toLocaleDateString()}</span>
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
  );
}
