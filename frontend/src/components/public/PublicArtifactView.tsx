/**
 * Standalone public artifact page at /public/artifacts/:id (no auth).
 *
 * Global CSS locks html/body/#root to overflow:hidden + position:fixed
 * for the app shell. Rather than fighting those styles, this component
 * renders as a fixed full-screen overlay with its own scroll container.
 */

import { useState, useEffect, useCallback } from "react";
import { API_BASE } from "../../lib/api";
import { ArtifactViewer } from "../ArtifactViewer";
import { parsePossiblySpaWrappedJson } from "../../lib/documentPayload";

interface PublicArtifactApiResponse {
  id: string;
  type: string | null;
  title: string | null;
  content_type: string | null;
  mime_type: string | null;
  filename: string | null;
  content: string | null;
}

interface PublicArtifactViewProps {
  artifactId: string;
}

export function PublicArtifactView({ artifactId }: PublicArtifactViewProps): JSX.Element {
  const [data, setData] = useState<PublicArtifactApiResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const fetchArtifact = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/public/artifacts/${artifactId}`);
      if (!res.ok) {
        setError("Artifact not found or not public.");
        setData(null);
        return;
      }
      const rawBody = await res.text();
      const parsed = parsePossiblySpaWrappedJson(rawBody);
      if (!parsed || typeof parsed !== "object") {
        throw new Error("Invalid artifact payload");
      }
      setData(parsed as PublicArtifactApiResponse);
      setError(null);
    } catch {
      setError("Failed to load artifact");
      setData(null);
    }
    setLoading(false);
  }, [artifactId]);

  useEffect(() => {
    void fetchArtifact();
  }, [fetchArtifact]);

  if (loading) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-surface-950">
        <div className="animate-spin w-8 h-8 border-2 border-surface-500 border-t-primary-500 rounded-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-surface-950 text-red-300 p-6">
        {error ?? "Not found"}
      </div>
    );
  }

  const contentType: "text" | "markdown" | "pdf" | "chart" =
    (data.content_type as "text" | "markdown" | "pdf" | "chart") ?? "text";

  return (
    <div className="fixed inset-0 bg-surface-950 overflow-auto">
      <div className="p-4">
        <ArtifactViewer
          artifact={{
            id: data.id,
            title: data.title ?? "Untitled",
            filename: data.filename ?? "artifact.txt",
            contentType,
            mimeType: data.mime_type ?? "text/plain",
            content: data.content ?? undefined,
          }}
        />
      </div>
    </div>
  );
}
