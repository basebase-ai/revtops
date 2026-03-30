/**
 * AppPreview — universal app preview component.
 *
 * Renders in priority order:
 * 1. Screenshot (data URL from html2canvas capture)
 * 2. Widget (LLM-inferred data summary card)
 * 3. Default chart icon placeholder
 *
 * Debug: click the cycle icon (top-right) to force a specific mode.
 */

import { useEffect, useState } from 'react';
import { apiRequest } from '../../lib/api';
import type { WidgetConfig } from '../../store/types';
import { WidgetCard } from './WidgetCard';

type PreviewMode = 'auto' | 'screenshot' | 'widget' | 'icon';
const MODES: PreviewMode[] = ['auto', 'screenshot', 'widget', 'icon'];
const MODE_LABELS: Record<PreviewMode, string> = {
  auto: 'Auto',
  screenshot: 'Screenshot',
  widget: 'Widget',
  icon: 'Icon',
};

interface AppPreviewProps {
  appId: string;
  appTitle: string;
  widgetConfig?: WidgetConfig | null;
  onClick?: (appId: string) => void;
}

function ScreenshotView({ src, title }: { src: string; title: string }): JSX.Element {
  return (
    <img src={src} alt={title} className="w-full h-full object-cover object-top" />
  );
}

function WidgetView({ appId, appTitle, widgetConfig, onClick }: {
  appId: string; appTitle: string; widgetConfig: WidgetConfig; onClick?: (id: string) => void;
}): JSX.Element {
  return (
    <WidgetCard appId={appId} appTitle={appTitle} widgetConfig={widgetConfig} onClick={onClick} />
  );
}

function DefaultIcon({ title }: { title: string }): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center flex-1 gap-2">
      <svg className="w-8 h-8 text-surface-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path
          strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
        />
      </svg>
      <span className="text-xs text-surface-400 truncate max-w-full">{title}</span>
    </div>
  );
}

export function AppPreview({ appId, appTitle, widgetConfig, onClick }: AppPreviewProps): JSX.Element {
  const [modeOverride, setModeOverride] = useState<PreviewMode>('auto');

  // Screenshot: inline data URL or has_screenshot flag (stripped from list responses)
  const hasScreenshotFlag = Boolean(
    widgetConfig?.screenshot || (widgetConfig as Record<string, unknown> | undefined)?.has_screenshot
  );
  const hasWidget = Boolean(widgetConfig?.layout);
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(
    widgetConfig?.screenshot ?? null
  );

  // Lazy-fetch screenshot on demand when flag is set but no inline URL
  useEffect(() => {
    if (screenshotUrl || !hasScreenshotFlag || widgetConfig?.screenshot) return;
    let cancelled = false;
    apiRequest<{ screenshot: string | null }>(`/apps/widgets/${appId}/screenshot`).then((resp) => {
      if (!cancelled && resp.data?.screenshot) setScreenshotUrl(resp.data.screenshot);
    });
    return () => { cancelled = true; };
  }, [appId, hasScreenshotFlag, screenshotUrl, widgetConfig?.screenshot]);

  // Determine what to show
  const effectiveMode: 'screenshot' | 'widget' | 'icon' =
    modeOverride === 'auto'
      ? (hasScreenshotFlag && screenshotUrl) ? 'screenshot' : hasWidget ? 'widget' : 'icon'
      : modeOverride;

  const cycleMode = (e: React.MouseEvent): void => {
    e.stopPropagation();
    const idx = MODES.indexOf(modeOverride);
    setModeOverride(MODES[(idx + 1) % MODES.length]!);
  };

  // For widget mode, render WidgetCard directly (it's its own button)
  if (effectiveMode === 'widget' && widgetConfig?.layout) {
    return (
      <div className="relative group">
        <WidgetView appId={appId} appTitle={appTitle} widgetConfig={widgetConfig} onClick={onClick} />
        <button
          onClick={cycleMode}
          className="absolute top-1.5 left-1.5 p-1 rounded bg-surface-800/80 text-surface-400 hover:text-surface-100 opacity-0 group-hover:opacity-100 transition-opacity z-10"
          title={`Mode: ${MODE_LABELS[modeOverride]} → click to cycle`}
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
        {modeOverride !== 'auto' && (
          <span className="absolute top-1.5 right-1.5 px-1 py-0.5 rounded text-[8px] font-bold bg-surface-800/80 text-surface-300 opacity-0 group-hover:opacity-100 transition-opacity z-10">
            {MODE_LABELS[modeOverride]}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="relative group">
      <button
        onClick={() => onClick?.(appId)}
        className="flex flex-col bg-surface-900 border border-surface-800 rounded-xl overflow-hidden h-[140px] w-full hover:border-surface-600 hover:bg-surface-800/50 transition-colors text-left cursor-pointer"
      >
        {effectiveMode === 'screenshot' && screenshotUrl ? (
          <ScreenshotView src={screenshotUrl} title={appTitle} />
        ) : (
          <DefaultIcon title={appTitle} />
        )}
      </button>
      <button
        onClick={cycleMode}
        className="absolute top-1.5 left-1.5 p-1 rounded bg-surface-800/80 text-surface-400 hover:text-surface-100 opacity-0 group-hover:opacity-100 transition-opacity z-10"
        title={`Mode: ${MODE_LABELS[modeOverride]} → click to cycle`}
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      </button>
      {modeOverride !== 'auto' && (
        <span className="absolute top-1.5 right-1.5 px-1 py-0.5 rounded text-[8px] font-bold bg-surface-800/80 text-surface-300 opacity-0 group-hover:opacity-100 transition-opacity z-10">
          {MODE_LABELS[modeOverride]}
        </span>
      )}
    </div>
  );
}
