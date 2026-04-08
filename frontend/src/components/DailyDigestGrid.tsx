/**
 * Team daily digest cards — one card per org member for a selected calendar date.
 */

import { useCallback, useEffect, useState } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  fetchDailyDigests,
  generateDailyDigests,
  type DailyDigestsResponse,
  type DigestMemberRow,
  type DigestSummaryJson,
} from "../api/daily-digests";
import {
  CONNECTOR_DISPLAY,
  DEFAULT_CONNECTOR_COLOR,
  DEFAULT_CONNECTOR_ICON,
  getConnectorColorClass,
  isImageIcon,
  renderConnectorIcon,
} from "./shared/ConnectorIcons";

function addCalendarDays(isoDate: string, deltaDays: number): string {
  const parts: string[] = isoDate.split("-");
  const y: number = Number(parts[0]);
  const m: number = Number(parts[1]);
  const d: number = Number(parts[2]);
  const dt: Date = new Date(Date.UTC(y, m - 1, d + deltaDays));
  return dt.toISOString().slice(0, 10);
}

function formatDisplayDate(isoDate: string): string {
  const [yy, mm, dd] = isoDate.split("-");
  if (!yy || !mm || !dd) return isoDate;
  return `${mm}/${dd}/${yy}`;
}

function SourceBadges({ sources }: { sources: string[] }): JSX.Element | null {
  if (sources.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-surface-500 mr-0.5">Sources:</span>
      {sources.map((s) => {
        const display = CONNECTOR_DISPLAY[s];
        const label: string = display?.label ?? s;
        const iconId: string = display?.icon ?? DEFAULT_CONNECTOR_ICON;
        const color: string = display?.color ?? DEFAULT_CONNECTOR_COLOR;
        const bgClass: string = isImageIcon(iconId) ? "" : getConnectorColorClass(color);
        return (
          <span
            key={s}
            title={label}
            className="inline-flex items-center gap-1.5 pl-1 pr-2 py-0.5 rounded-md bg-surface-800 text-surface-300 text-xs"
          >
            <span className={`${bgClass} rounded p-0.5 text-white flex items-center justify-center`}>
              {renderConnectorIcon(iconId, "w-3.5 h-3.5")}
            </span>
            <span>{label}</span>
          </span>
        );
      })}
    </div>
  );
}

function categoryLabel(key: string): string {
  const labels: Record<string, string> = {
    code: "Code",
    issues: "Issues",
    meetings: "Meetings",
    slack: "Slack",
    calendar: "Calendar",
    crm: "CRM",
    documents: "Documents",
  };
  return labels[key] ?? key;
}

function renderSummary(summary: DigestSummaryJson | null): JSX.Element {
  if (!summary) {
    return (
      <p className="text-surface-500 text-sm">
        No digest for this day yet. Digests are generated daily (after midnight PT) or use Generate below.
      </p>
    );
  }
  const narrative: string = summary.narrative?.trim() ?? "";
  const highlights: unknown[] = Array.isArray(summary.highlights) ? summary.highlights : [];
  const categories: Record<string, unknown> =
    summary.categories && typeof summary.categories === "object" && summary.categories !== null
      ? (summary.categories as Record<string, unknown>)
      : {};

  return (
    <div className="space-y-3 text-sm">
      {narrative ? <p className="text-surface-200 leading-relaxed">{narrative}</p> : null}
      {highlights.length > 0 ? (
        <ul className="list-disc list-inside text-surface-300 space-y-1">
          {highlights.map((h, i) => (
            <li key={i}>{String(h)}</li>
          ))}
        </ul>
      ) : null}
      {Object.entries(categories).map(([key, val]) => {
        if (!Array.isArray(val) || val.length === 0) return null;
        return (
          <div key={key}>
            <div className="text-xs font-medium text-surface-500 uppercase tracking-wide mb-1">
              {categoryLabel(key)}
            </div>
            <ul className="list-disc list-inside text-surface-400 space-y-0.5">
              {val.map((item, i) => (
                <li key={i}>{String(item)}</li>
              ))}
            </ul>
          </div>
        );
      })}
      {!narrative && highlights.length === 0 && Object.keys(categories).length === 0 ? (
        <p className="text-surface-500">No activity data available for this day.</p>
      ) : null}
    </div>
  );
}

const TEAM_SUMMARY_MD_COMPONENTS: Components = {
  h1({ children }) {
    return (
      <h3 className="text-sm font-semibold text-surface-100 mt-3 first:mt-0 mb-1">
        {children}
      </h3>
    );
  },
  h2({ children }) {
    return (
      <h4 className="text-sm font-medium text-surface-200 mt-2 first:mt-0 mb-1">
        {children}
      </h4>
    );
  },
  p({ children }) {
    return (
      <p className="text-surface-300 text-sm leading-relaxed mb-1.5 last:mb-0">
        {children}
      </p>
    );
  },
};

