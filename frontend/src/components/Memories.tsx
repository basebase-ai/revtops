import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '../lib/api';
import { useAppStore } from '../store';

interface StoredMemory {
  id: string;
  entity_type: string;
  category: string | null;
  content: string;
  created_by_user_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface MemoryDashboardResponse {
  memories: StoredMemory[];
}

const GLOBAL_COMMAND_CATEGORY = 'global_commands';
const GLOBAL_COMMAND_MAX_LENGTH = 800;

function formatTime(value: string | null): string {
  if (!value) return 'Unknown time';
  return new Date(value).toLocaleString();
}

export function Memories(): JSX.Element {
  const { organization, user } = useAppStore();
  const queryClient = useQueryClient();
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [memoryDraft, setMemoryDraft] = useState<string>('');
  const [newMemoryContent, setNewMemoryContent] = useState<string>('');
  const [globalCommandDraft, setGlobalCommandDraft] = useState<string>('');
  const [showAddForm, setShowAddForm] = useState<boolean>(false);
  const [isGlobalCommandDirty, setIsGlobalCommandDirty] = useState<boolean>(false);
  const globalCommandSaveTimeoutRef = useRef<number | null>(null);
  const lastSavedGlobalCommandRef = useRef<string>('');
  const globalCommandTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  const orgId = organization?.id;
  const userId = user?.id;
  const queryKey = useMemo(() => ['memory-dashboard', orgId, userId], [orgId, userId]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    enabled: !!orgId && !!userId,
    queryFn: async () => {
      const { data: res, error: err } = await apiRequest<MemoryDashboardResponse>(`/memories/${orgId}?user_id=${userId}`);
      if (err || !res) throw new Error(err ?? 'Failed to load memories');
      return res;
    },
  });

  const memories: StoredMemory[] = data?.memories ?? [];
  const globalCommandMemory = memories.find((memory) => memory.category === GLOBAL_COMMAND_CATEGORY) ?? null;
  const otherMemories = memories.filter((memory) => memory.category !== GLOBAL_COMMAND_CATEGORY);

  useEffect(() => {
    const serverValue = globalCommandMemory?.content ?? '';
    setGlobalCommandDraft(serverValue);
    setIsGlobalCommandDirty(false);
    lastSavedGlobalCommandRef.current = serverValue;
  }, [globalCommandMemory?.id, globalCommandMemory?.content]);

