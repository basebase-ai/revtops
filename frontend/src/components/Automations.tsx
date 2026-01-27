/**
 * Automations page - view and manage workflow automations.
 * 
 * Features:
 * - List user's workflows with status
 * - View workflow steps/recipe
 * - See last run time and results
 * - Manually trigger workflows
 * - Delete workflows
 */

import { useState } from 'react';
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
async function triggerWorkflow(orgId: string, workflowId: string): Promise<{ task_id: string }> {
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
}: {
  workflow: Workflow;
  onClose: () => void;
  onTrigger: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
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

          {/* Steps */}
          <div>
            <h3 className="text-sm font-medium text-surface-300 mb-3">Steps</h3>
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
  );
}

// Main Automations component
export function Automations(): JSX.Element {
  const user = useAppStore((state) => state.user);
  const organization = useAppStore((state) => state.organization);
  const queryClient = useQueryClient();
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);

  // Fetch workflows
  const { data: workflows = [], isLoading, error } = useQuery({
    queryKey: ['workflows', organization?.id],
    queryFn: () => fetchWorkflows(organization?.id ?? ''),
    enabled: !!organization?.id,
  });

  // Filter to user's workflows
  const userWorkflows = workflows.filter((w) => w.created_by_user_id === user?.id);
  const otherWorkflows = workflows.filter((w) => w.created_by_user_id !== user?.id);

  // Mutations
  const triggerMutation = useMutation({
    mutationFn: ({ workflowId }: { workflowId: string }) =>
      triggerWorkflow(organization?.id ?? '', workflowId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-runs'] });
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

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-surface-400">Loading automations...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-red-400">Failed to load automations</div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="px-6 py-4 border-b border-surface-800">
        <h1 className="text-xl font-semibold text-surface-100">Automations</h1>
        <p className="text-sm text-surface-400 mt-1">
          Workflows created by you and your team through conversations with Revtops
        </p>
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
            <h2 className="text-lg font-medium text-surface-200 mb-2">No automations yet</h2>
            <p className="text-surface-400 max-w-md mx-auto">
              Ask your Revtops agent to create an automation. For example: "Every morning, send me a summary of stale deals to Slack."
            </p>
          </div>
        ) : (
          <div className="space-y-8">
            {/* My Automations */}
            {userWorkflows.length > 0 && (
              <div>
                <h2 className="text-sm font-medium text-surface-400 uppercase tracking-wider mb-4">
                  My Automations
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

            {/* Team Automations */}
            {otherWorkflows.length > 0 && (
              <div>
                <h2 className="text-sm font-medium text-surface-400 uppercase tracking-wider mb-4">
                  Team Automations
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
