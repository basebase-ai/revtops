/**
 * API client for workstreams (semantic Home clusters).
 */

import { apiRequest } from "../lib/api";
import type { WorkstreamsResponse } from "../store/types";

const WORKSTREAMS_PATH = "/workstreams";

export async function fetchWorkstreams(
  windowHours: number = 24
): Promise<{ data: WorkstreamsResponse | null; error: string | null }> {
  const endpoint = `${WORKSTREAMS_PATH}?window=${windowHours}`;
  return apiRequest<WorkstreamsResponse>(endpoint);
}

export async function renameWorkstream(
  workstreamId: string,
  label: string
): Promise<{ data: { id: string; label: string } | null; error: string | null }> {
  return apiRequest<{ id: string; label: string }>(`${WORKSTREAMS_PATH}/${workstreamId}`, {
    method: "PATCH",
    body: JSON.stringify({ label: label.trim() }),
  });
}
