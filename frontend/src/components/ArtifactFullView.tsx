/**
 * Full-screen artifact view at /artifacts/:id.
 *
 * Fetches the artifact by ID and displays it with ArtifactViewer.
 * Shows a header with title, back button, copy link, and search
 * highlight navigation (when opened from a search).
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { apiRequest } from "../lib/api";
import { useAppStore, useUIStore } from "../store";
import { ArtifactViewer } from "./ArtifactViewer";

interface ArtifactApiResponse {
  id: string;
  type: string | null;
  title: string | null;
  description: string | null;
  content_type: string | null;
  mime_type: string | null;
  filename: string | null;
  content: string | null;
  conversation_id: string | null;
  message_id: string | null;
  created_at: string | null;
  user_id: string | null;
}

// Map API snake_case to ArtifactViewer camelCase
function toFileArtifact(api: ArtifactApiResponse): {
  id: string;
  title: string;
  filename: string;
  contentType: "text" | "markdown" | "pdf" | "chart";
  mimeType: string;
  content?: string;
} {
  const contentType: "text" | "markdown" | "pdf" | "chart" =
    (api.content_type as "text" | "markdown" | "pdf" | "chart") ?? "text";
  return {
    id: api.id,
    title: api.title ?? "Untitled",
    filename: api.filename ?? "artifact.txt",
    contentType,
    mimeType: api.mime_type ?? "text/plain",
    content: api.content ?? undefined,
  };
}

interface ArtifactFullViewProps {
  artifactId: string;
}

/**
 * Walk all text nodes inside a container and wrap matches of `term`
 * in <mark data-search-highlight> elements. Returns the total count.
 */
function highlightTextNodes(container: HTMLElement, term: string): number {
  const termLower = term.toLowerCase();
  let count = 0;

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text);

  for (const node of textNodes) {
    const text = node.textContent ?? "";
    const lower = text.toLowerCase();
    const idx = lower.indexOf(termLower);
    if (idx === -1) continue;

    // Split the text node around the match
    const before = text.slice(0, idx);
    const match = text.slice(idx, idx + term.length);
    const after = text.slice(idx + term.length);

    const mark = document.createElement("mark");
    mark.setAttribute("data-search-highlight", "");
    mark.className = "bg-yellow-400/30 text-inherit rounded-sm px-0.5";
    mark.textContent = match;

    const parent = node.parentNode;
    if (!parent) continue;

    if (before) parent.insertBefore(document.createTextNode(before), node);
    parent.insertBefore(mark, node);
    if (after) parent.insertBefore(document.createTextNode(after), node);
    parent.removeChild(node);
    count++;
  }
  return count;
}

function clearHighlights(container: HTMLElement): void {
  container.querySelectorAll("mark[data-search-highlight]").forEach((el) => {
    const parent = el.parentNode;
    if (parent) {
      parent.replaceChild(document.createTextNode(el.textContent ?? ""), el);
      parent.normalize();
    }
  });
}

