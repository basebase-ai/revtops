/**
 * Artifact viewer component for displaying various artifact types.
 *
 * SECURITY: API calls use JWT authentication via the centralized apiRequest
 * function. No user_id is passed in query parameters.
 *
 * Supports:
 * - Text files (.txt) - monospace display with copy button
 * - Markdown files (.md) - rendered with react-markdown
 * - PDF files (.pdf) - download button (PDF generated server-side)
 * - Charts - interactive Plotly charts
 * - Legacy data views (deals, accounts, pipelines)
 */

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiRequest, API_BASE } from "../lib/api";
import { formatDateOnly } from "../lib/dates";
import { supabase } from "../lib/supabase";

// New file-based artifact format
interface FileArtifact {
  id: string;
  title: string;
  filename: string;
  contentType: "text" | "markdown" | "pdf" | "chart";
  mimeType: string;
  content?: string;
}

// Legacy data artifact format
interface DataArtifact {
  id: string;
  type: string;
  title: string;
  data: Record<string, unknown>;
}

interface ArtifactViewerProps {
  artifact: FileArtifact | DataArtifact;
  onDownload?: () => void;
}

// Type guard to check if artifact is file-based
function isFileArtifact(artifact: FileArtifact | DataArtifact): artifact is FileArtifact {
  return "contentType" in artifact && "filename" in artifact;
}

export function ArtifactViewer({
  artifact,
  onDownload,
}: ArtifactViewerProps): JSX.Element {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<boolean>(false);
  const [showDownloadMenu, setShowDownloadMenu] = useState<boolean>(false);

  // Fetch content for file artifacts
  useEffect(() => {
    if (!isFileArtifact(artifact)) return;
    if (artifact.content) {
      setContent(artifact.content);
      return;
    }

    // Fetch content from API using authenticated request
    const fetchContent = async (): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const { data, error: apiError } = await apiRequest<{ content: string }>(
          `/artifacts/${artifact.id}`
        );
        if (apiError) {
          throw new Error(apiError);
        }
        setContent(data?.content ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load artifact");
      } finally {
        setLoading(false);
      }
    };

    void fetchContent();
  }, [artifact]);

  const handleDownload = async (format: "markdown" | "pdf"): Promise<void> => {
    if (!isFileArtifact(artifact)) return;
    setShowDownloadMenu(false);

    try {
      // Get auth token for download request
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      
      const response = await fetch(
        `${API_BASE}/artifacts/${artifact.id}/download?format=${format}`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        }
      );
      if (!response.ok) {
        throw new Error("Failed to download artifact");
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const extension = format === "pdf" ? ".pdf" : ".md";
      const baseName = artifact.filename.replace(/\.[^/.]+$/, "");
      a.download = baseName + extension;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);

      onDownload?.();
    } catch (err) {
      console.error("Download failed:", err);
    }
  };

  const handleCopy = async (): Promise<void> => {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Copy failed:", err);
    }
  };

  // Handle file-based artifacts
  if (isFileArtifact(artifact)) {
    return (
      <div className="h-full flex flex-col">
        {/* Header with download button */}
        <div className="flex items-center justify-between mb-4 pb-3 border-b border-surface-700">
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-surface-700 text-surface-300">
            {getContentTypeLabel(artifact.contentType)}
          </span>
          <div className="relative">
            <button
              onClick={() => setShowDownloadMenu(!showDownloadMenu)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary-600 hover:bg-primary-500 text-white text-sm font-medium transition-colors"
            >
              <DownloadIcon />
              Download
              <ChevronDownIcon />
            </button>
            {showDownloadMenu && (
              <div className="absolute right-0 mt-1 w-36 rounded-md bg-surface-800 border border-surface-700 shadow-lg z-10">
                <button
                  onClick={() => handleDownload("markdown")}
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm text-surface-200 hover:bg-surface-700 transition-colors"
                >
                  Markdown
                </button>
                <button
                  onClick={() => handleDownload("pdf")}
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm text-surface-200 hover:bg-surface-700 transition-colors"
                >
                  PDF
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center h-32">
              <div className="animate-spin w-6 h-6 border-2 border-surface-500 border-t-primary-500 rounded-full" />
            </div>
          )}

          {error && (
            <div className="p-4 rounded-lg bg-red-900/20 border border-red-700 text-red-300 text-sm">
              {error}
            </div>
          )}

          {!loading && !error && content && (
            <>
              {artifact.contentType === "text" && (
                <TextViewer content={content} onCopy={handleCopy} copied={copied} />
              )}
              {artifact.contentType === "markdown" && (
                <MarkdownViewer content={content} />
              )}
              {artifact.contentType === "pdf" && (
                <PdfViewer content={content} />
              )}
              {artifact.contentType === "chart" && <ChartViewer content={content} />}
            </>
          )}
        </div>
      </div>
    );
  }

  // Handle legacy data artifacts
  return (
    <div className="h-full overflow-auto">
      {/* Type badge */}
      <div className="mb-4">
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-primary-900 text-primary-200">
          {artifact.type}
        </span>
      </div>

      {/* Data display */}
      <div className="space-y-4">{renderLegacyData(artifact.data)}</div>
    </div>
  );
}