  useEffect(() => {
    const textarea = globalCommandTextareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${textarea.scrollHeight}px`;
  }, [globalCommandDraft]);

  const updateMemory = useMutation({
    mutationFn: async ({ memoryId, content }: { memoryId: string; content: string }) => {
      const { error: err } = await apiRequest(`/memories/${orgId}/user/${memoryId}?user_id=${userId}`, {
        method: 'PATCH',
        body: JSON.stringify({ content }),
      });
      if (err) throw new Error(err);
    },
    onSuccess: () => {
      setEditingMemoryId(null);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const deleteMemory = useMutation({
    mutationFn: async (memoryId: string) => {
      const { error: err } = await apiRequest(`/memories/${orgId}/user/${memoryId}?user_id=${userId}`, {
        method: 'DELETE',
      });
      if (err) throw new Error(err);
    },
    onSuccess: () => {
      setEditingMemoryId(null);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const createMemory = useMutation({
    mutationFn: async ({ content, category }: { content: string; category?: string }) => {
      const { error: err } = await apiRequest(`/memories/${orgId}/user?user_id=${userId}`, {
        method: 'POST',
        body: JSON.stringify({ content: content.trim(), category }),
      });
      if (err) throw new Error(err);
    },
    onSuccess: () => {
      setNewMemoryContent('');
      setShowAddForm(false);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const saveGlobalCommand = useMutation({
    mutationFn: async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed || trimmed.length > GLOBAL_COMMAND_MAX_LENGTH) return;

      if (globalCommandMemory) {
        const { error: err } = await apiRequest(`/memories/${orgId}/user/${globalCommandMemory.id}?user_id=${userId}`, {
          method: 'PATCH',
          body: JSON.stringify({ content: trimmed }),
        });
        if (err) throw new Error(err);
        return;
      }

      const { error: err } = await apiRequest(`/memories/${orgId}/user?user_id=${userId}`, {
        method: 'POST',
        body: JSON.stringify({ content: trimmed, category: GLOBAL_COMMAND_CATEGORY }),
      });
      if (err) throw new Error(err);
    },
    onSuccess: (_data, savedContent) => {
      const trimmed = savedContent.trim();
      lastSavedGlobalCommandRef.current = trimmed;
      setIsGlobalCommandDirty(false);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  useEffect(() => {
    if (!isGlobalCommandDirty) return;

    if (globalCommandSaveTimeoutRef.current) {
      window.clearTimeout(globalCommandSaveTimeoutRef.current);
    }

    globalCommandSaveTimeoutRef.current = window.setTimeout(() => {
      const trimmed = globalCommandDraft.trim();
      if (!trimmed || trimmed.length > GLOBAL_COMMAND_MAX_LENGTH) return;
      if (trimmed === lastSavedGlobalCommandRef.current) {
        setIsGlobalCommandDirty(false);
        return;
      }
      saveGlobalCommand.mutate(globalCommandDraft);
    }, 700);

    return () => {
      if (globalCommandSaveTimeoutRef.current) {
        window.clearTimeout(globalCommandSaveTimeoutRef.current);
      }
    };
  }, [globalCommandDraft, isGlobalCommandDirty, saveGlobalCommand]);

  const handleAddMemory = (): void => {
    const trimmed = newMemoryContent.trim();
    if (!trimmed) return;
    createMemory.mutate({ content: trimmed });
  };

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {isLoading && <div className="text-surface-400 text-sm">Loading memories...</div>}
        {isError && <div className="text-red-400 text-sm">{error instanceof Error ? error.message : 'Failed to load memories'}</div>}

        {!isLoading && !isError && (
          <>
            <div className="rounded-lg border border-primary-700/40 bg-primary-950/20 p-3">
              <div className="text-xs uppercase tracking-wide text-primary-300 mb-2">Global command</div>
              <p className="text-xs text-surface-400 mb-2">Applied on every message. Maximum 800 characters.</p>
              <textarea
                ref={globalCommandTextareaRef}
                className="w-full min-h-20 overflow-hidden rounded-lg bg-surface-800 border border-surface-700 px-3 py-2 text-sm text-surface-100"
                value={globalCommandDraft}
                onChange={(e) => {
                  setGlobalCommandDraft(e.target.value);
                  setIsGlobalCommandDirty(true);
                }}
                placeholder="e.g. Always start with a short summary before details"
                maxLength={GLOBAL_COMMAND_MAX_LENGTH}
              />
              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="text-xs text-surface-500">
                  {globalCommandDraft.length}/{GLOBAL_COMMAND_MAX_LENGTH}
                </span>
                <div className="flex items-center gap-2">
                  {saveGlobalCommand.isPending && <span className="text-xs text-surface-500">Saving...</span>}
                  {globalCommandMemory && (
                    <button
                      type="button"
                      className="px-3 py-1.5 text-xs rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300"
                      onClick={() => deleteMemory.mutate(globalCommandMemory.id)}
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            </div>

            <div className="flex justify-end">
              {!showAddForm && (
                <button
                  type="button"
                  className="px-3 py-1.5 text-xs rounded-md bg-primary-600 hover:bg-primary-700 text-white"
                  onClick={() => setShowAddForm(true)}
                >
                  Add memory
                </button>
              )}
            </div>

            {showAddForm && (
              <div className="rounded-lg border border-surface-800 bg-surface-900 p-3 mb-4">
                <textarea
                  className="w-full min-h-20 rounded-lg bg-surface-800 border border-surface-700 px-3 py-2 text-sm text-surface-100"
                  value={newMemoryContent}
                  onChange={(e) => setNewMemoryContent(e.target.value)}
                  placeholder="e.g. I prefer morning meetings before 10am"
                  autoFocus
                />
                <div className="flex justify-end gap-2 mt-2">
                  <button
                    type="button"
                    className="px-3 py-1.5 text-xs rounded-md bg-surface-800 hover:bg-surface-700 text-surface-200"
                    onClick={() => { setShowAddForm(false); setNewMemoryContent(''); }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="px-3 py-1.5 text-xs rounded-md bg-primary-600 hover:bg-primary-700 text-white disabled:opacity-60"
                    disabled={!newMemoryContent.trim() || createMemory.isPending}
                    onClick={handleAddMemory}
                  >
                    {createMemory.isPending ? 'Adding...' : 'Add'}
                  </button>
                </div>
              </div>
            )}

            <div className="space-y-3">
              {otherMemories.length ? otherMemories.map((memory) => (
                <div key={memory.id} className="rounded-lg border border-surface-800 bg-surface-900 p-3">
                  <div className="flex flex-col gap-3">
                    <div className="w-full">
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
                    <div className="flex flex-wrap justify-end gap-2">
                      {editingMemoryId === memory.id ? (
                        <>
                          <button
                            type="button"
                            className="px-3 py-1.5 text-xs rounded-md bg-primary-600 hover:bg-primary-700 text-white"
                            onClick={() => updateMemory.mutate({ memoryId: memory.id, content: memoryDraft })}
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="px-3 py-1.5 text-xs rounded-md bg-surface-800 hover:bg-surface-700 text-surface-200"
                            onClick={() => setEditingMemoryId(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="px-3 py-1.5 text-xs rounded-md bg-surface-800 hover:bg-surface-700 text-surface-200"
                          onClick={() => { setEditingMemoryId(memory.id); setMemoryDraft(memory.content); }}
                        >
                          Edit
                        </button>
                      )}
                      <button
                        type="button"
                        className="px-3 py-1.5 text-xs rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300"
                        onClick={() => deleteMemory.mutate(memory.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </div>
              )) : !showAddForm && <p className="text-sm text-surface-500">No memories yet. Add one above or ask the agent to remember something.</p>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
