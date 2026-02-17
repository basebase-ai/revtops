import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '../lib/api';
import { useAppStore } from '../store';

const AGENT_GLOBAL_COMMANDS_MAX_LENGTH = 500;
const USER_STORED_COLLAPSE_STATE_KEY = 'memory_user_stored_collapsed';
const WORKFLOW_STORED_COLLAPSE_STATE_KEY = 'memory_workflow_stored_collapsed';

interface StoredMemory {
  id: string;
  entity_type: string;
  category: string | null;
  content: string;
  created_by_user_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface WorkflowNote {
  note_id: string;
  run_id: string;
  workflow_id: string;
  workflow_name: string | null;
  note_index: number;
  content: string;
  created_by_user_id: string | null;
  created_at: string | null;
  run_started_at: string | null;
}

interface MemoryDashboardResponse {
  memories: StoredMemory[];
  workflow_notes: WorkflowNote[];
}

function formatTime(value: string | null): string {
  if (!value) return 'Unknown time';
  return new Date(value).toLocaleString();
}

export function Memories(): JSX.Element {
  const { organization, user, setUser } = useAppStore();
  const queryClient = useQueryClient();
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [memoryDraft, setMemoryDraft] = useState<string>('');
  const [agentGlobalCommands, setAgentGlobalCommands] = useState<string>('');
  const [commandsError, setCommandsError] = useState<string | null>(null);
  const [isUserStoredExpanded, setIsUserStoredExpanded] = useState<boolean>(() => {
    return localStorage.getItem(USER_STORED_COLLAPSE_STATE_KEY) !== 'true';
  });
  const [isWorkflowStoredExpanded, setIsWorkflowStoredExpanded] = useState<boolean>(() => {
    return localStorage.getItem(WORKFLOW_STORED_COLLAPSE_STATE_KEY) !== 'true';
  });

  useEffect(() => {
    setAgentGlobalCommands(user?.agentGlobalCommands ?? '');
  }, [user?.agentGlobalCommands]);

  useEffect(() => {
    localStorage.setItem(USER_STORED_COLLAPSE_STATE_KEY, String(!isUserStoredExpanded));
  }, [isUserStoredExpanded]);

  useEffect(() => {
    localStorage.setItem(WORKFLOW_STORED_COLLAPSE_STATE_KEY, String(!isWorkflowStoredExpanded));
  }, [isWorkflowStoredExpanded]);

  const orgId = organization?.id;
  const userId = user?.id;
  const queryKey = useMemo(() => ['memory-dashboard', orgId, userId], [orgId, userId]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    enabled: !!orgId && !!userId,
    queryFn: async () => {
      const { data, error } = await apiRequest<MemoryDashboardResponse>(`/memories/${orgId}?user_id=${userId}`);
      if (error || !data) throw new Error(error ?? 'Failed to load memories');
      return data;
    },
  });

