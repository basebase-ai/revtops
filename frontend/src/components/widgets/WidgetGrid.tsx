/**
 * WidgetGrid — responsive grid of AppPreview cards for the Home view.
 */

import type { WidgetData } from '../../store/types';
import { AppPreview } from './AppPreview';

interface WidgetGridProps {
  widgets: WidgetData[];
  onWidgetClick?: (appId: string) => void;
}

export function WidgetGrid({ widgets, onWidgetClick }: WidgetGridProps): JSX.Element | null {
  if (widgets.length === 0) return null;

  return (
    <div className="mb-6 mr-2 md:mr-4">
      <h2 className="text-sm font-medium text-surface-400 mb-3">Apps</h2>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {widgets.map((w) => (
          <div key={w.id}>
            <AppPreview
              appId={w.id}
              appTitle={w.title}
              widgetConfig={w.widget_config}
              onClick={onWidgetClick}
            />
            <div className="mt-1 px-1">
              <div className="text-xs font-medium text-surface-300 truncate">{w.title}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
