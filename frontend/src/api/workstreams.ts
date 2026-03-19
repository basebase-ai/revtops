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