  const updateMemory = useMutation({
    mutationFn: async ({ memoryId, content }: { memoryId: string; content: string }) => {
      const { error } = await apiRequest(`/memories/${orgId}/user/${memoryId}?user_id=${userId}`, {
        method: 'PATCH',
        body: JSON.stringify({ content }),
      });
      if (error) throw new Error(error);
    },
    onSuccess: () => {
      setEditingMemoryId(null);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const deleteMemory = useMutation({
    mutationFn: async (memoryId: string) => {
      const { error } = await apiRequest(`/memories/${orgId}/user/${memoryId}?user_id=${userId}`, {
        method: 'DELETE',
      });
      if (error) throw new Error(error);
    },
    onSuccess: () => {
      setEditingMemoryId(null);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const deleteWorkflowNote = useMutation({
    mutationFn: async ({ runId, noteIndex }: { runId: string; noteIndex: number }) => {
      const { error } = await apiRequest(`/memories/${orgId}/workflow-notes/${runId}/${noteIndex}?user_id=${userId}`, {
        method: 'DELETE',
      });
      if (error) throw new Error(error);
    },
    onSuccess: () => {
      setEditingMemoryId(null);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const saveGlobalCommands = useMutation({
    mutationFn: async () => {
      if (!user) throw new Error('User is required');

      const trimmedCommands = agentGlobalCommands.trim();
      if (trimmedCommands.length > AGENT_GLOBAL_COMMANDS_MAX_LENGTH) {
        throw new Error(`Agent global commands must be ${AGENT_GLOBAL_COMMANDS_MAX_LENGTH} characters or less.`);
      }

      const { data, error } = await apiRequest<{
        name: string | null;
        avatar_url: string | null;
        agent_global_commands: string | null;
        phone_number: string | null;
        job_title: string | null;
      }>(`/auth/me?user_id=${user.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: user.name,
          avatar_url: user.avatarUrl,
          agent_global_commands: trimmedCommands || null,
          phone_number: user.phoneNumber,
          job_title: user.jobTitle,
        }),
      });

      if (error || !data) throw new Error(error ?? 'Failed to update global commands');
      return data;
    },
    onSuccess: (updatedUser) => {
      if (!user) return;
      setCommandsError(null);
      setUser({
        ...user,
        name: updatedUser.name,
        avatarUrl: updatedUser.avatar_url,
        agentGlobalCommands: updatedUser.agent_global_commands,
        phoneNumber: updatedUser.phone_number,
        jobTitle: updatedUser.job_title,
      });
    },
    onError: (err) => {
      setCommandsError(err instanceof Error ? err.message : 'Failed to update global commands');
    },
  });

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <h1 className="text-xl md:text-2xl font-bold text-surface-50">Memory</h1>
        <p className="text-surface-400 mt-1 text-sm md:text-base">A shared place to manage workflow notes and user-saved memories.</p>
      </header>

      <div className="flex-1 overflow-y-auto px-4 md:px-8 py-4 md:py-6 space-y-6">
        {isLoading && <div className="text-surface-400">Loading memory data...</div>}
        {isError && <div className="text-red-400">{error instanceof Error ? error.message : 'Failed to load memory data'}</div>}

        {!isLoading && !isError && (
          <>
            <section className="rounded-xl border border-surface-800 bg-surface-900/40 p-4 md:p-6">
              <div className="flex items-center justify-between gap-4">
                <h2 className="text-lg font-semibold text-surface-100">Global commands</h2>
                <button
                  className="px-3 py-1.5 text-xs rounded-md bg-primary-600 hover:bg-primary-700 text-white disabled:opacity-60"
                  disabled={saveGlobalCommands.isPending}
                  onClick={() => saveGlobalCommands.mutate()}
                >
                  {saveGlobalCommands.isPending ? 'Saving...' : 'Save'}
                </button>
              </div>
              <p className="text-xs text-surface-500 mt-2">Persistent instructions included with every agent prompt.</p>
              <textarea
                value={agentGlobalCommands}
                onChange={(e) => setAgentGlobalCommands(e.target.value)}
                placeholder="Add global command guidance for the agent"
                className="input-field min-h-28 mt-3"
                maxLength={AGENT_GLOBAL_COMMANDS_MAX_LENGTH}
              />
              <div className="flex items-center justify-between mt-1">
                <p className="text-xs text-surface-500">{agentGlobalCommands.length}/{AGENT_GLOBAL_COMMANDS_MAX_LENGTH}</p>
                {commandsError && <p className="text-xs text-red-400">{commandsError}</p>}
              </div>
            </section>

            <section className="rounded-xl border border-surface-800 bg-surface-900/40 p-4 md:p-6">
              <div className="flex items-center justify-between gap-4">
                <button
                  className="flex items-center gap-2 text-left"
                  onClick={() => setIsUserStoredExpanded((value) => !value)}
                  aria-expanded={isUserStoredExpanded}
                >
                  <span className="text-surface-400 text-xs leading-none">{isUserStoredExpanded ? '▾' : '▸'}</span>
                  <h2 className="text-lg font-semibold text-surface-100">User stored</h2>
                </button>
                <span className="text-xs text-surface-500">Editable + deletable</span>
              </div>
              {isUserStoredExpanded && <div className="mt-4 space-y-3">
                {data?.memories.length ? data.memories.map((memory) => (
                  <div key={memory.id} className="rounded-lg border border-surface-800 bg-surface-900 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="text-xs text-surface-500 mb-2">
                          {memory.category ?? 'uncategorized'} • Updated {formatTime(memory.updated_at)}
                        </div>
                        {editingMemoryId === memory.id ? (
                          <textarea
                            className="w-full min-h-20 rounded-lg bg-surface-800 border border-surface-700 px-3 py-2 text-sm text-surface-100"
                            value={memoryDraft}
                            onChange={(e) => setMemoryDraft(e.target.value)}
                          />
                        ) : (
                          <p className="text-sm text-surface-200 whitespace-pre-wrap">{memory.content}</p>
                        )}
                      </div>
                      <div className="flex gap-2">
                        {editingMemoryId === memory.id ? (
                          <>
                            <button className="px-3 py-1.5 text-xs rounded-md bg-primary-600 hover:bg-primary-700 text-white" onClick={() => updateMemory.mutate({ memoryId: memory.id, content: memoryDraft })}>Save</button>
                            <button className="px-3 py-1.5 text-xs rounded-md bg-surface-800 hover:bg-surface-700 text-surface-200" onClick={() => setEditingMemoryId(null)}>Cancel</button>
                          </>
                        ) : (
                          <button className="px-3 py-1.5 text-xs rounded-md bg-surface-800 hover:bg-surface-700 text-surface-200" onClick={() => { setEditingMemoryId(memory.id); setMemoryDraft(memory.content); }}>Edit</button>
                        )}
                        <button className="px-3 py-1.5 text-xs rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300" onClick={() => deleteMemory.mutate(memory.id)}>Delete</button>
                      </div>
                    </div>
                  </div>
                )) : <p className="text-sm text-surface-500">No user memories found yet.</p>}
              </div>}
            </section>

            <section className="rounded-xl border border-surface-800 bg-surface-900/40 p-4 md:p-6">
              <div className="flex items-center justify-between gap-4">
                <button
                  className="flex items-center gap-2 text-left"
                  onClick={() => setIsWorkflowStoredExpanded((value) => !value)}
                  aria-expanded={isWorkflowStoredExpanded}
                >
                  <span className="text-surface-400 text-xs leading-none">{isWorkflowStoredExpanded ? '▾' : '▸'}</span>
                  <h2 className="text-lg font-semibold text-surface-100">Workflow stored</h2>
                </button>
                <span className="text-xs text-surface-500">Deletable</span>
              </div>
              {isWorkflowStoredExpanded && <div className="mt-4 space-y-3">
                {data?.workflow_notes.length ? data.workflow_notes.map((note) => (
                  <div key={note.note_id} className="rounded-lg border border-surface-800 bg-surface-900 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="text-xs text-surface-500 mb-2">
                          {note.workflow_name ?? 'Workflow'} • Run {formatTime(note.run_started_at)}
                        </div>
                        <p className="text-sm text-surface-200 whitespace-pre-wrap">{note.content}</p>
                      </div>
                      <button className="px-3 py-1.5 text-xs rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300" onClick={() => deleteWorkflowNote.mutate({ runId: note.run_id, noteIndex: note.note_index })}>Delete</button>
                    </div>
                  </div>
                )) : <p className="text-sm text-surface-500">No workflow notes saved yet.</p>}
              </div>}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