// =============================================================================
// File Type Viewers
// =============================================================================

function TextViewer({
  content,
  onCopy,
  copied,
}: {
  content: string;
  onCopy: () => void;
  copied: boolean;
}): JSX.Element {
  return (
    <div className="relative">
      <button
        onClick={onCopy}
        className="absolute top-2 right-2 p-1.5 rounded bg-surface-700 hover:bg-surface-600 text-surface-400 hover:text-surface-200 transition-colors"
        title="Copy to clipboard"
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </button>
      <pre className="p-4 rounded-lg bg-surface-800 text-surface-300 text-sm overflow-auto font-mono whitespace-pre-wrap">
        {content}
      </pre>
    </div>
  );
}

function MarkdownViewer({ content }: { content: string }): JSX.Element {
  return (
    <div className="prose prose-sm prose-invert max-w-none prose-p:my-2 prose-headings:my-3 prose-ul:my-2 prose-ol:my-2 prose-li:my-1 prose-pre:my-3 prose-code:text-primary-300 prose-code:bg-surface-800 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-pre:bg-surface-800 prose-table:text-sm prose-th:bg-surface-700/50 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-td:border-surface-700 prose-th:border-surface-700">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function PdfViewer({
  content,
}: {
  content: string;
}): JSX.Element {
  // Show markdown preview since we store markdown source
  return (
    <div className="space-y-4">
      <div className="p-3 rounded-lg bg-surface-800 border border-surface-700">
        <div className="flex items-center gap-2 text-surface-300">
          <PdfIcon />
          <span className="text-sm text-surface-400">Use the Download button above to export as PDF</span>
        </div>
      </div>

      {/* Show markdown preview */}
      <div>
        <div className="text-xs text-surface-500 uppercase tracking-wider mb-2">
          Preview
        </div>
        <MarkdownViewer content={content} />
      </div>
    </div>
  );
}

interface PlotlySpec {
  data: Array<Record<string, unknown>>;
  layout?: Record<string, unknown>;
}

function ChartViewer({ content }: { content: string }): JSX.Element {
  const [chartError, setChartError] = useState<string | null>(null);
  const [PlotComponent, setPlotComponent] = useState<typeof import("react-plotly.js").default | null>(null);
  const [parsedSpec, setParsedSpec] = useState<PlotlySpec | null>(null);

  // Dynamically import react-plotly.js to avoid SSR issues
  useEffect(() => {
    import("react-plotly.js")
      .then((mod) => setPlotComponent(() => mod.default))
      .catch(() => setChartError("Failed to load chart library"));
  }, []);

  // Parse the chart spec
  useEffect(() => {
    try {
      const spec = JSON.parse(content) as PlotlySpec;
      setParsedSpec(spec);
    } catch {
      setChartError("Invalid chart data");
    }
  }, [content]);

  if (chartError) {
    return (
      <div className="p-4 rounded-lg bg-red-900/20 border border-red-700 text-red-300 text-sm">
        {chartError}
      </div>
    );
  }

  if (!PlotComponent || !parsedSpec) {
    return (
      <div className="flex items-center justify-center h-32">
        <div className="animate-spin w-6 h-6 border-2 border-surface-500 border-t-primary-500 rounded-full" />
      </div>
    );
  }

  return (
    <div className="w-full h-[400px]">
      <PlotComponent
        data={parsedSpec.data}
        layout={{
          ...parsedSpec.layout,
          autosize: true,
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent",
          font: { color: "#a1a1aa" },
          margin: { t: 40, r: 20, b: 40, l: 50 },
        }}
        config={{ responsive: true, displayModeBar: false }}
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  );
}

// =============================================================================
// Legacy Data Views (backward compatibility)
// =============================================================================

function renderLegacyData(data: Record<string, unknown>): JSX.Element {
  if ("count" in data && "deals" in data) {
    return <DealsView data={data as unknown as DealsData} />;
  }
  if ("count" in data && "accounts" in data) {
    return <AccountsView data={data as unknown as AccountsData} />;
  }
  if ("by_stage" in data) {
    return <PipelineView data={data as unknown as PipelineData} />;
  }
  return <JsonView data={data} />;
}

interface Deal {
  id: string;
  name: string;
  amount: number | null;
  stage: string | null;
  close_date: string | null;
}

interface DealsData {
  count: number;
  deals: Deal[];
}

function DealsView({ data }: { data: DealsData }): JSX.Element {
  return (
    <div>
      <div className="text-sm text-surface-400 mb-3">
        {data.count} deal{data.count !== 1 ? "s" : ""} found
      </div>
      <div className="space-y-2">
        {data.deals.map((deal) => (
          <div
            key={deal.id}
            className="p-3 rounded-lg bg-surface-800 border border-surface-700"
          >
            <div className="font-medium text-surface-100">{deal.name}</div>
            <div className="flex items-center gap-4 mt-1 text-sm">
              {deal.amount && (
                <span className="text-green-400">${deal.amount.toLocaleString()}</span>
              )}
              {deal.stage && <span className="text-surface-400">{deal.stage}</span>}
              {deal.close_date && (
                <span className="text-surface-500">
                  Closes {formatDateOnly(deal.close_date)}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface Account {
  id: string;
  name: string;
  industry: string | null;
  annual_revenue: number | null;
}

interface AccountsData {
  count: number;
  accounts: Account[];
}

function AccountsView({ data }: { data: AccountsData }): JSX.Element {
  return (
    <div>
      <div className="text-sm text-surface-400 mb-3">
        {data.count} account{data.count !== 1 ? "s" : ""} found
      </div>
      <div className="space-y-2">
        {data.accounts.map((account) => (
          <div
            key={account.id}
            className="p-3 rounded-lg bg-surface-800 border border-surface-700"
          >
            <div className="font-medium text-surface-100">{account.name}</div>
            <div className="flex items-center gap-4 mt-1 text-sm">
              {account.industry && (
                <span className="text-surface-400">{account.industry}</span>
              )}
              {account.annual_revenue && (
                <span className="text-green-400">
                  ${(account.annual_revenue / 1000000).toFixed(1)}M ARR
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface StageData {
  count: number;
  total_amount: number;
  avg_amount: number;
}

interface PipelineData {
  by_stage: Record<string, StageData>;
  total_deals: number;
  total_pipeline_value: number;
}

function PipelineView({ data }: { data: PipelineData }): JSX.Element {
  const stages = Object.entries(data.by_stage);

  return (
    <div>
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="p-4 rounded-lg bg-surface-800 border border-surface-700">
          <div className="text-2xl font-bold text-surface-100">{data.total_deals}</div>
          <div className="text-sm text-surface-400">Total Deals</div>
        </div>
        <div className="p-4 rounded-lg bg-surface-800 border border-surface-700">
          <div className="text-2xl font-bold text-green-400">
            ${(data.total_pipeline_value / 1000000).toFixed(1)}M
          </div>
          <div className="text-sm text-surface-400">Pipeline Value</div>
        </div>
      </div>

      <div className="space-y-2">
        {stages.map(([stage, stageData]) => (
          <div
            key={stage}
            className="p-3 rounded-lg bg-surface-800 border border-surface-700"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-surface-100">{stage}</span>
              <span className="text-surface-400">{stageData.count} deals</span>
            </div>
            <div className="mt-1 text-sm text-green-400">
              ${stageData.total_amount.toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function JsonView({ data }: { data: Record<string, unknown> }): JSX.Element {
  return (
    <pre className="p-4 rounded-lg bg-surface-800 text-surface-300 text-sm overflow-auto font-mono">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// =============================================================================
// Helper Components & Functions
// =============================================================================

function getContentTypeLabel(contentType: string): string {
  switch (contentType) {
    case "text":
      return "Text File";
    case "markdown":
      return "Markdown";
    case "pdf":
      return "PDF Document";
    case "chart":
      return "Interactive Chart";
    default:
      return "File";
  }
}

function DownloadIcon(): JSX.Element {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
      />
    </svg>
  );
}

function CopyIcon(): JSX.Element {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
      />
    </svg>
  );
}

function CheckIcon(): JSX.Element {
  return (
    <svg
      className="w-4 h-4 text-green-400"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M5 13l4 4L19 7"
      />
    </svg>
  );
}

function PdfIcon(): JSX.Element {
  return (
    <svg className="w-5 h-5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1.5}
        d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
      />
    </svg>
  );
}

function ChevronDownIcon(): JSX.Element {
  return (
    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
    </svg>
  );
}

export type { FileArtifact, DataArtifact };
