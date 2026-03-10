/**
 * Persistent app preview panel shown above the chat messages.
 *
 * Displays the active Basebase App with a header bar containing:
 * - App title + switcher dropdown (when multiple apps exist)
 * - Collapse toggle (hides/shows the renderer)
 * - Close (X) button to dismiss the preview
 */

import { useCallback, useState } from "react";
import { SandpackAppRenderer } from "./SandpackAppRenderer";
import type { AppBlock } from "../../store";

interface AppPreviewPanelProps {
  apps: AppBlock["app"][];
  activeAppId: string | null;
  onActiveAppChange: (appId: string) => void;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  onClose: () => void;
  onAppError?: (errorMessage: string) => void;
  height: number;
}

export function AppPreviewPanel({
  apps,
  activeAppId,
  onActiveAppChange,
  collapsed,
  onCollapsedChange,
  onClose,
  onAppError,
  height,
}: AppPreviewPanelProps): JSX.Element {
  const activeApp = apps.find((a) => a.id === activeAppId) ?? apps[apps.length - 1];
  const [linkCopied, setLinkCopied] = useState(false);

  const handleCopyLink = useCallback(async (): Promise<void> => {
    if (!activeApp) return;
    const url = `${window.location.origin}/apps/${activeApp.id}`;
    await navigator.clipboard.writeText(url);
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  }, [activeApp]);

  if (!activeApp) return <></>;

  return (
    <div
      className="flex flex-col border-b border-surface-700 bg-surface-900 flex-shrink-0"
      style={{ height: collapsed ? "auto" : height }}
    >
      {/* Header bar */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-surface-800 flex-shrink-0">
        {/* App icon */}
        <div className="flex-shrink-0 w-5 h-5 rounded bg-primary-900/40 flex items-center justify-center">
          <svg className="w-3 h-3 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        </div>

        {/* Title / Switcher */}
        {apps.length > 1 ? (
          <select
            value={activeApp.id}
            onChange={(e) => onActiveAppChange(e.target.value)}
            className="bg-transparent text-sm font-medium text-surface-200 truncate border-none focus:outline-none focus:ring-0 cursor-pointer appearance-none pr-5 min-w-0"
            style={{
              backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`,
              backgroundRepeat: "no-repeat",
              backgroundPosition: "right 0 center",
            }}
          >
            {apps.map((app) => (
              <option key={app.id} value={app.id}>
                {app.title ?? "Untitled App"}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-sm font-medium text-surface-200 truncate min-w-0">
            {activeApp.title ?? "Untitled App"}
          </span>
        )}

        <div className="flex-1" />

        {/* Share link */}
        <button
          onClick={() => void handleCopyLink()}
          className="p-1 rounded text-surface-500 hover:text-surface-300 hover:bg-surface-800 transition-colors"
          title={linkCopied ? "Copied!" : "Copy app link"}
        >
          {linkCopied ? (
            <svg className="w-3.5 h-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          ) : (
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
          )}
        </button>

        {/* Collapse toggle */}
        <button
          onClick={() => onCollapsedChange(!collapsed)}
          className="p-1 rounded text-surface-500 hover:text-surface-300 hover:bg-surface-800 transition-colors"
          title={collapsed ? "Expand preview" : "Collapse preview"}
        >
          <svg
            className={`w-3.5 h-3.5 transition-transform ${collapsed ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
          </svg>
        </button>

        {/* Close */}
        <button
          onClick={onClose}
          className="p-1 rounded text-surface-500 hover:text-surface-300 hover:bg-surface-800 transition-colors"
          title="Close preview"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* App renderer body */}
      {!collapsed && (
        <div className="flex-1 overflow-hidden min-h-0">
          <SandpackAppRenderer
            appId={activeApp.id}
            frontendCode={activeApp.frontendCode}
            frontendCodeCompiled={activeApp.frontendCodeCompiled}
            onError={onAppError}
          />
        </div>
      )}
    </div>
  );
}
