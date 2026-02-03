/**
 * Workflows page - view and manage saved agent workflows.
 * 
 * Features:
 * - List user's workflows with status
 * - View workflow steps/recipe
 * - See last run time and results
 * - Manually trigger workflows
 * - Delete workflows
 */

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAppStore } from '../store';
import { API_BASE } from '../lib/api';

// Types
interface WorkflowStep {
  action: string;
  params: Record<string, unknown>;
}

interface Workflow {
  id: string;
  organization_id: string;
  created_by_user_id: string;
  name: string;
  description: string | null;
  trigger_type: 'schedule' | 'event' | 'manual';
  trigger_config: {
    cron?: string;
    event?: string;
    filter?: Record<string, unknown>;
  };
  steps: WorkflowStep[];
  prompt: string | null;  // New: Agent prompt for prompt-based workflows
  auto_approve_tools: string[];  // New: Tools that don't require approval
  is_enabled: boolean;
  last_run_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

interface WorkflowRun {
  id: string;
  workflow_id: string;
  triggered_by: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  steps_completed: Array<{
    step_index: number;
    action: string;
    result: Record<string, unknown>;
  }> | null;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
}

interface WorkflowListResponse {
  workflows: Workflow[];
  total: number;
}

// Fetch workflows for the organization
async function fetchWorkflows(orgId: string): Promise<Workflow[]> {
  const response = await fetch(`${API_BASE}/workflows/${orgId}`);
  if (!response.ok) throw new Error('Failed to fetch workflows');
  const data: WorkflowListResponse = await response.json();
  return data.workflows;
}

// Fetch runs for a workflow
async function fetchWorkflowRuns(orgId: string, workflowId: string): Promise<WorkflowRun[]> {
  const response = await fetch(`${API_BASE}/workflows/${orgId}/${workflowId}/runs?limit=10`);
  if (!response.ok) throw new Error('Failed to fetch workflow runs');
  return response.json();
}

// Trigger a workflow
interface TriggerResponse {
  task_id: string;
  workflow_id: string;
  conversation_id?: string;
}

async function triggerWorkflow(orgId: string, workflowId: string): Promise<TriggerResponse> {
  const response = await fetch(`${API_BASE}/workflows/${orgId}/${workflowId}/trigger`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error('Failed to trigger workflow');
  return response.json();
}

// Delete a workflow
async function deleteWorkflow(orgId: string, workflowId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/workflows/${orgId}/${workflowId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete workflow');
}

// Create a workflow
interface CreateWorkflowParams {
  name: string;
  description?: string;
  prompt: string;
  trigger_type: 'schedule' | 'manual';
  cron?: string;
  auto_approve_tools?: string[];
}

async function createWorkflow(orgId: string, userId: string, params: CreateWorkflowParams): Promise<Workflow> {
  const response = await fetch(`${API_BASE}/workflows/${orgId}?user_id=${userId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: params.name,
      description: params.description ?? null,
      trigger_type: params.trigger_type,
      trigger_config: {
        cron: params.cron ?? null,
      },
      steps: [],
      prompt: params.prompt,
      auto_approve_tools: params.auto_approve_tools ?? [],
      is_enabled: true,
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to create workflow' }));
    throw new Error(error.detail ?? 'Failed to create workflow');
  }
  return response.json();
}

// Update workflow
async function updateWorkflow(orgId: string, workflowId: string, params: CreateWorkflowParams): Promise<Workflow> {
  const response = await fetch(`${API_BASE}/workflows/${orgId}/${workflowId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: params.name,
      description: params.description ?? null,
      trigger_type: params.trigger_type,
      trigger_config: {
        cron: params.cron ?? null,
      },
      prompt: params.prompt,
      auto_approve_tools: params.auto_approve_tools ?? [],
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to update workflow' }));
    throw new Error(error.detail ?? 'Failed to update workflow');
  }
  return response.json();
}

// Toggle workflow enabled state
async function toggleWorkflow(orgId: string, workflowId: string, enabled: boolean): Promise<Workflow> {
  const response = await fetch(`${API_BASE}/workflows/${orgId}/${workflowId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_enabled: enabled }),
  });
  if (!response.ok) throw new Error('Failed to update workflow');
  return response.json();
}

// Format relative time
function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

// Get trigger description
function getTriggerDescription(workflow: Workflow): string {
  if (workflow.trigger_type === 'schedule' && workflow.trigger_config.cron) {
    return `Scheduled: ${workflow.trigger_config.cron}`;
  }
  if (workflow.trigger_type === 'event' && workflow.trigger_config.event) {
    return `On event: ${workflow.trigger_config.event}`;
  }
  return 'Manual trigger';
}

// Get action display name
function getActionDisplayName(action: string): string {
  const names: Record<string, string> = {
    run_query: 'Query Data',
    query: 'Query',
    llm: 'AI Processing',
    send_email: 'Send Email',
    send_system_email: 'System Email',
    send_email_from: 'Send Email (User)',
    send_slack: 'Post to Slack',
    send_system_sms: 'Send SMS',
    sync: 'Sync Data',
  };
  return names[action] ?? action;
}

// Status badge component
function StatusBadge({ status }: { status: WorkflowRun['status'] }): JSX.Element {
  const styles: Record<string, string> = {
    completed: 'bg-green-500/20 text-green-400',
    running: 'bg-blue-500/20 text-blue-400',
    pending: 'bg-yellow-500/20 text-yellow-400',
    failed: 'bg-red-500/20 text-red-400',
    cancelled: 'bg-surface-500/20 text-surface-400',
  };

  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? styles.pending}`}>
      {status}
    </span>
  );
}

// Workflow detail panel
function WorkflowDetail({
  workflow,
  onClose,
  onTrigger,
  onDelete,
  onToggle,
  onEdit,
}: {
  workflow: Workflow;
  onClose: () => void;
  onTrigger: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
  onEdit: () => void;
}): JSX.Element {
  const organization = useAppStore((state) => state.organization);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const { data: runs = [], isLoading: runsLoading } = useQuery({
    queryKey: ['workflow-runs', workflow.id],
    queryFn: () => fetchWorkflowRuns(organization?.id ?? '', workflow.id),
    enabled: !!organization?.id,
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-surface-900 rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-surface-800 flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-surface-100">{workflow.name}</h2>
            {workflow.description && (
              <p className="text-sm text-surface-400 mt-1">{workflow.description}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface-800 text-surface-400 hover:text-surface-200"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Trigger & Status */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-sm text-surface-400">{getTriggerDescription(workflow)}</span>
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                workflow.is_enabled ? 'bg-green-500/20 text-green-400' : 'bg-surface-700 text-surface-400'
              }`}>
                {workflow.is_enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <span className="text-sm text-surface-400">Active</span>
              <button
                onClick={() => onToggle(!workflow.is_enabled)}
                className={`relative w-10 h-6 rounded-full transition-colors ${
                  workflow.is_enabled ? 'bg-primary-600' : 'bg-surface-700'
                }`}
              >
                <span
                  className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                    workflow.is_enabled ? 'left-5' : 'left-1'
                  }`}
                />
              </button>
            </label>
          </div>

          {/* Prompt (new agent-based workflows) */}
          {workflow.prompt && (
            <div>
              <h3 className="text-sm font-medium text-surface-300 mb-3">Instructions</h3>
              <div className="p-4 bg-surface-800/50 rounded-lg">
                <p className="text-sm text-surface-200 whitespace-pre-wrap">{workflow.prompt}</p>
              </div>
            </div>
          )}

          {/* Auto-approved Tools */}
          {workflow.auto_approve_tools && workflow.auto_approve_tools.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-surface-300 mb-3">Auto-approved Tools</h3>
              <div className="flex flex-wrap gap-2">
                {workflow.auto_approve_tools.map((tool) => (
                  <span
                    key={tool}
                    className="px-2 py-1 bg-surface-800 text-surface-300 rounded text-xs font-mono"
                  >
                    {tool}
                  </span>
                ))}
              </div>
              <p className="text-xs text-surface-500 mt-2">
                These tools will run without requiring manual approval.
              </p>
            </div>
          )}

          {/* Steps (legacy workflows only) */}
          {workflow.steps && workflow.steps.length > 0 && !workflow.prompt && (
            <div>
              <h3 className="text-sm font-medium text-surface-300 mb-3">Steps (Legacy)</h3>
              <div className="space-y-2">
                {workflow.steps.map((step, idx) => (
                  <div key={idx} className="flex items-center gap-3 p-3 bg-surface-800/50 rounded-lg">
                    <span className="w-6 h-6 rounded-full bg-primary-600/20 text-primary-400 text-xs font-medium flex items-center justify-center">
                      {idx + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-surface-200">{getActionDisplayName(step.action)}</div>
                      {step.params && Object.keys(step.params).length > 0 && (
                        <div className="text-xs text-surface-500 truncate mt-0.5">
                          {JSON.stringify(step.params).slice(0, 100)}...
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent Runs */}
          <div>
            <h3 className="text-sm font-medium text-surface-300 mb-3">Recent Runs</h3>
            {runsLoading ? (
              <div className="text-sm text-surface-500">Loading...</div>
            ) : runs.length === 0 ? (
              <div className="text-sm text-surface-500">No runs yet</div>
            ) : (
              <div className="space-y-2">
                {runs.map((run) => (
                  <div key={run.id} className="p-3 bg-surface-800/50 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <StatusBadge status={run.status} />
                        <span className="text-xs text-surface-500">{run.triggered_by}</span>
                      </div>
                      <span className="text-xs text-surface-500">
                        {formatRelativeTime(run.started_at)}
                        {run.duration_ms && ` (${(run.duration_ms / 1000).toFixed(1)}s)`}
                      </span>
                    </div>
                    {run.error_message && (
                      <div className="text-xs text-red-400 mt-1">{run.error_message}</div>
                    )}
                    {run.steps_completed && run.steps_completed.length > 0 && (
                      <div className="mt-2 text-xs text-surface-400">
                        {run.steps_completed.length} step{run.steps_completed.length !== 1 ? 's' : ''} completed
                        {run.status === 'completed' && (() => {
                          const lastStep = run.steps_completed?.[run.steps_completed.length - 1];
                          const output = lastStep?.result?.output;
                          if (!output) return null;
                          return (
                            <div className="mt-1 p-2 bg-surface-900 rounded text-surface-300 max-h-24 overflow-y-auto">
                              {String(output).slice(0, 300)}
                            </div>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-surface-800 flex items-center justify-between">
          <div>
            {showDeleteConfirm ? (
              <div className="flex items-center gap-2">
                <span className="text-sm text-red-400">Delete this workflow?</span>
                <button
                  onClick={() => {
                    onDelete();
                    onClose();
                  }}
                  className="px-3 py-1.5 text-sm bg-red-600 hover:bg-red-700 text-white rounded-lg"
                >
                  Yes, delete
                </button>
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className="px-3 py-1.5 text-sm bg-surface-700 hover:bg-surface-600 text-surface-200 rounded-lg"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="text-sm text-red-400 hover:text-red-300"
              >
                Delete workflow
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onEdit}
              className="px-4 py-2 bg-surface-700 hover:bg-surface-600 text-surface-200 rounded-lg text-sm font-medium transition-colors"
            >
              Edit
            </button>
            <button
              onClick={onTrigger}
              disabled={!workflow.is_enabled}
              className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:bg-surface-700 disabled:text-surface-500 text-white rounded-lg text-sm font-medium transition-colors"
            >
              Run Now
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Create Workflow Modal
interface WorkflowModalProps {
  onClose: () => void;
  onSubmit: (params: CreateWorkflowParams) => void;
  isSubmitting: boolean;
  workflow?: Workflow | null; // If provided, modal is in edit mode
}

function WorkflowModal({
  onClose,
  onSubmit,
  isSubmitting,
  workflow,
}: WorkflowModalProps): JSX.Element {
  const isEditMode = !!workflow;
  
  // Initialize state from workflow if editing
  const [name, setName] = useState(workflow?.name ?? '');
  const [description, setDescription] = useState(workflow?.description ?? '');
  const [prompt, setPrompt] = useState(workflow?.prompt ?? '');
  const [triggerType, setTriggerType] = useState<'schedule' | 'manual'>(
    workflow?.trigger_type === 'schedule' ? 'schedule' : 'manual'
  );
  const [cron, setCron] = useState(
    workflow?.trigger_config?.cron ?? '0 9 * * 1-5'
  );
  const [autoApproveTools, setAutoApproveTools] = useState<string[]>(
    workflow?.auto_approve_tools ?? []
  );

  // Tools that can be auto-approved for workflows
  const availableAutoApproveTools = [
    { id: 'send_slack', label: 'Post to Slack', description: 'Send messages to Slack channels' },
    { id: 'send_email_from', label: 'Send Email', description: 'Send emails from your connected account' },
    { id: 'crm_write', label: 'CRM Updates', description: 'Create or update contacts, deals, accounts' },
  ];

  const toggleAutoApproveTool = (toolId: string): void => {
    setAutoApproveTools(prev => 
      prev.includes(toolId) 
        ? prev.filter(t => t !== toolId)
        : [...prev, toolId]
    );
  };

  const handleSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    if (!name.trim() || !prompt.trim()) return;
    
    onSubmit({
      name: name.trim(),
      description: description.trim() || undefined,
      prompt: prompt.trim(),
      trigger_type: triggerType,
      cron: triggerType === 'schedule' ? cron : undefined,
      auto_approve_tools: autoApproveTools.length > 0 ? autoApproveTools : undefined,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-surface-900 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-surface-800 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-surface-100">
            {isEditMode ? 'Edit Workflow' : 'Create Workflow'}
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface-800 text-surface-400 hover:text-surface-200"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-surface-300 mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Daily Pipeline Summary"
              className="w-full px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              required
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-surface-300 mb-1">Description (optional)</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief description of what this workflow does"
              className="w-full px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

          {/* Prompt */}
          <div>
            <label className="block text-sm font-medium text-surface-300 mb-1">Instructions</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="What should the agent do? e.g., Query all deals closing this week and post a summary to #sales on Slack"
              rows={4}
              className="w-full px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none"
              required
            />
          </div>

          {/* Trigger Type */}
          <div>
            <label className="block text-sm font-medium text-surface-300 mb-2">Trigger</label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="triggerType"
                  checked={triggerType === 'manual'}
                  onChange={() => setTriggerType('manual')}
                  className="text-primary-500 focus:ring-primary-500"
                />
                <span className="text-surface-200">Manual only</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="triggerType"
                  checked={triggerType === 'schedule'}
                  onChange={() => setTriggerType('schedule')}
                  className="text-primary-500 focus:ring-primary-500"
                />
                <span className="text-surface-200">Scheduled</span>
              </label>
            </div>
          </div>

          {/* Cron Expression */}
          {triggerType === 'schedule' && (
            <div>
              <label className="block text-sm font-medium text-surface-300 mb-1">Schedule (cron)</label>
              <input
                type="text"
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                placeholder="0 9 * * 1-5"
                className="w-full px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent font-mono text-sm"
              />
              <p className="mt-1 text-xs text-surface-500">
                Examples: <code className="bg-surface-800 px-1 rounded">0 9 * * 1-5</code> (9am weekdays),{' '}
                <code className="bg-surface-800 px-1 rounded">0 */4 * * *</code> (every 4 hours)
              </p>
            </div>
          )}

          {/* Auto-approve Tools */}
          <div>
            <label className="block text-sm font-medium text-surface-300 mb-2">
              Auto-approve Actions
            </label>
            <p className="text-xs text-surface-500 mb-3">
              Select actions this workflow can perform automatically without pausing for approval.
            </p>
            <div className="space-y-2">
              {availableAutoApproveTools.map((tool) => (
                <label
                  key={tool.id}
                  className="flex items-start gap-3 p-3 bg-surface-800/50 rounded-lg cursor-pointer hover:bg-surface-800 transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={autoApproveTools.includes(tool.id)}
                    onChange={() => toggleAutoApproveTool(tool.id)}
                    className="mt-0.5 text-primary-500 focus:ring-primary-500 rounded"
                  />
                  <div>
                    <span className="text-surface-200 font-medium">{tool.label}</span>
                    <p className="text-xs text-surface-500">{tool.description}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>
        </form>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-surface-800 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-surface-300 hover:text-surface-100 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!name.trim() || !prompt.trim() || isSubmitting}
            className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:bg-surface-700 disabled:text-surface-500 text-white rounded-lg text-sm font-medium transition-colors"
          >
            {isSubmitting 
              ? (isEditMode ? 'Saving...' : 'Creating...') 
              : (isEditMode ? 'Save Changes' : 'Create Workflow')
            }
          </button>
        </div>
      </div>
    </div>
  );
}

// Main Workflows component
export function Workflows(): JSX.Element {
  const user = useAppStore((state) => state.user);
  const organization = useAppStore((state) => state.organization);
  const queryClient = useQueryClient();
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editingWorkflow, setEditingWorkflow] = useState<Workflow | null>(null);

  // Fetch workflows
  const { data: workflows = [], isLoading, error, refetch } = useQuery({
    queryKey: ['workflows', organization?.id],
    queryFn: () => fetchWorkflows(organization?.id ?? ''),
    enabled: !!organization?.id,
  });

  // Auto-refresh when navigating to this view or when workflows are modified via chat
  useEffect(() => {
    // Refetch on mount (navigation to this view)
    void refetch();
    
    // Listen for workflow updates from chat/tools
    const handleWorkflowsUpdated = (): void => {
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
    };
    window.addEventListener('workflows-updated', handleWorkflowsUpdated);
    
    return () => {
      window.removeEventListener('workflows-updated', handleWorkflowsUpdated);
    };
  }, [refetch, queryClient]);

  // Filter to user's workflows
  const userWorkflows = workflows.filter((w) => w.created_by_user_id === user?.id);
  const otherWorkflows = workflows.filter((w) => w.created_by_user_id !== user?.id);

  // Navigation
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const setCurrentChatId = useAppStore((state) => state.setCurrentChatId);

  // Mutations
  const triggerMutation = useMutation({
    mutationFn: ({ workflowId }: { workflowId: string }) =>
      triggerWorkflow(organization?.id ?? '', workflowId),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-runs'] });
      
      // If a conversation was created, navigate to it
      if (data.conversation_id) {
        setCurrentChatId(data.conversation_id);
        setCurrentView('chat');
        // Also refresh the conversations list
        void queryClient.invalidateQueries({ queryKey: ['conversations'] });
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ workflowId }: { workflowId: string }) =>
      deleteWorkflow(organization?.id ?? '', workflowId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ workflowId, enabled }: { workflowId: string; enabled: boolean }) =>
      toggleWorkflow(organization?.id ?? '', workflowId, enabled),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const createMutation = useMutation({
    mutationFn: (params: CreateWorkflowParams) =>
      createWorkflow(organization?.id ?? '', user?.id ?? '', params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
      setShowModal(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ workflowId, params }: { workflowId: string; params: CreateWorkflowParams }) =>
      updateWorkflow(organization?.id ?? '', workflowId, params),
    onSuccess: (updatedWorkflow) => {
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
      setShowModal(false);
      setEditingWorkflow(null);
      // Update selectedWorkflow if we were viewing it
      if (selectedWorkflow?.id === updatedWorkflow.id) {
        setSelectedWorkflow(updatedWorkflow);
      }
    },
  });

  const openCreateModal = (): void => {
    setEditingWorkflow(null);
    setShowModal(true);
  };

  const openEditModal = (workflow: Workflow): void => {
    setEditingWorkflow(workflow);
    setShowModal(true);
  };

  const closeModal = (): void => {
    setShowModal(false);
    setEditingWorkflow(null);
  };

  const handleModalSubmit = (params: CreateWorkflowParams): void => {
    if (editingWorkflow) {
      updateMutation.mutate({ workflowId: editingWorkflow.id, params });
    } else {
      createMutation.mutate(params);
    }
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-surface-400">Loading workflows...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-red-400">Failed to load workflows</div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="px-6 py-4 border-b border-surface-800 flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-surface-100">Workflows</h1>
          <p className="text-sm text-surface-400 mt-1">
            Automated tasks that run on schedule or manually
          </p>
        </div>
        <button
          onClick={openCreateModal}
          className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Create Workflow
        </button>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {workflows.length === 0 ? (
          <div className="text-center py-12">
            <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </div>
            <h2 className="text-lg font-medium text-surface-200 mb-2">No workflows yet</h2>
            <p className="text-surface-400 max-w-md mx-auto mb-4">
              Create automated tasks that run on schedule or manually. For example: "Every morning, send me a summary of stale deals to Slack."
            </p>
            <button
              onClick={openCreateModal}
              className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg text-sm font-medium transition-colors"
            >
              Create Your First Workflow
            </button>
          </div>
        ) : (
          <div className="space-y-8">
            {/* My Workflows */}
            {userWorkflows.length > 0 && (
              <div>
                <h2 className="text-sm font-medium text-surface-400 uppercase tracking-wider mb-4">
                  My Workflows
                </h2>
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {userWorkflows.map((workflow) => (
                    <WorkflowCard
                      key={workflow.id}
                      workflow={workflow}
                      onClick={() => setSelectedWorkflow(workflow)}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Team Workflows */}
            {otherWorkflows.length > 0 && (
              <div>
                <h2 className="text-sm font-medium text-surface-400 uppercase tracking-wider mb-4">
                  Team Workflows
                </h2>
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {otherWorkflows.map((workflow) => (
                    <WorkflowCard
                      key={workflow.id}
                      workflow={workflow}
                      onClick={() => setSelectedWorkflow(workflow)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Detail Panel */}
      {selectedWorkflow && (
        <WorkflowDetail
          workflow={selectedWorkflow}
          onClose={() => setSelectedWorkflow(null)}
          onTrigger={() => triggerMutation.mutate({ workflowId: selectedWorkflow.id })}
          onDelete={() => deleteMutation.mutate({ workflowId: selectedWorkflow.id })}
          onToggle={(enabled) =>
            toggleMutation.mutate({ workflowId: selectedWorkflow.id, enabled })
          }
          onEdit={() => openEditModal(selectedWorkflow)}
        />
      )}

      {/* Create/Edit Workflow Modal */}
      {showModal && (
        <WorkflowModal
          onClose={closeModal}
          onSubmit={handleModalSubmit}
          isSubmitting={createMutation.isPending || updateMutation.isPending}
          workflow={editingWorkflow}
        />
      )}
    </div>
  );
}

// Workflow card component
function WorkflowCard({
  workflow,
  onClick,
}: {
  workflow: Workflow;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      onClick={onClick}
      className="text-left p-4 bg-surface-900 border border-surface-800 rounded-xl hover:border-surface-700 transition-colors"
    >
      <div className="flex items-start justify-between mb-2">
        <h3 className="font-medium text-surface-100 truncate flex-1">{workflow.name}</h3>
        <span className={`ml-2 w-2 h-2 rounded-full ${workflow.is_enabled ? 'bg-green-500' : 'bg-surface-600'}`} />
      </div>
      
      <p className="text-xs text-surface-500 mb-3">{getTriggerDescription(workflow)}</p>
      
      <div className="flex items-center gap-1 flex-wrap">
        {workflow.steps.slice(0, 3).map((step, idx) => (
          <span key={idx} className="px-2 py-0.5 bg-surface-800 rounded text-xs text-surface-400">
            {getActionDisplayName(step.action)}
          </span>
        ))}
        {workflow.steps.length > 3 && (
          <span className="text-xs text-surface-500">+{workflow.steps.length - 3} more</span>
        )}
      </div>
      
      {workflow.last_run_at && (
        <div className="mt-3 pt-3 border-t border-surface-800 text-xs text-surface-500">
          Last run: {formatRelativeTime(workflow.last_run_at)}
        </div>
      )}
    </button>
  );
}