function TeamSummaryCard({ summary }: { summary: string }): JSX.Element {
  return (
    <article className="rounded-xl border border-primary-500/30 bg-surface-900/60 p-4 md:p-5">
      <div className="text-xs font-medium text-primary-400 uppercase tracking-wide mb-2">
        Team Summary
      </div>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={TEAM_SUMMARY_MD_COMPONENTS}>
        {summary}
      </ReactMarkdown>
    </article>
  );
}

function MemberCard({ member }: { member: DigestMemberRow }): JSX.Element {
  const displayName: string = member.name?.trim() || member.user_id.slice(0, 8);
  return (
    <article className="rounded-xl border border-surface-700 bg-surface-900/50 p-4 md:p-5 flex flex-col gap-3 max-h-[28rem] overflow-y-auto">
      <div className="flex items-center gap-3">
        {member.avatar_url ? (
          <img
            src={member.avatar_url}
            alt=""
            className="w-10 h-10 rounded-full object-cover border border-surface-600"
          />
        ) : (
          <div className="w-10 h-10 rounded-full bg-surface-700 flex items-center justify-center text-surface-300 text-sm font-medium">
            {displayName.slice(0, 1).toUpperCase()}
          </div>
        )}
        <div>
          <h3 className="text-surface-100 font-medium">{displayName}</h3>
          {member.generated_at ? (
            <p className="text-xs text-surface-500">
              Updated {new Date(member.generated_at).toLocaleString()}
            </p>
          ) : null}
        </div>
      </div>
      {renderSummary(member.summary)}
    </article>
  );
}

export interface DailyDigestGridProps {
  /** YYYY-MM-DD */
  digestDate: string;
  onDigestDateChange: (next: string) => void;
}

export function DailyDigestGrid({ digestDate, onDigestDateChange }: DailyDigestGridProps): JSX.Element {
  const [data, setData] = useState<DailyDigestsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState<boolean>(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    const { data: resp, error: err } = await fetchDailyDigests(digestDate);
    if (err) {
      setError(err);
      setData(null);
    } else if (resp) {
      setData(resp);
    }
    setLoading(false);
  }, [digestDate]);

  useEffect(() => {
    void load();
  }, [load]);

  const handlePrev = (): void => {
    onDigestDateChange(addCalendarDays(digestDate, -1));
  };
  const handleNext = (): void => {
    onDigestDateChange(addCalendarDays(digestDate, 1));
  };

  const handleGenerate = async (): Promise<void> => {
    setGenerating(true);
    setError(null);
    const { data: gen, error: genErr } = await generateDailyDigests(digestDate);
    if (genErr) {
      setError(genErr);
    } else if (gen?.errors?.length) {
      setError(gen.errors.join("; "));
    }
    setGenerating(false);
    await load();
  };

  return (
    <div className="flex flex-col gap-4 min-h-[400px]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handlePrev}
            className="px-2 py-1 rounded-md border border-surface-600 text-surface-300 hover:bg-surface-800 text-sm"
            aria-label="Previous day"
          >
            ←
          </button>
          <span className="text-surface-200 text-sm font-medium min-w-[8rem] text-center">
            {formatDisplayDate(digestDate)}
          </span>
          <button
            type="button"
            onClick={handleNext}
            className="px-2 py-1 rounded-md border border-surface-600 text-surface-300 hover:bg-surface-800 text-sm"
            aria-label="Next day"
          >
            →
          </button>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={generating}
            onClick={() => void handleGenerate()}
            className="text-xs text-surface-300 hover:text-surface-100 disabled:opacity-50"
          >
            {generating ? "Generating…" : "Generate for this day"}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            className="text-xs text-primary-400 hover:text-primary-300"
          >
            Refresh
          </button>
        </div>
      </div>

      {data && data.all_active_sources.length > 0 ? (
        <SourceBadges sources={data.all_active_sources} />
      ) : null}

      {error ? (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-red-300 text-sm">{error}</div>
      ) : null}

      {loading ? (
        <div className="flex items-center justify-center flex-1 min-h-[280px] text-surface-400 gap-2">
          <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          Loading digests…
        </div>
      ) : data && data.members.length === 0 ? (
        <p className="text-surface-500 text-sm">No active team members in this organization.</p>
      ) : data && data.members.length > 0 ? (
        <>
          {data.team_summary ? <TeamSummaryCard summary={data.team_summary} /> : null}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 pb-4">
            {data.members.map((m) => (
              <MemberCard key={m.user_id} member={m} />
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
