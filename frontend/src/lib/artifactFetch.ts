import { API_BASE, DIRECT_API_BASE, getAuthenticatedRequestHeaders } from "./api";

type ArtifactLikePayload = Record<string, unknown> & {
  id?: unknown;
  content?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseArtifactPayload(rawBody: string, sourceLabel: string): ArtifactLikePayload {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody) as unknown;
  } catch (error) {
    throw new Error(`[artifactFetch] ${sourceLabel} returned non-JSON payload`);
  }

  if (!isRecord(parsed)) {
    throw new Error(`[artifactFetch] ${sourceLabel} returned non-object JSON payload`);
  }

  return parsed as ArtifactLikePayload;
}

function looksLikeUnfurlUrl(value: string): boolean {
  return /^https?:\/\//i.test(value) && /\/basebase\/(documents|artifacts)\//i.test(value);
}

async function fetchArtifactFromBase(
  artifactId: string,
  baseApiUrl: string,
  requestHeaders: Record<string, string>,
  sourceLabel: string,
): Promise<Record<string, unknown>> {
  const query = sourceLabel === "direct-backend" ? `?direct_fetch_ts=${Date.now()}` : "";
  const response = await fetch(`${baseApiUrl}/artifacts/${artifactId}${query}`, {
    method: "GET",
    headers: {
      ...requestHeaders,
      Accept: "application/json",
      "Cache-Control": "no-cache",
      Pragma: "no-cache",
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`[artifactFetch] ${sourceLabel} returned HTTP ${response.status}`);
  }

  const rawBody = await response.text();
  const payload = parseArtifactPayload(rawBody, sourceLabel);
  const contentValue = payload.content;
  if (typeof contentValue === "string" && looksLikeUnfurlUrl(contentValue)) {
    throw new Error(`[artifactFetch] ${sourceLabel} returned unfurl URL in content`);
  }

  console.info("[artifactFetch] Loaded artifact payload from %s for artifact_id=%s", sourceLabel, artifactId);
  return payload;
}

export async function fetchArtifactByIdWithFallback(artifactId: string): Promise<Record<string, unknown>> {
  const authHeaders = await getAuthenticatedRequestHeaders();

  try {
    return await fetchArtifactFromBase(artifactId, API_BASE, authHeaders, "primary-api");
  } catch (primaryError) {
    console.warn(
      "[artifactFetch] Primary artifact fetch failed for artifact_id=%s. Falling back to direct backend fetch. reason=%s",
      artifactId,
      primaryError instanceof Error ? primaryError.message : String(primaryError),
    );
  }

  return fetchArtifactFromBase(artifactId, DIRECT_API_BASE, authHeaders, "direct-backend");
}
