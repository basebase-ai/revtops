/**
 * Modal for selecting which app to display on the Home tab.
 *
 * Fetches all apps for the org and lets the user pick one (or clear).
 */

import { useEffect, useState, useCallback } from "react";
import { apiRequest } from "../../lib/api";

interface AppListItem {
  id: string;
  title: string | null;
  description: string | null;
  creator_name: string | null;
}

interface HomeAppPickerProps {
  currentAppId: string | null;
  onSelect: (appId: string | null) => void;
  onClose: () => void;
}

export function HomeAppPicker({
  currentAppId,
  onSelect,
  onClose,
}: HomeAppPickerProps): JSX.Element {
  const [apps, setApps] = useState<AppListItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);

  useEffect(() => {
    const fetchApps = async (): Promise<void> => {
      const resp = await apiRequest<{ apps: AppListItem[] }>("/apps");
      if (resp.data) {
        setApps(resp.data.apps);
      }
      setLoading(false);
    };
    void fetchApps();
  }, []);

  const handleSelect = useCallback(
    async (appId: string | null): Promise<void> => {
      setSaving(true);
      const resp = await apiRequest<{ status: string }>("/apps/home", {
        method: "PATCH",
        body: JSON.stringify({ app_id: appId }),
      });
      setSaving(false);
      if (resp.data?.status === "success") {
        onSelect(appId);
      }
    },
    [onSelect],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />

      {/* Modal */}
      <div className="relative w-full max-w-md mx-4 bg-surface-900 border border-surface-700 rounded-xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface-700">
          <div>
            <h2 className="text-base font-semibold text-surface-100">
              Customize Home
            </h2>
            <p className="text-xs text-surface-400 mt-0.5">
              Choose an app to display on your Home tab
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-surface-400 hover:text-surface-200 p-1"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="max-h-80 overflow-y-auto p-3">
          {loading ? (
            <div className="flex justify-center py-8">
              <div className="w-6 h-6 border-2 border-surface-600 border-t-primary-500 rounded-full animate-spin" />
            </div>
          ) : (
            <div className="space-y-1.5">
              {/* Default (no app) option */}
              <button
                onClick={() => void handleSelect(null)}
                disabled={saving}
                className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                  currentAppId === null
                    ? "border-primary-500 bg-primary-500/10"
                    : "border-surface-700 hover:border-surface-600 hover:bg-surface-800/50"
                }`}
              >
                <div className="font-medium text-surface-200 text-sm">
                  Default Pipeline View
                </div>
                <div className="text-xs text-surface-400 mt-0.5">
                  Show the built-in deals &amp; pipeline summary
                </div>
              </button>

              {/* App options */}
              {apps.map((app) => (
                <button
                  key={app.id}
                  onClick={() => void handleSelect(app.id)}
                  disabled={saving}
                  className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                    currentAppId === app.id
                      ? "border-primary-500 bg-primary-500/10"
                      : "border-surface-700 hover:border-surface-600 hover:bg-surface-800/50"
                  }`}
                >
                  <div className="font-medium text-surface-200 text-sm">
                    {app.title ?? "Untitled App"}
                  </div>
                  {app.description && (
                    <div className="text-xs text-surface-400 mt-0.5 truncate">
                      {app.description}
                    </div>
                  )}
                </button>
              ))}

              {apps.length === 0 && (
                <div className="text-center py-6 text-surface-500 text-sm">
                  No apps yet. Ask Penny to create one!
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
