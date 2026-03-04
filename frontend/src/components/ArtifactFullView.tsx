/**
 * Full-screen artifact view at /artifacts/:id.
 *
 * Fetches the artifact by ID and displays it with ArtifactViewer.
 * Shows a header with title, back button, and copy link.
 */

import { useState, useEffect, useCallback } from "react";
import { apiRequest } from "../lib/api";
import { useAppStore } from "../store";
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

export function ArtifactFullView({
  artifactId,
}: ArtifactFullViewProps): JSX.Element {
  const [artifact, setArtifact] = useState<ReturnType<
    typeof toFileArtifact
  > | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState<boolean>(false);

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
    setCurrentView("chat");
    window.history.pushState({}, "", `${prefix}/chat`);
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
            title="Back to Chat"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 19l-7-7 7-7"
              />
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

        <button
          onClick={() => void handleCopyLink()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-surface-700 hover:bg-surface-600 text-surface-300 text-xs font-medium transition-colors"
        >
          <svg
            className="w-3.5 h-3.5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
            />
          </svg>
          {linkCopied ? "Copied!" : "Copy link"}
        </button>
      </div>

      <div className="flex-1 overflow-auto p-4">
        <ArtifactViewer artifact={artifact} />
      </div>
    </div>
  );
}
