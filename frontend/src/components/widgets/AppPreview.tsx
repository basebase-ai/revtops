/**
 * AppPreview — universal app preview component.
 *
 * Renders in priority order based on preferred_mode (from widget_config) or auto:
 * 1. Screenshot (data URL from html2canvas capture)
 * 2. Widget (LLM-inferred data summary card)
 * 3. Mini App (CSS-scaled iframe of the full app)
 * 4. Default chart icon placeholder
 */

import { lazy, Suspense, useEffect, useState } from 'react';
import { apiRequest } from '../../lib/api';
import type { WidgetConfig } from '../../store/types';
import { WidgetCard } from './WidgetCard';

const LazySandpackAppRenderer = lazy(() =>
  import('../apps/SandpackAppRenderer').then((m) => ({ default: m.SandpackAppRenderer }))
);

type PreviewMode = 'auto' | 'screenshot' | 'widget' | 'mini_app' | 'icon';

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

function MiniAppView({ appId, onClick }: { appId: string; onClick?: (id: string) => void }): JSX.Element {
  return (
    <div
      className="w-full aspect-video overflow-hidden relative rounded-xl border border-surface-800 cursor-pointer bg-surface-900"
    >
      {/* Transparent overlay captures clicks instead of the iframe */}
      <div
        className="absolute inset-0 z-10"
        onClick={() => onClick?.(appId)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') onClick?.(appId); }}
      />
      <div className="pointer-events-none" style={{ width: 1280, height: 720, transform: 'scale(var(--preview-scale, 0.2))', transformOrigin: 'top left' }} ref={(el) => {
        if (el) {
          const parent = el.parentElement;
          if (parent) {
            const scale = parent.clientWidth / 1280;
            el.style.setProperty('--preview-scale', String(scale));
            el.style.transform = `scale(${scale})`;
          }
        }
      }}>
        <Suspense fallback={<div className="w-full h-full bg-surface-900" />}>
          <LazySandpackAppRenderer appId={appId} />
        </Suspense>
      </div>
    </div>
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
  // Use preferred_mode from widgetConfig as the default mode
  const defaultMode: PreviewMode = widgetConfig?.preferred_mode ?? 'auto';

  // Screenshot: inline data URL or has_screenshot flag (stripped from list responses)
  const hasScreenshotFlag = Boolean(
    widgetConfig?.screenshot || widgetConfig?.has_screenshot
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

  // Determine effective mode with fallback chain
  let effectiveMode: 'screenshot' | 'widget' | 'mini_app' | 'icon';
  if (defaultMode === 'auto') {
    effectiveMode = (hasScreenshotFlag && screenshotUrl)
      ? 'screenshot'
      : hasWidget
        ? 'widget'
        : 'icon';
  } else if (defaultMode === 'screenshot') {
    effectiveMode = (hasScreenshotFlag && screenshotUrl) ? 'screenshot' : hasWidget ? 'widget' : 'icon';
  } else if (defaultMode === 'widget') {
    effectiveMode = hasWidget ? 'widget' : 'icon';
  } else if (defaultMode === 'mini_app') {
    effectiveMode = 'mini_app';
  } else {
    effectiveMode = 'icon';
  }

  // For widget mode, render WidgetCard directly (it's its own button)
  if (effectiveMode === 'widget' && widgetConfig?.layout) {
    return (
      <WidgetView appId={appId} appTitle={appTitle} widgetConfig={widgetConfig} onClick={onClick} />
    );
  }

  // Mini app mode
  if (effectiveMode === 'mini_app') {
    return <MiniAppView appId={appId} onClick={onClick} />;
  }

  return (
    <button
      onClick={() => onClick?.(appId)}
      className="flex flex-col bg-surface-900 border border-surface-800 rounded-xl overflow-hidden aspect-video w-full hover:border-surface-600 hover:bg-surface-800/50 transition-colors text-left cursor-pointer"
    >
      {effectiveMode === 'screenshot' && screenshotUrl ? (
        <ScreenshotView src={screenshotUrl} title={appTitle} />
      ) : (
        <DefaultIcon title={appTitle} />
      )}
    </button>
  );
}
