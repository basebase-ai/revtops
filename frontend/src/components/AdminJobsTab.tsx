import { useCallback, useEffect, useMemo, useState } from 'react';
import { API_BASE } from '../lib/api';

interface AdminRunningJob {
  id: string;
  job_type: 'chat' | 'workflow' | 'connector_sync';
  status: string;
  task_name: string;
  started_at: string | null;
  organization_id: string | null;
  organization_name: string | null;
  user_id: string | null;
  user_email: string | null;
  provider: string | null;
  workflow_id: string | null;
}

interface AdminJobsTabProps {
  userId: string;
}

export function AdminJobsTab({ userId }: AdminJobsTabProps): JSX.Element {
  const [jobs, setJobs] = useState<AdminRunningJob[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelingId, setCancelingId] = useState<string | null>(null);

  const fetchJobs = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE}/sync/admin/jobs?user_id=${userId}`);
      if (!response.ok) {
        const payload = await response.json() as { detail?: string };
        throw new Error(payload.detail ?? 'Failed to fetch running jobs');
      }

      const data = await response.json() as { jobs: AdminRunningJob[] };
      setJobs(data.jobs);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch running jobs');
      setJobs([]);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void fetchJobs();
    const interval = setInterval(() => {
      void fetchJobs();
    }, 10000);

    return () => clearInterval(interval);
  }, [fetchJobs]);

  const handleCancel = useCallback(async (job: AdminRunningJob): Promise<void> => {
    setCancelingId(job.id);
    try {
      const response = await fetch(`${API_BASE}/sync/admin/jobs/${job.job_type}/${job.id}/cancel?user_id=${userId}`, {
        method: 'POST',
      });
      if (!response.ok) {
        const payload = await response.json() as { detail?: string };
        throw new Error(payload.detail ?? 'Failed to cancel job');
      }
      await fetchJobs();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to cancel job');
    } finally {
      setCancelingId(null);
    }
  }, [fetchJobs, userId]);

  const sortedJobs = useMemo(
    () => [...jobs].sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? '')),
    [jobs],
  );

  const renderJobBadge = (jobType: AdminRunningJob['job_type']): JSX.Element => {
    const config: Record<AdminRunningJob['job_type'], string> = {
      chat: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
      workflow: 'bg-violet-500/20 text-violet-400 border-violet-500/30',
      connector_sync: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    };

    return (
      <span className={`px-2 py-0.5 rounded-full text-xs border ${config[jobType]}`}>
        {jobType.replace('_', ' ')}
      </span>
    );
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleString();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-surface-100">Running Jobs</h2>
        <button
          onClick={() => void fetchJobs()}
          className="px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 text-sm text-surface-200"
        >
          Refresh
        </button>
      </div>

      {loading && <div className="text-surface-400">Loading jobs…</div>}
      {!loading && error && <div className="text-red-400">{error}</div>}

      {!loading && !error && sortedJobs.length === 0 && (
        <div className="text-surface-400">No running jobs found.</div>
      )}

      {!loading && !error && sortedJobs.length > 0 && (
        <div className="bg-surface-900 rounded-xl border border-surface-800 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-surface-800 text-left">
                <th className="px-4 py-3 text-sm font-medium text-surface-400">Type</th>
                <th className="px-4 py-3 text-sm font-medium text-surface-400">Task</th>
                <th className="px-4 py-3 text-sm font-medium text-surface-400">Organization</th>
                <th className="px-4 py-3 text-sm font-medium text-surface-400">Started</th>
                <th className="px-4 py-3 text-sm font-medium text-surface-400">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-800">
              {sortedJobs.map((job) => (
                <tr key={`${job.job_type}-${job.id}`} className="hover:bg-surface-800/50">
                  <td className="px-4 py-3">{renderJobBadge(job.job_type)}</td>
                  <td className="px-4 py-3">
                    <div className="text-surface-100 text-sm font-mono">{job.id}</div>
                    <div className="text-xs text-surface-500">{job.task_name}</div>
                    {job.provider && <div className="text-xs text-surface-400">Provider: {job.provider}</div>}
                  </td>
                  <td className="px-4 py-3 text-sm text-surface-300">{job.organization_name ?? '—'}</td>
                  <td className="px-4 py-3 text-sm text-surface-400">{formatDate(job.started_at)}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => void handleCancel(job)}
                      disabled={cancelingId === job.id}
                      className="px-3 py-1.5 rounded-lg bg-red-500/80 hover:bg-red-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
                    >
                      {cancelingId === job.id ? 'Canceling…' : 'Cancel'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
