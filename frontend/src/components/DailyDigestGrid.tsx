/**
 * Team daily digest cards — one card per org member for a selected calendar date.
 * Supports search filtering and card/list layout toggle for large teams.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
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

type DigestLayout = "cards" | "list";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function formatDayMonth(isoDate: string): string {
  const [, mm, dd] = isoDate.split("-");
  if (!mm || !dd) return isoDate;
  return `${dd}/${mm}`;
}

function getNarrative(summary: DigestSummaryJson | null): string {
  return summary?.narrative?.trim() ?? "";
}

// ---------------------------------------------------------------------------
// Small presentational components
// ---------------------------------------------------------------------------

function SourceBadges({ sources }: { sources: string[] }): JSX.Element | null {
  if (sources.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
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
  const narrative: string = getNarrative(summary);
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

// ---------------------------------------------------------------------------
// Markdown components for team summary
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Member cards — full (list view) and compact (card view)
// ---------------------------------------------------------------------------

function MemberAvatar({ member, size }: { member: DigestMemberRow; size: "sm" | "md" }): JSX.Element {
  const displayName: string = member.name?.trim() || member.user_id.slice(0, 8);
  const px: string = size === "sm" ? "w-8 h-8 text-xs" : "w-10 h-10 text-sm";
  if (member.avatar_url) {
    return (
      <img
        src={member.avatar_url}
        alt=""
        className={`${px} rounded-full object-cover border border-surface-600`}
      />
    );
  }
  return (
    <div className={`${px} rounded-full bg-surface-700 flex items-center justify-center text-surface-300 font-medium`}>
      {displayName.slice(0, 1).toUpperCase()}
    </div>
  );
}

function MemberCard({ member }: { member: DigestMemberRow }): JSX.Element {
  const displayName: string = member.name?.trim() || member.user_id.slice(0, 8);
  return (
    <article className="rounded-xl border border-surface-700 bg-surface-900/50 p-4 md:p-5 flex flex-col gap-3 max-h-[28rem] overflow-y-auto">
      <div className="flex items-center gap-3">
        <MemberAvatar member={member} size="md" />
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

function MemberCardCompact({
  member,
  expanded,
  onToggle,
}: {
  member: DigestMemberRow;
  expanded: boolean;
  onToggle: () => void;
}): JSX.Element {
  const displayName: string = member.name?.trim() || member.user_id.slice(0, 8);
  const narrative: string = getNarrative(member.summary);

  if (expanded) {
    return (
      <article className="rounded-xl border border-primary-500/40 bg-surface-900/70 p-4 md:p-5 flex flex-col gap-3 max-h-[28rem] overflow-y-auto">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <MemberAvatar member={member} size="md" />
            <div>
              <h3 className="text-surface-100 font-medium">{displayName}</h3>
              {member.generated_at ? (
                <p className="text-xs text-surface-500">
                  Updated {new Date(member.generated_at).toLocaleString()}
                </p>
              ) : null}
            </div>
          </div>
          <button
            type="button"
            onClick={onToggle}
            className="text-surface-400 hover:text-surface-200 p-1"
            aria-label="Collapse"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <SourceBadges sources={member.active_sources} />
        {renderSummary(member.summary)}
      </article>
    );
  }

  return (
    <button
      type="button"
      onClick={onToggle}
      className="rounded-xl border border-surface-700 bg-surface-900/50 p-4 md:p-5 flex flex-col gap-3 text-left hover:border-surface-500 hover:bg-surface-800/50 transition-colors cursor-pointer w-full min-h-[10rem]"
    >
      <div className="flex items-center gap-2.5">
        <MemberAvatar member={member} size="sm" />
        <h3 className="text-surface-100 font-medium text-sm truncate">{displayName}</h3>
      </div>
      {narrative ? (
        <p className="text-surface-400 text-sm leading-relaxed line-clamp-5">{narrative}</p>
      ) : (
        <p className="text-surface-600 text-sm italic">No activity</p>
      )}
      {member.active_sources.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {member.active_sources.slice(0, 4).map((s) => {
            const display = CONNECTOR_DISPLAY[s];
            const iconId: string = display?.icon ?? DEFAULT_CONNECTOR_ICON;
            const color: string = display?.color ?? DEFAULT_CONNECTOR_COLOR;
            const bgClass: string = isImageIcon(iconId) ? "" : getConnectorColorClass(color);
            return (
              <span
                key={s}
                className={`${bgClass} rounded p-0.5 text-white flex items-center justify-center`}
                title={display?.label ?? s}
              >
                {renderConnectorIcon(iconId, "w-3 h-3")}
              </span>
            );
          })}
          {member.active_sources.length > 4 && (
            <span className="text-[10px] text-surface-500">+{member.active_sources.length - 4}</span>
          )}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Layout toggle icons
// ---------------------------------------------------------------------------

function GridIcon({ active }: { active: boolean }): JSX.Element {
  return (
    <svg className={`w-4 h-4 ${active ? "text-surface-100" : "text-surface-500"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
    </svg>
  );
}

function ListIcon({ active }: { active: boolean }): JSX.Element {
  return (
    <svg className={`w-4 h-4 ${active ? "text-surface-100" : "text-surface-500"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

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
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [layout, setLayout] = useState<DigestLayout>("cards");
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);

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

  const filteredMembers: DigestMemberRow[] = useMemo(() => {
    if (!data) return [];
    const q: string = searchQuery.trim().toLowerCase();
    if (q.length === 0) return data.members;
    return data.members.filter((m) => {
      const name: string = (m.name ?? m.user_id).toLowerCase();
      return name.includes(q);
    });
  }, [data, searchQuery]);

  const toggleExpanded = useCallback((userId: string) => {
    setExpandedUserId((prev) => (prev === userId ? null : userId));
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar: date nav + search + layout toggle + actions */}
      <div className="flex flex-col md:flex-row md:flex-wrap md:items-center md:justify-between gap-3">
        <div className="flex items-center justify-center md:justify-start gap-2 w-full md:w-auto">
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

        <div className="flex items-center gap-2 w-full md:w-auto overflow-x-auto pr-1">
          {/* Search */}
          <div className="relative">
            <svg className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-surface-500 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search members…"
              className="w-32 sm:w-40 pl-7 pr-2 py-1 rounded-md border border-surface-600 bg-surface-800 text-surface-200 text-xs placeholder:text-surface-500 focus:outline-none focus:border-primary-500"
            />
          </div>

          {/* Layout toggle */}
          <div className="flex items-center rounded-md border border-surface-600 overflow-hidden">
            <button
              type="button"
              onClick={() => setLayout("cards")}
              className={`p-1.5 ${layout === "cards" ? "bg-surface-700" : "bg-surface-800 hover:bg-surface-700"} transition-colors`}
              aria-label="Card view"
              title="Card view"
            >
              <GridIcon active={layout === "cards"} />
            </button>
            <button
              type="button"
              onClick={() => setLayout("list")}
              className={`p-1.5 ${layout === "list" ? "bg-surface-700" : "bg-surface-800 hover:bg-surface-700"} transition-colors`}
              aria-label="List view"
              title="List view"
            >
              <ListIcon active={layout === "list"} />
            </button>
          </div>

          {/* Generate / Refresh */}
          <button
            type="button"
            disabled={generating}
            onClick={() => void handleGenerate()}
            className="inline-flex items-center justify-center min-w-[8.5rem] md:min-w-[9rem] h-8 px-3 rounded-md border border-surface-600 text-xs text-surface-300 hover:text-surface-100 hover:bg-surface-800 disabled:opacity-50"
          >
            {generating ? "Generating…" : `Generate for ${formatDayMonth(digestDate)}`}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center justify-center min-w-[8.5rem] md:min-w-[9rem] h-8 px-3 rounded-md border border-primary-500/40 text-xs text-primary-400 hover:text-primary-300 hover:bg-primary-500/10"
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

          {filteredMembers.length === 0 ? (
            <p className="text-surface-500 text-sm py-4">No members matching &ldquo;{searchQuery}&rdquo;</p>
          ) : layout === "cards" ? (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 pb-4">
              {filteredMembers.map((m) => (
                <MemberCardCompact
                  key={m.user_id}
                  member={m}
                  expanded={expandedUserId === m.user_id}
                  onToggle={() => toggleExpanded(m.user_id)}
                />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 pb-4">
              {filteredMembers.map((m) => (
                <MemberCard key={m.user_id} member={m} />
              ))}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