export function ArtifactFullView({
  artifactId,
}: ArtifactFullViewProps): JSX.Element {
  const [artifact, setArtifact] = useState<ReturnType<
    typeof toFileArtifact
  > | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState<boolean>(false);

  // Search highlighting
  const documentSearchTerm = useUIStore((s) => s.documentSearchTerm);
  const [matchTotal, setMatchTotal] = useState<number>(0);
  const [matchIndex, setMatchIndex] = useState<number>(0);
  const contentRef = useRef<HTMLDivElement>(null);

  const setCurrentView = useAppStore((s) => s.setCurrentView);

  const fetchArtifact = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    const resp = await apiRequest<ArtifactApiResponse>(
      `/artifacts/${artifactId}`,
    );
    if (resp.error || !resp.data) {
      setError(resp.error ?? "Failed to load artifact");
      setArtifact(null);
    } else {
      setArtifact(toFileArtifact(resp.data));
    }
    setLoading(false);
  }, [artifactId]);

  useEffect(() => {
    void fetchArtifact();
  }, [fetchArtifact]);

  // Refetch when this artifact is updated by the agent (real-time update)
  const lastArtifactUpdateId = useUIStore((s) => s.lastArtifactUpdateId);
  useEffect(() => {
    if (lastArtifactUpdateId === artifactId) {
      useUIStore.getState().consumeArtifactUpdate();
      void fetchArtifact();
    }
  }, [lastArtifactUpdateId, artifactId, fetchArtifact]);

  // Apply search highlights after content renders
  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    clearHighlights(container);
    if (!documentSearchTerm?.trim()) {
      setMatchTotal(0);
      setMatchIndex(0);
      return;
    }
    // Wait a tick for ReactMarkdown to render
    const raf = requestAnimationFrame(() => {
      const total = highlightTextNodes(container, documentSearchTerm.trim());
      setMatchTotal(total);
      setMatchIndex(0);
      // Scroll to first match
      if (total > 0) {
        const first = container.querySelector("mark[data-search-highlight]");
        first?.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [documentSearchTerm, artifact]);

  const scrollToMatch = useCallback((idx: number) => {
    const container = contentRef.current;
    if (!container) return;
    const marks = container.querySelectorAll("mark[data-search-highlight]");
    if (marks.length === 0) return;
    const clamped = Math.max(0, Math.min(idx, marks.length - 1));
    setMatchIndex(clamped);
    // Highlight the current match more brightly
    marks.forEach((m, i) => {
      (m as HTMLElement).className = i === clamped
        ? "bg-yellow-400/60 text-inherit rounded-sm px-0.5 ring-2 ring-yellow-400/50"
        : "bg-yellow-400/30 text-inherit rounded-sm px-0.5";
    });
    marks[clamped]?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, []);

  const organization = useAppStore((s) => s.organization);
  const organizations = useAppStore((s) => s.organizations);
  const orgHandle: string | null =
    organization?.handle ??
    (organization?.id ? organizations.find((o) => o.id === organization.id)?.handle ?? null : null) ??
    null;
  const prefix: string = orgHandle ? `/${orgHandle}` : "";

  const handleCopyLink = async (): Promise<void> => {
    const url: string = `${window.location.origin}${prefix}/artifacts/${artifactId}`;
    await navigator.clipboard.writeText(url);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  };

  const goBack = (): void => {
    setCurrentView("documents");
    window.history.pushState({}, "", `${prefix}/documents`);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" />
      </div>
    );
  }

  if (error || !artifact) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="p-4 rounded-lg bg-red-900/20 border border-red-700 text-red-300 text-sm max-w-md text-center">
          {error ?? "Artifact not found"}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700 bg-surface-900 flex-shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={goBack}
            className="text-surface-400 hover:text-surface-200 transition-colors"
            title="Back to Documents"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div>
            <h1 className="text-base font-semibold text-surface-100">
              {artifact.title}
            </h1>
            <p className="text-xs text-surface-400 mt-0.5 truncate max-w-md">
              {artifact.filename}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Search match navigator */}
          {documentSearchTerm && matchTotal > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-surface-300">
              <span>{matchIndex + 1} of {matchTotal}</span>
              <button
                onClick={() => scrollToMatch(matchIndex - 1)}
                disabled={matchIndex <= 0}
                className="p-1 rounded hover:bg-surface-700 disabled:opacity-30 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                </svg>
              </button>
              <button
                onClick={() => scrollToMatch(matchIndex + 1)}
                disabled={matchIndex >= matchTotal - 1}
                className="p-1 rounded hover:bg-surface-700 disabled:opacity-30 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
            </div>
          )}
          {documentSearchTerm && matchTotal === 0 && (
            <span className="text-xs text-surface-500">No matches</span>
          )}

          <button
            onClick={() => void handleCopyLink()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-surface-700 hover:bg-surface-600 text-surface-300 text-xs font-medium transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            {linkCopied ? "Copied!" : "Copy link"}
          </button>
        </div>
      </div>

      <div ref={contentRef} className="flex-1 overflow-auto p-4">
        <ArtifactViewer artifact={artifact} />
      </div>
    </div>
  );
}
