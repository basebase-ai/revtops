import { useEffect, useMemo, useState } from 'react';
import { apiRequest } from '../lib/api';
import type { OrganizationInfo, UserProfile } from './AppLayout';

const AGENT_GLOBAL_COMMANDS_MAX_LENGTH = 500;

interface UserMemory {
  id: string;
  content: string;
  updated_at: string | null;
}

interface WorkflowNote {
  note_id: string;
  run_id: string;
  workflow_name: string;
  note_index: number;
  content: string;
  created_at: string | null;
}

interface MemoriesResponse {
  agent_global_commands: string | null;
  user_memories: UserMemory[];
  workflow_notes: WorkflowNote[];
}

interface MemoriesProps {
  user: UserProfile;
  organization: OrganizationInfo;
  onUpdateUser: (updates: Partial<UserProfile>) => void;
}

export function Memories({ user, organization, onUpdateUser }: MemoriesProps): JSX.Element {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<MemoriesResponse | null>(null);
  const [globalCommands, setGlobalCommands] = useState(user.agentGlobalCommands ?? '');
  const [isSavingCommands, setIsSavingCommands] = useState(false);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingMemoryContent, setEditingMemoryContent] = useState('');
  const [showUserStored, setShowUserStored] = useState(true);
  const [showWorkflowStored, setShowWorkflowStored] = useState(true);

  const fetchData = async (): Promise<void> => {
    setLoading(true);
    setError(null);
    const { data: response, error: requestError } = await apiRequest<MemoriesResponse>(
      `/memories/${organization.id}?user_id=${user.id}`,
    );
    if (requestError || !response) {
      setError(requestError ?? 'Failed to load memories');
      setLoading(false);
      return;
    }
    setData(response);
    setGlobalCommands(response.agent_global_commands ?? user.agentGlobalCommands ?? '');
    setLoading(false);
  };

  useEffect(() => {
    void fetchData();
  }, [organization.id, user.id]);

  const sortedUserMemories = useMemo(() => data?.user_memories ?? [], [data]);
  const workflowNotes = useMemo(() => data?.workflow_notes ?? [], [data]);

  const saveGlobalCommands = async (): Promise<void> => {
    const trimmed = globalCommands.trim();
    if (trimmed.length > AGENT_GLOBAL_COMMANDS_MAX_LENGTH) {
      setError(`Agent global commands must be ${AGENT_GLOBAL_COMMANDS_MAX_LENGTH} characters or less.`);
      return;
    }

    setIsSavingCommands(true);
    setError(null);
    const { data: updated, error: requestError } = await apiRequest<{ agent_global_commands: string | null }>(
      `/auth/me?user_id=${user.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ agent_global_commands: trimmed || null }),
      },
    );

    if (requestError || !updated) {
      setError(requestError ?? 'Failed to save global commands');
      setIsSavingCommands(false);
      return;
    }

    onUpdateUser({ agentGlobalCommands: updated.agent_global_commands });
    setIsSavingCommands(false);
  };

  const handleDeleteMemory = async (memoryId: string): Promise<void> => {
    const { error: requestError } = await apiRequest(`/memories/${organization.id}/${memoryId}?user_id=${user.id}`, {
      method: 'DELETE',
    });
    if (requestError) {
      setError(requestError);
      return;
    }
    await fetchData();
  };

  const startEditing = (memory: UserMemory): void => {
    setEditingMemoryId(memory.id);
    setEditingMemoryContent(memory.content);
  };

  const saveEditedMemory = async (): Promise<void> => {
    if (!editingMemoryId || !editingMemoryContent.trim()) return;
    const { error: requestError } = await apiRequest(
      `/memories/${organization.id}/${editingMemoryId}?user_id=${user.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ content: editingMemoryContent.trim() }),
      },
    );
    if (requestError) {
      setError(requestError);
      return;
    }
    setEditingMemoryId(null);
    setEditingMemoryContent('');
    await fetchData();
  };

  const handleDeleteWorkflowNote = async (note: WorkflowNote): Promise<void> => {
    const { error: requestError } = await apiRequest(
      `/memories/${organization.id}/workflow-notes/${note.run_id}/${note.note_index}?user_id=${user.id}`,
      { method: 'DELETE' },
    );
    if (requestError) {
      setError(requestError);
      return;
    }
    await fetchData();
  };

  if (loading) return <div className="p-6 text-surface-400">Loading memories...</div>;

  return (
    <div className="h-full overflow-y-auto p-6 space-y-5">
      <h1 className="text-2xl font-semibold text-surface-100">Memories</h1>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <section className="card p-4 space-y-3">
        <h2 className="text-sm font-medium text-surface-200">Global commands</h2>
        <textarea
          value={globalCommands}
          onChange={(e) => setGlobalCommands(e.target.value)}
          className="input-field min-h-28"
          maxLength={AGENT_GLOBAL_COMMANDS_MAX_LENGTH}
          placeholder="Persistent instructions included with prompts"
        />
        <div className="flex items-center justify-between">
          <p className="text-xs text-surface-500">{globalCommands.length}/{AGENT_GLOBAL_COMMANDS_MAX_LENGTH}</p>
          <button onClick={() => void saveGlobalCommands()} disabled={isSavingCommands} className="btn-primary disabled:opacity-50">
            {isSavingCommands ? 'Saving...' : 'Save global commands'}
          </button>
        </div>
      </section>

      <section className="card p-4">
        <button onClick={() => setShowUserStored((prev) => !prev)} className="w-full flex items-center justify-between text-left">
          <h2 className="text-sm font-medium text-surface-200">User stored ({sortedUserMemories.length})</h2>
          <span className="text-surface-500">{showUserStored ? '−' : '+'}</span>
        </button>
        {showUserStored && (
          <div className="mt-3 space-y-2">
            {sortedUserMemories.length === 0 && <p className="text-sm text-surface-500">No user memories yet.</p>}
            {sortedUserMemories.map((memory) => (
              <div key={memory.id} className="rounded-lg border border-surface-800 p-3">
                {editingMemoryId === memory.id ? (
                  <div className="space-y-2">
                    <textarea value={editingMemoryContent} onChange={(e) => setEditingMemoryContent(e.target.value)} className="input-field min-h-20" />
                    <div className="flex gap-2 justify-end">
                      <button className="btn-secondary" onClick={() => setEditingMemoryId(null)}>Cancel</button>
                      <button className="btn-primary" onClick={() => void saveEditedMemory()}>Save</button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="text-sm text-surface-200 whitespace-pre-wrap">{memory.content}</p>
                    <div className="mt-2 flex justify-end gap-2">
                      <button className="btn-secondary" onClick={() => startEditing(memory)}>Edit</button>
                      <button className="px-3 py-1.5 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20" onClick={() => void handleDeleteMemory(memory.id)}>Delete</button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card p-4">
        <button onClick={() => setShowWorkflowStored((prev) => !prev)} className="w-full flex items-center justify-between text-left">
          <h2 className="text-sm font-medium text-surface-200">Workflow stored ({workflowNotes.length})</h2>
          <span className="text-surface-500">{showWorkflowStored ? '−' : '+'}</span>
        </button>
        {showWorkflowStored && (
          <div className="mt-3 space-y-2">
            {workflowNotes.length === 0 && <p className="text-sm text-surface-500">No workflow notes yet.</p>}
            {workflowNotes.map((note) => (
              <div key={note.note_id} className="rounded-lg border border-surface-800 p-3">
                <p className="text-xs text-surface-500 mb-1">{note.workflow_name}</p>
                <p className="text-sm text-surface-200 whitespace-pre-wrap">{note.content}</p>
                <div className="mt-2 flex justify-end">
                  <button className="px-3 py-1.5 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20" onClick={() => void handleDeleteWorkflowNote(note)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
