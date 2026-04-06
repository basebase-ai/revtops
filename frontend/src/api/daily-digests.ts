/**
 * API client for daily team digests (per-member activity summaries).
 */

import { apiRequest } from "../lib/api";

const BASE_PATH = "/daily-digests";

export interface DigestSummaryJson {
  narrative: string;
  highlights: unknown[];
  categories: Record<string, unknown>;
}

export interface DigestMemberRow {
  user_id: string;
  name: string | null;
  avatar_url: string | null;
  digest_date: string;
  summary: DigestSummaryJson | null;
  generated_at: string | null;
  active_sources: string[];
}

export interface DailyDigestsResponse {
  digest_date: string;
  members: DigestMemberRow[];
  all_active_sources: string[];
}

export interface DigestDatesResponse {
  dates: string[];
}

export interface GenerateDigestResponse {
  status: string;
  digest_date: string;
  generated: number;
  errors: string[];
}

export async function fetchDailyDigests(
  date?: string | null
): Promise<{ data: DailyDigestsResponse | null; error: string | null }> {
  const q: string = date && date.length > 0 ? `?date=${encodeURIComponent(date)}` : "";
  return apiRequest<DailyDigestsResponse>(`${BASE_PATH}${q}`);
}

export async function fetchDigestDates(): Promise<{ data: DigestDatesResponse | null; error: string | null }> {
  return apiRequest<DigestDatesResponse>(`${BASE_PATH}/dates`);
}

export async function generateDailyDigests(
  date?: string | null
): Promise<{ data: GenerateDigestResponse | null; error: string | null }> {
  const body: Record<string, string | null> = {};
  if (date && date.length > 0) {
    body.date = date;
  }
  return apiRequest<GenerateDigestResponse>(BASE_PATH + "/generate", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
