/**
 * WidgetCard — compact app preview showing 1-2 key data points.
 *
 * Renders one of four layout types (big_number, mini_list, status, sparkline)
 * based on the widget_config stored on the app.
 */

import type {
  BigNumberSlots,
  MiniListSlots,
  SparklineSlots,
  StatusSlots,
  WidgetConfig,
} from '../../store/types';

// ---------------------------------------------------------------------------
// Layout sub-components
// ---------------------------------------------------------------------------

function BigNumber({ slots }: { slots: BigNumberSlots }): JSX.Element {
  const trendIcon =
    slots.trend === 'up' ? '\u2191' : slots.trend === 'down' ? '\u2193' : slots.trend === 'flat' ? '\u2192' : null;
  const trendColor =
    slots.trend === 'up'
      ? 'text-emerald-400'
      : slots.trend === 'down'
        ? 'text-red-400'
        : 'text-surface-400';

  return (
    <div className="flex flex-col items-center justify-center flex-1 gap-1">
      <div className="flex items-baseline gap-1.5">
        <span className="text-2xl font-bold text-surface-100">{slots.value}</span>
        {trendIcon && <span className={`text-sm font-medium ${trendColor}`}>{trendIcon}</span>}
      </div>
      <span className="text-xs text-surface-400">{slots.label}</span>
    </div>
  );
}

function MiniList({ slots, maxRows = 3 }: { slots: MiniListSlots; maxRows?: number }): JSX.Element {
  return (
    <div className="flex flex-col gap-1.5 flex-1 justify-center w-full px-1">
      {(slots.rows || []).slice(0, maxRows).map((row, i) => (
        <div key={i} className="flex justify-between items-center text-xs">
          <span className="text-surface-400 truncate mr-2">{row.label}</span>
          <span className="text-surface-100 font-medium whitespace-nowrap">{row.value}</span>
        </div>
      ))}
    </div>
  );
}

function Status({ slots }: { slots: StatusSlots }): JSX.Element {
  const iconColor =
    slots.icon === 'warning'
      ? 'text-amber-400'
      : slots.icon === 'success'
        ? 'text-emerald-400'
        : 'text-blue-400';
  const iconChar = slots.icon === 'warning' ? '\u26A0' : slots.icon === 'success' ? '\u2713' : '\u2139';

  return (
    <div className="flex items-center gap-2 flex-1 justify-center">
      <span className={`text-lg ${iconColor}`}>{iconChar}</span>
      <span className="text-sm text-surface-200">{slots.text}</span>
    </div>
  );
}

function Sparkline({ slots }: { slots: SparklineSlots }): JSX.Element {
  const values = slots.values || [];
  if (values.length < 2) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 gap-1">
        <span className="text-xl font-bold text-surface-100">{slots.current}</span>
        <span className="text-xs text-surface-400">{slots.label}</span>
      </div>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 120;
  const h = 32;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / range) * h;
      return `${x},${y}`;
    })
    .join(' ');

  return (
    <div className="flex flex-col items-center justify-center flex-1 gap-1">
      <svg width={w} height={h} className="overflow-visible">
        <polyline
          points={points}
          fill="none"
          stroke="#6366f1"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="flex items-baseline gap-1.5">
        <span className="text-lg font-bold text-surface-100">{slots.current}</span>
      </div>
      <span className="text-xs text-surface-400">{slots.label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main WidgetCard
// ---------------------------------------------------------------------------

interface WidgetCardProps {
  appId: string;
  appTitle: string;
  widgetConfig: WidgetConfig;
  onClick?: (appId: string) => void;
}

export function WidgetCard({ appId, appTitle, widgetConfig, onClick }: WidgetCardProps): JSX.Element {
  const { layout, title, slots } = widgetConfig;
  const detailLevel = widgetConfig.detail_level;
  const miniListMaxRows = detailLevel === 'detailed' ? 5 : 3;

  return (
    <button
      onClick={() => onClick?.(appId)}
      className="flex flex-col bg-surface-900 border border-surface-800 rounded-xl p-3 h-[140px] w-full hover:border-surface-600 hover:bg-surface-800/50 transition-colors text-left cursor-pointer"
    >
      <div className="text-[10px] font-medium text-surface-500 uppercase tracking-wider mb-1 truncate">
        {title || appTitle}
      </div>
      {layout === 'big_number' && <BigNumber slots={slots as BigNumberSlots} />}
      {layout === 'mini_list' && <MiniList slots={slots as MiniListSlots} maxRows={miniListMaxRows} />}
      {layout === 'status' && <Status slots={slots as StatusSlots} />}
      {layout === 'sparkline' && <Sparkline slots={slots as SparklineSlots} />}
    </button>
  );
}
