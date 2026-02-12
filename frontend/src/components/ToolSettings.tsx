/**
 * Tool Settings Panel for configuring approval preferences.
 * 
 * Similar to Cursor's "yolo mode" - allows users to auto-approve
 * certain tools that normally require explicit approval.
 * 
 * Approval-gated tools (for example email send, Slack post, and memory save)
 * require approval by default but can be auto-approved here.
 */

import { useState, useEffect, useCallback } from 'react';
import { apiRequest } from '../lib/api';

interface ToolInfo {
  name: string;
  description: string;
  category: string;
  default_requires_approval: boolean;
  user_auto_approve: boolean | null;
}

interface ToolSettingsProps {
  userId: string;
  onClose: () => void;
}

// Tool display names
const TOOL_LABELS: Record<string, string> = {
  crm_write: 'CRM Write',
  send_email_from: 'Send Email',
  send_slack: 'Post to Slack',
  trigger_sync: 'Trigger Sync',
  save_memory: 'Save Interim Values',
};

// Tool descriptions for the UI
const TOOL_DESCRIPTIONS: Record<string, string> = {
  crm_write: 'Create or update contacts, companies, and deals in HubSpot',
  send_email_from: 'Send emails from your connected Gmail or Outlook',
  send_slack: 'Post messages to your connected Slack workspace',
  trigger_sync: 'Trigger data sync from connected integrations',
  save_memory: 'Store user-scoped preferences or interim facts for future conversations',
};

export function ToolSettings({ userId, onClose }: ToolSettingsProps): JSX.Element {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatingTool, setUpdatingTool] = useState<string | null>(null);

  const loadToolSettings = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    setError(null);
    
    try {
      const { data, error: apiError } = await apiRequest<{ tools: ToolInfo[] }>(
        `/api/tools/registry?user_id=${userId}`,
      );
      
      if (apiError || !data) {
        throw new Error(apiError ?? 'Failed to load tool settings');
      }
      
      // Filter to only show tools that require approval by default
      const approvalTools = data.tools.filter(t => t.default_requires_approval);
      setTools(approvalTools);
    } catch (err) {
      console.error('[ToolSettings] Failed to load:', err);
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    } finally {
      setIsLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void loadToolSettings();
  }, [loadToolSettings]);

  const handleToggle = async (toolName: string, currentValue: boolean | null): Promise<void> => {
    const newValue = !(currentValue ?? false);
    setUpdatingTool(toolName);
    
    try {
      const { error: apiError } = await apiRequest(
        `/api/tools/settings/${toolName}?user_id=${userId}`,
        {
          method: 'PUT',
          body: JSON.stringify({ auto_approve: newValue }),
        },
      );
      
      if (apiError) {
        throw new Error(apiError);
      }
      
      // Update local state
      setTools(prev => prev.map(t => 
        t.name === toolName 
          ? { ...t, user_auto_approve: newValue }
          : t
      ));
    } catch (err) {
      console.error('[ToolSettings] Failed to update:', err);
      setError(err instanceof Error ? err.message : 'Failed to update setting');
    } finally {
      setUpdatingTool(null);
    }
  };

  const autoApprovedCount = tools.filter(t => t.user_auto_approve).length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-surface-900 rounded-lg shadow-xl max-w-lg w-full mx-4 max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-surface-700 flex-shrink-0">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">Tool Permissions</h2>
            <button
              onClick={onClose}
              className="text-surface-400 hover:text-white transition-colors"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <p className="text-sm text-surface-400 mt-1">
            Control which actions run automatically without asking
          </p>
        </div>
        
        {/* Content */}
        <div className="p-4 overflow-y-auto flex-1">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-6 w-6 border-2 border-accent-500 border-t-transparent" />
            </div>
          ) : error ? (
            <div className="text-red-400 text-sm py-4 text-center">{error}</div>
          ) : tools.length === 0 ? (
            <div className="text-surface-400 text-sm py-4 text-center">
              No tools require approval configuration
            </div>
          ) : (
            <div className="space-y-3">
              <div className="text-xs text-surface-500 uppercase tracking-wide mb-3">
                Actions Requiring Approval
              </div>
              
              {tools.map(tool => {
                const isAutoApproved = tool.user_auto_approve ?? false;
                const isUpdating = updatingTool === tool.name;
                
                return (
                  <div
                    key={tool.name}
                    className="flex items-start gap-3 p-3 rounded-lg bg-surface-800 hover:bg-surface-750 transition-colors"
                  >
                    {/* Toggle */}
                    <button
                      onClick={() => handleToggle(tool.name, tool.user_auto_approve)}
                      disabled={isUpdating}
                      className={`
                        mt-0.5 flex-shrink-0 w-10 h-6 rounded-full transition-colors
                        ${isAutoApproved ? 'bg-accent-600' : 'bg-surface-600'}
                        ${isUpdating ? 'opacity-50 cursor-wait' : 'cursor-pointer'}
                      `}
                    >
                      <div
                        className={`
                          w-4 h-4 rounded-full bg-white shadow transform transition-transform mt-1
                          ${isAutoApproved ? 'translate-x-5 ml-0.5' : 'translate-x-1'}
                        `}
                      />
                    </button>
                    
                    {/* Label */}
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-white">
                        {TOOL_LABELS[tool.name] ?? tool.name}
                      </div>
                      <div className="text-xs text-surface-400 mt-0.5">
                        {TOOL_DESCRIPTIONS[tool.name] ?? tool.description}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        
        {/* Footer */}
        <div className="p-4 border-t border-surface-700 flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="text-xs text-surface-500">
              {autoApprovedCount > 0 ? (
                <span className="text-amber-400">
                  {autoApprovedCount} tool{autoApprovedCount !== 1 ? 's' : ''} will run without asking
                </span>
              ) : (
                'All approval-gated actions will ask for approval'
              )}
            </div>
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium bg-surface-700 hover:bg-surface-600 text-white rounded-lg transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
