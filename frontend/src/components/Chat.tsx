/**
 * Chat interface component.
 *
 * Features:
 * - Uses global WebSocket from AppLayout for persistent connections
 * - Per-conversation state (messages, streaming) from Zustand
 * - Background tasks continue even when switching chats
 * - Streaming response display with "thinking" indicator
 * - Artifact viewer for dashboards/reports
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { ArtifactViewer, type FileArtifact } from './ArtifactViewer';
import { ArtifactTile } from './ArtifactTile';
import { AppTile } from './apps/AppTile';
import { AppPreviewPanel } from './apps/AppPreviewPanel';
import { Avatar } from './Avatar';
import { PendingApprovalCard, type ApprovalResult } from './PendingApprovalCard';
import { ScopeLockIcon } from './ScopeVisibilityIcons';
import { getConversation, updateConversation, uploadChatFile, type UploadResponse } from '../api/client';
import { useIsMobile } from '../hooks';
import { useTeamMembers, type TeamMember } from '../hooks/useOrganization';
import { API_BASE, apiRequest, getAuthenticatedRequestHeaders } from '../lib/api';

import { crossTab } from '../lib/crossTab';
import { APP_NAME, LOGO_PATH } from '../lib/brand';
import {
  useAppStore,
  useChatStore,
  useConversationState,
  useActiveTasksByConversation,
  useConnectedIntegrations,
  type AppBlock,
  type ChatMessage,
  type ConversationSummaryData,
  type Integration,
  type ThinkingBlock as ThinkingBlockType,
  type ToolCallData,
  type ToolUseBlock,
  type ErrorBlock,
  type AttachmentBlock,
  type TypingUserEntry,
} from '../store';

// Legacy data artifact format
interface LegacyArtifact {
  id: string;
  type: string;
  title: string;
  data: Record<string, unknown>;
}

// Union type for all artifact formats
type AnyArtifact = LegacyArtifact | FileArtifact;

const ALLOWED_DROP_EXTENSIONS: readonly string[] = [
  '.pdf', '.csv', '.tsv', '.xlsx', '.docx', '.pptx',
  '.txt', '.json', '.md', '.xml', '.html', '.css',
  '.yaml', '.yml', '.rtf', '.eml', '.ics', '.vcf',
  '.sql', '.log', '.py', '.js', '.ts', '.jsx', '.tsx',
  '.sh', '.rb', '.java', '.c', '.cpp', '.h', '.go',
  '.rs', '.swift', '.kt', '.r', '.m',
] as const;

interface ChatProps {
  userId?: string | null;
  organizationId: string;
  chatId?: string | null;
  sendMessage: (data: Record<string, unknown>) => void;
  isConnected: boolean;
  connectionState: 'connecting' | 'connected' | 'disconnected' | 'error';
  crmApprovalResults: Map<string, unknown>;
  /** Called when the current conversation ID returns 404 (e.g. deleted or wrong org). Clears selection. */
  onConversationNotFound?: () => void;
  /** Credits remaining and total included for the org. Null if billing not loaded. */
  creditsInfo?: { balance: number; included: number } | null;
}

// Tool approval result type (received via parent component)
interface WsToolApprovalResult {
  type: 'tool_approval_result';
  operation_id: string;
  tool_name: string;
  status: string;
  message?: string;
  success_count?: number;
  failure_count?: number;
  skipped_count?: number;
  error?: string;
}

// Tool approval state tracking (generic for all tools)
interface ToolApprovalState {
  operationId: string;
  toolName: string;
  isProcessing: boolean;
  result: WsToolApprovalResult | null;
}

/**
 * Slack-like message thread typography & layout.
 * NOTE: :root light mode uses an inverted surface scale (see index.css): low numbers = dark ink,
 * high numbers = light fills. Never use text-surface-900 for body text in light mode — it is ~white.
 */
const AGENT_AVATAR_PATH: string = '/basebase_logo_reverse-256.png';
const CHAT_MSG_ROW: string =
  'group/msg flex items-start gap-3 px-5 -mx-5 hover:bg-black/[0.035] dark:hover:bg-surface-800/40 transition-colors';
const CHAT_MSG_AVATAR: string = 'flex-shrink-0 !w-9 !h-9 !rounded-md mt-px';
const CHAT_MSG_AVATAR_SPACER: string = 'w-9 flex-shrink-0 mt-px';
const CHAT_MSG_NAME: string =
  'text-[15px] font-extrabold leading-tight text-surface-50 dark:text-surface-50 tracking-[-0.015em]';
const CHAT_MSG_TIME: string =
  'text-[10px] tabular-nums font-normal text-surface-500 dark:text-surface-500 leading-none';
const CHAT_MSG_BODY: string =
  'text-[15px] leading-[1.466] text-surface-100 dark:text-surface-200 whitespace-pre-wrap break-words';

function isSlackIdentitySource(source: string): boolean {
  return source.toLowerCase().includes('slack');
}

/** Map Slack external user IDs (e.g. U09GYNDKNBT) to org display names from team member identities. */
function buildSlackUserIdToNameMap(members: readonly TeamMember[]): ReadonlyMap<string, string> {
  const map: Map<string, string> = new Map();
  for (const member of members) {
    const fallbackLabel: string = (member.name?.trim() || member.email || 'Unknown').trim();
    const identities = member.identities ?? [];
    for (const identity of identities) {
      if (!identity.externalUserid) continue;
      if (!isSlackIdentitySource(identity.source)) continue;
      map.set(identity.externalUserid, fallbackLabel);
    }
  }
  return map;
}

function escapeMarkdownLinkLabel(label: string): string {
  return label.replace(/\\/g, '\\\\').replace(/\[/g, '\\[').replace(/\]/g, '\\]');
}

function formatAtMentionLabel(display: string): string {
  const t: string = display.trim();
  return t.startsWith('@') ? t : `@${t}`;
}

function preprocessSlackMentionsForMarkdown(text: string, slackIdToName: ReadonlyMap<string, string>): string {
  let result: string = text;
  result = result.replace(/<!channel>/gi, '[@channel](mention:broadcast:channel)');
  result = result.replace(/<!here>/gi, '[@here](mention:broadcast:here)');
  result = result.replace(/<!everyone>/gi, '[@everyone](mention:broadcast:everyone)');
  result = result.replace(
    /<@([A-Z0-9]+)(?:\|([^>\n]*))?>/g,
    (_full: string, slackUserId: string, inlineLabel: string | undefined) => {
      const trimmedInline: string | undefined = inlineLabel?.trim();
      const label: string =
        trimmedInline !== undefined && trimmedInline.length > 0
          ? trimmedInline
          : slackIdToName.get(slackUserId) ?? slackUserId;
      return `[${escapeMarkdownLinkLabel(formatAtMentionLabel(label))}](mention:slack:${slackUserId})`;
    },
  );
  return result;
}

/**
 * Model output often includes 3+ consecutive newlines; markdown turns those into empty <p> nodes,
 * each with prose margins — huge vertical gaps. One blank line is enough for a paragraph break.
 */
function collapseExcessiveMarkdownBlankLines(text: string): string {
  return text.replace(/\n{3,}/g, '\n\n').trimEnd();
}

function createSlackMentionRegex(): RegExp {
  return /<@([A-Z0-9]+)(?:\|([^>\n]*))?>|<!channel>|<!here>|<!everyone>/gi;
}

function UserMessageTextWithMentions({
  text,
  slackIdToName,
  bodyClassName = 'mt-0.5',
}: {
  text: string;
  slackIdToName: ReadonlyMap<string, string>;
  bodyClassName?: string;
}): JSX.Element {
  const re: RegExp = createSlackMentionRegex();
  const nodes: JSX.Element[] = [];
  let lastIndex: number = 0;
  let match: RegExpExecArray | null;
  let key: number = 0;
  while ((match = re.exec(text)) !== null) {
    const start: number = match.index;
    if (start > lastIndex) {
      nodes.push(<span key={`plain-${key++}`}>{text.slice(lastIndex, start)}</span>);
    }
    const full: string = match[0];
    const lower: string = full.toLowerCase();
    let display: string;
    if (lower === '<!channel>') display = '@channel';
    else if (lower === '<!here>') display = '@here';
    else if (lower === '<!everyone>') display = '@everyone';
    else {
      const uid: string = match[1] ?? '';
      const trimmedInline: string | undefined = match[2]?.trim();
      display =
        trimmedInline !== undefined && trimmedInline.length > 0
          ? formatAtMentionLabel(trimmedInline)
          : formatAtMentionLabel(slackIdToName.get(uid) ?? uid);
    }
    nodes.push(
      <span
        key={`mention-${key++}`}
        className="inline-flex items-center rounded bg-primary-500/15 text-primary-700 dark:text-primary-300 px-1 py-px text-[14px] font-semibold align-baseline mx-px"
      >
        {display}
      </span>,
    );
    lastIndex = start + full.length;
  }
  const bodyClasses: string = `${CHAT_MSG_BODY} ${bodyClassName}`;
  if (nodes.length === 0) {
    return <div className={bodyClasses}>{text}</div>;
  }
  if (lastIndex < text.length) {
    nodes.push(<span key={`plain-${key++}`}>{text.slice(lastIndex)}</span>);
  }
  return <div className={bodyClasses}>{nodes}</div>;
}

function shouldGroupMessageWithPrevious(
  prev: ChatMessage | undefined,
  current: ChatMessage,
  currentUserId: string | null | undefined,
): boolean {
  if (!prev) return false;
  if (prev.role !== current.role) return false;
  if (current.role === 'assistant') return true;
  const prevUid: string = prev.userId ?? currentUserId ?? 'self';
  const currUid: string = current.userId ?? currentUserId ?? 'self';
  return prevUid === currUid;
}

function SummaryCard({ summary }: { summary: ConversationSummaryData }): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left rounded-lg border border-surface-700 bg-surface-850 px-4 py-3 transition-colors hover:bg-surface-800"
      >
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-primary-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span className="text-sm font-medium text-surface-300">Conversation Summary</span>
          <svg
            className={`w-4 h-4 text-surface-400 ml-auto transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
        {!expanded && (
          <p className="mt-1 text-sm text-surface-400 truncate">{summary.overall}</p>
        )}
      </button>
      {expanded && (
        <div className="mt-0 rounded-b-lg border border-t-0 border-surface-700 bg-surface-850 px-4 py-3 space-y-3">
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-surface-400 mb-1">Overall</h4>
            <p className="text-sm text-surface-200">{summary.overall}</p>
          </div>
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-surface-400 mb-1">Recent Updates</h4>
            <p className="text-sm text-surface-200">{summary.recent}</p>
          </div>
        </div>
      )}
    </div>
  );
}

interface SuggestedInvitesBannerProps {
  invites: Array<{ id: string; name: string | null; email: string }>;
  onAdd: (userIds: string[]) => void;
  onDismiss: () => void;
}

function SuggestedInvitesBanner({ invites, onAdd, onDismiss }: SuggestedInvitesBannerProps): JSX.Element {
  const names = invites.map(u => u.name || u.email).join(', ');
  const isMultiple = invites.length > 1;

  return (
    <div className="mb-4 rounded-lg border border-primary-500/30 bg-surface-850 px-4 py-3 shadow-lg animate-in fade-in slide-in-from-top-2 duration-300">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-full bg-primary-500/20 flex items-center justify-center flex-shrink-0">
            <svg className="w-4 h-4 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-surface-100">
              {isMultiple ? `${names} are not in this chat.` : `${names} is not in this chat.`}
            </p>
            <p className="text-xs text-surface-400 truncate">
              {isMultiple ? 'Would you like to add them so they can see this conversation?' : 'Would you like to add them so they can see this conversation?'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            type="button"
            onClick={onDismiss}
            className="px-3 py-1.5 text-xs font-medium text-surface-400 hover:text-surface-200 transition-colors"
          >
            Dismiss
          </button>
          <button
            type="button"
            onClick={() => onAdd(invites.map(u => u.id))}
            className="px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 text-white rounded-md shadow-sm transition-colors"
          >
            {isMultiple ? 'Add them' : 'Add them'}
          </button>
        </div>
      </div>
    </div>
  );
}

export function Chat({
  userId,
  organizationId,
  chatId,
  sendMessage,
  isConnected,
  connectionState,
  crmApprovalResults,
  onConversationNotFound,
  creditsInfo,
}: ChatProps): JSX.Element {
  const isMobile = useIsMobile();

  // Credits status
  const creditsPct = creditsInfo && creditsInfo.included > 0 ? creditsInfo.balance / creditsInfo.included : 1;
  const outOfCredits = creditsInfo != null && creditsInfo.balance <= 0;
  const lowCredits = creditsInfo != null && creditsPct <= 0.1 && !outOfCredits;

  // Get per-conversation state from Zustand
  const conversationState = useConversationState(chatId ?? null);
  const suggestedInvites = conversationState?.suggestedInvites ?? [];

  const handleSuggestedInvitesAdd = useCallback(async (userIds: string[]) => {
    if (!chatId) return;
    try {
      const added: Array<{ id: string; name: string | null; email: string }> = [];
      for (const uid of userIds) {
        const { data, error } = await apiRequest<{
          participant: { id: string; name: string | null; email: string };
        }>(`/chat/conversations/${chatId}/participants`, {
          method: 'POST',
          body: JSON.stringify({ user_id: uid }),
        });

        if (error) {
          console.error(`[Chat] Failed to add participant ${uid}:`, error);
          continue;
        }

        if (data?.participant) {
          added.push({
            id: data.participant.id,
            name: data.participant.name,
            email: data.participant.email,
          });
        }
      }

      if (added.length > 0) {
        setConversationParticipants((prev) => [...prev, ...added]);
      }
      useChatStore.getState().clearConversationSuggestedInvites(chatId);
    } catch (err) {
      console.error('[Chat] Failed to add suggested participants:', err);
    }
  }, [chatId]);

  const handleSuggestedInvitesDismiss = useCallback(() => {
    if (!chatId) return;
    useChatStore.getState().clearConversationSuggestedInvites(chatId);
  }, [chatId]);
  const activeTasksByConversation = useActiveTasksByConversation();
  const chatTitle = conversationState?.title ?? 'New Chat';
  const conversationThinking = conversationState?.isThinking ?? false;
  
  // Get actions from Zustand (stable references)
  const addConversationMessage = useAppStore((s) => s.addConversationMessage);
  const setConversationMessages = useAppStore((s) => s.setConversationMessages);
  const setConversationTitle = useAppStore((s) => s.setConversationTitle);
  const setConversationThinking = useAppStore((s) => s.setConversationThinking);
  const setConversationAgentResponding = useAppStore((s) => s.setConversationAgentResponding);
  const fetchOlderMessages = useAppStore((s) => s.fetchOlderMessages);
  const clearUnreadConversation = useAppStore((s) => s.clearUnreadConversation);
  const clearExpiredTyping = useAppStore((s) => s.clearExpiredTyping);
  const pendingChatInput = useAppStore((s) => s.pendingChatInput);
  const setPendingChatInput = useAppStore((s) => s.setPendingChatInput);
  const pendingChatAutoSend = useAppStore((s) => s.pendingChatAutoSend);
  const setPendingChatAutoSend = useAppStore((s) => s.setPendingChatAutoSend);
  
  // Local state
  const [input, setInput] = useState<string>('');
  const [composerFocused, setComposerFocused] = useState<boolean>(false);
  const [currentArtifactId, setCurrentArtifactId] = useState<string | null>(null);
  const [currentAttachmentId, setCurrentAttachmentId] = useState<string | null>(null);
  const [currentAttachmentMeta, setCurrentAttachmentMeta] = useState<{ filename: string; mimeType: string } | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  // App preview panel state
  const [previewAppId, setPreviewAppId] = useState<string | null>(null);
  const [previewCollapsed, setPreviewCollapsed] = useState(false);
  const [previewDismissed, setPreviewDismissed] = useState(false);
  const [previewHeight, setPreviewHeight] = useState(300);
  const [selectedToolCall, setSelectedToolCall] = useState<ToolCallData | null>(null);
  const [toolApprovals, setToolApprovals] = useState<Map<string, ToolApprovalState>>(new Map());
  const [localConversationId, setLocalConversationId] = useState<string | null>(chatId ?? null);
  // Use activeTasksByConversation as fallback when chatId doesn't match (e.g. new chat before URL update, post-WS reconnect)
  const currentConvIdForTask: string | null = localConversationId ?? chatId ?? null;
  const taskIdFromMap: string | undefined = currentConvIdForTask ? activeTasksByConversation[currentConvIdForTask] : undefined;
  const activeTaskId: string | null = (conversationState?.activeTaskId ?? taskIdFromMap) ?? null;
  // Pending messages for new conversations (before we have an ID)
  const [pendingMessages, setPendingMessages] = useState<ChatMessage[]>([]);
  const [pendingThinking, setPendingThinking] = useState<boolean>(false);
  const [conversationType, setConversationType] = useState<string | null>(null);
  const [conversationScope, setConversationScope] = useState<'private' | 'shared'>('shared');
  const [conversationCreatorId, setConversationCreatorId] = useState<string | null>(null);
  const [isEditingHeaderTitle, setIsEditingHeaderTitle] = useState(false);
  const [headerTitleDraft, setHeaderTitleDraft] = useState('');
  const headerTitleInputRef = useRef<HTMLInputElement>(null);
  const scopePatchInFlightRef = useRef(false);
  const [conversationParticipants, setConversationParticipants] = useState<Array<{
    id: string;
    name: string | null;
    email: string;
    avatarUrl?: string | null;
  }>>([]);
  const [isWorkflowPolling, setIsWorkflowPolling] = useState<boolean>(false);
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [chatHeaderMenuOpen, setChatHeaderMenuOpen] = useState<boolean>(false);
  const [shareChatLinkCopied, setShareChatLinkCopied] = useState<boolean>(false);
  const chatHeaderMenuRef = useRef<HTMLDivElement>(null);
  const [newConversationScope, setNewConversationScope] = useState<'private' | 'shared'>('shared');
  /** True while PATCH /scope is in flight (optimistic UI already applied). */
  const [scopeToggleSaving, setScopeToggleSaving] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState<boolean>(false);
  const [isLoadingOlder, setIsLoadingOlder] = useState<boolean>(false);
  const [messageMentions, setMessageMentions] = useState<Array<{ type: 'user'; userId: string } | { type: 'agent' }>>([]);
  const [mentionPopover, setMentionPopover] = useState<{ open: boolean; query: string; selectedIndex: number }>({
    open: false,
    query: '',
    selectedIndex: 0,
  });
  const { data: teamMembersData } = useTeamMembers(organizationId ?? null, userId ?? null);
  const slackUserIdToName: ReadonlyMap<string, string> = useMemo(
    () => buildSlackUserIdToNameMap(teamMembersData?.members ?? []),
    [teamMembersData?.members],
  );

  const mentionSuggestions = useMemo(() => {
    const members = teamMembersData?.members ?? [];
    // Derive query from input string directly (not mentionPopover.query)
    // to avoid one-character lag from React state batching.
    const lastAt = input.lastIndexOf('@');
    const rawQuery = mentionPopover.open && lastAt !== -1 ? input.substring(lastAt + 1) : '';
    const q: string = rawQuery.includes(' ') ? '' : rawQuery.toLowerCase();
    // Only offer @Basebase when query is empty (bare "@") or prefixes the agent name,
    // so e.g. "@Cyn" + Enter selects Cynthia, not Basebase at index 0.
    const agentCanonical: string = 'basebase';
    const showAgentOption: boolean = q.length === 0 || agentCanonical.startsWith(q);

    // Derive org email domain from the majority of member emails
    const domainCounts = new Map<string, number>();
    for (const m of members) {
      if (m.isGuest) continue;
      const d = m.email.split('@')[1]?.toLowerCase();
      if (d) domainCounts.set(d, (domainCounts.get(d) ?? 0) + 1);
    }
    let orgDomain = '';
    let maxCount = 0;
    for (const [d, c] of domainCounts) {
      if (c > maxCount) { orgDomain = d; maxCount = c; }
    }

    // Deduplicate: when multiple members share a name, keep the one on the org domain
    const byName = new Map<string, typeof members[number]>();
    for (const m of members) {
      if (m.isGuest) continue;
      const key = (m.name ?? m.email).trim().toLowerCase();
      const existing = byName.get(key);
      if (!existing) {
        byName.set(key, m);
      } else {
        // Prefer the member whose email matches the org domain
        const existingOnDomain = existing.email.toLowerCase().endsWith(`@${orgDomain}`);
        const currentOnDomain = m.email.toLowerCase().endsWith(`@${orgDomain}`);
        if (currentOnDomain && !existingOnDomain) {
          byName.set(key, m);
        }
      }
    }
    const dedupedMembers = Array.from(byName.values());

    const agentOption = { type: 'agent' as const, displayName: 'Basebase', userId: null };
    const userOptions = dedupedMembers
      .filter((m) => {
        if (!q) return true;
        const name = (m.name ?? '').toLowerCase();
        const emailLocal = (m.email.split('@')[0] ?? '').toLowerCase();
        // Match query against start of any word in the name, or start of email local part
        return name.split(/\s+/).some((w) => w.startsWith(q)) || emailLocal.startsWith(q);
      })
      .map((m) => ({
        type: 'user' as const,
        displayName: (m.name ?? m.email).trim() || m.email,
        userId: m.id,
        email: m.email,
      }));
    return showAgentOption ? [agentOption, ...userOptions] : userOptions;
  }, [teamMembersData?.members, mentionPopover.open, input]);

  // Attachment state
  const [pendingAttachments, setPendingAttachments] = useState<UploadResponse[]>([]);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [isDragOver, setIsDragOver] = useState<boolean>(false);
  
  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const isUserNearBottomRef = useRef<boolean>(true);
  const isProgrammaticScrollRef = useRef<boolean>(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const pendingTitleRef = useRef<string | null>(null);
  const pendingMessagesRef = useRef<ChatMessage[]>([]);
  const pendingAutoSendRef = useRef<string | null>(null);
  const messagesRef = useRef<ChatMessage[]>([]); // Track current messages for polling comparison
  const workflowDoneRef = useRef<boolean>(false); // Prevents polling restart after workflow completes
  const prevAppCountRef = useRef(0); // Track app count for auto-switching preview
  const dragContainerRef = useRef<HTMLDivElement>(null); // Container for drag-resize
  const lastTypingSentRef = useRef<number>(0);

  // Keep ref in sync with state
  pendingMessagesRef.current = pendingMessages;

  // Combined messages and thinking state (conversation + pending for new chats).
  // Always sort by timestamp to guard against race conditions where WebSocket
  // chunks (assistant message) arrive before the pending user message is moved
  // into the conversation state, which would otherwise cause out-of-order display.
  // If two messages share the same timestamp (common when backend timestamps have
  // coarse resolution), keep user questions before assistant responses so grouped
  // responses always appear after the asking question.
  const messages = useMemo(() => {
    const conversationMessages = conversationState?.messages ?? [];
    const combined: ChatMessage[] = pendingMessages.length > 0
      ? [...pendingMessages, ...conversationMessages]
      : conversationMessages;
    // Fast path: skip sort when already ordered (common case)
    let needsSort = false;
    for (let i = 1; i < combined.length; i++) {
      const prev = combined[i - 1] as ChatMessage;
      const curr = combined[i] as ChatMessage;
      if (prev.timestamp.getTime() > curr.timestamp.getTime()) {
        needsSort = true;
        break;
      }
    }
    if (!needsSort) return combined;

    return combined
      .map((message, index) => ({ message, index }))
      .sort((a, b) => {
        const timeDiff = a.message.timestamp.getTime() - b.message.timestamp.getTime();
        if (timeDiff !== 0) return timeDiff;

        if (a.message.role !== b.message.role) {
          return a.message.role === 'user' ? -1 : 1;
        }

        return a.index - b.index;
      })
      .map(({ message }) => message);
  }, [pendingMessages, conversationState?.messages]);
  const isThinking = pendingThinking || conversationThinking;
  const hasMoreMessages = conversationState?.hasMore ?? false;

  const notifyTyping = useCallback((): void => {
    const cid: string | null = localConversationId ?? chatId ?? null;
    if (!cid || conversationScope !== 'shared' || !isConnected) {
      return;
    }
    const now: number = Date.now();
    if (now - lastTypingSentRef.current < 3000) {
      return;
    }
    lastTypingSentRef.current = now;
    sendMessage({ type: 'typing', conversation_id: cid });
  }, [localConversationId, chatId, conversationScope, isConnected, sendMessage]);

  useEffect(() => {
    if (!chatId || conversationScope !== 'shared') {
      return;
    }
    const id: ReturnType<typeof setInterval> = setInterval(() => {
      clearExpiredTyping(chatId);
    }, 1000);
    return () => clearInterval(id);
  }, [chatId, conversationScope, clearExpiredTyping]);

  const activeHumanTypers: Array<{ userId: string; name: string }> = useMemo(() => {
    const tu: Record<string, TypingUserEntry> | undefined = conversationState?.typingUsers;
    if (!tu || Object.keys(tu).length === 0) {
      return [];
    }
    const cutoff: number = Date.now() - 5000;
    return Object.entries(tu)
      .filter(([, v]) => v.timestamp >= cutoff)
      .map(([userId, v]) => ({ userId, name: v.name }));
  }, [conversationState?.typingUsers]);

  // Agent is running if there's an active task OR we're in a thinking/pending state
  const agentRunning = activeTaskId !== null || isThinking;

  // Extract all apps from conversation messages (for preview panel).
  // When an app is updated, a newer block appears in a later message — use the latest version.
  const conversationApps = useMemo((): AppBlock["app"][] => {
    const latestById = new Map<string, AppBlock["app"]>();
    const order: string[] = [];
    for (const msg of messages) {
      for (const block of msg.contentBlocks) {
        if (block.type === "app") {
          const app = (block as AppBlock).app;
          if (!latestById.has(app.id)) order.push(app.id);
          latestById.set(app.id, app);
        }
      }
    }
    return order.map((id) => latestById.get(id)!);
  }, [messages]);

  // Derive current artifact from messages (latest block with matching id) so updates propagate in real time
  const currentArtifact = useMemo((): AnyArtifact | null => {
    if (!currentArtifactId) return null;
    let latest: AnyArtifact | null = null;
    for (const msg of messages) {
      for (const block of msg.contentBlocks) {
        if (block.type === "artifact" && block.artifact.id === currentArtifactId) {
          latest = block.artifact as AnyArtifact;
        }
      }
    }
    return latest;
  }, [messages, currentArtifactId]);

  // Auto-switch to latest app when a new one appears
  useEffect(() => {
    if (conversationApps.length > prevAppCountRef.current && conversationApps.length > 0) {
      const latestApp = conversationApps[conversationApps.length - 1];
      if (latestApp) {
        setPreviewAppId(latestApp.id);
        setPreviewCollapsed(false);
        setPreviewDismissed(false);
      }
    }
    // Default to latest app if no selection yet
    if (conversationApps.length > 0 && previewAppId === null) {
      const latestApp = conversationApps[conversationApps.length - 1];
      if (latestApp) {
        setPreviewAppId(latestApp.id);
      }
    }
    prevAppCountRef.current = conversationApps.length;
  }, [conversationApps, previewAppId]);

  // Track if this conversation has uncommitted changes (write tools completed)
  const hasUncommittedChanges = useMemo(() => {
    return messages.some((msg) =>
      (msg.contentBlocks ?? []).some(
        (block) =>
          block.type === 'tool_use' &&
          (block as ToolUseBlock).status === 'complete' &&
          ((block as ToolUseBlock).name === 'write_to_system_of_record' ||
           (block as ToolUseBlock).name === 'run_sql_write')
      )
    );
  }, [messages]);

  // Handle tool approval (generic for all tools)
  const handleToolApprove = useCallback((operationId: string, options?: Record<string, unknown>) => {
    const existing = toolApprovals.get(operationId);
    setToolApprovals((prev) => {
      const newMap = new Map(prev);
      newMap.set(operationId, {
        operationId,
        toolName: existing?.toolName ?? 'unknown',
        isProcessing: true,
        result: null,
      });
      return newMap;
    });
    const currentConversationId = localConversationId || chatId;
    sendMessage({
      type: 'tool_approval',
      operation_id: operationId,
      approved: true,
      options: options ?? {},
      conversation_id: currentConversationId,
    });
  }, [sendMessage, localConversationId, chatId, toolApprovals]);

  // Handle tool cancel (generic for all tools)
  const handleToolCancel = useCallback((operationId: string) => {
    const existing = toolApprovals.get(operationId);
    setToolApprovals((prev) => {
      const newMap = new Map(prev);
      newMap.set(operationId, {
        operationId,
        toolName: existing?.toolName ?? 'unknown',
        isProcessing: true,
        result: null,
      });
      return newMap;
    });
    const currentConversationId = localConversationId || chatId;
    sendMessage({
      type: 'tool_approval',
      operation_id: operationId,
      approved: false,
      conversation_id: currentConversationId,
    });
  }, [sendMessage, localConversationId, chatId, toolApprovals]);

  // Sync tool approval results from parent (handles both old crm_approval and new tool_approval)
  useEffect(() => {
    crmApprovalResults.forEach((result, operationId) => {
      setToolApprovals((prev) => {
        const existing = prev.get(operationId);
        if (existing?.isProcessing) {
          const newMap = new Map(prev);
          newMap.set(operationId, {
            ...existing,
            isProcessing: false,
            result: result as WsToolApprovalResult,
          });
          return newMap;
        }
        return prev;
      });
    });
  }, [crmApprovalResults]);

  // Reset local state when chatId changes
  useEffect(() => {
    setLocalConversationId(chatId ?? null);
    setCurrentArtifactId(null);
    setCurrentAttachmentId(null);
    setCurrentAttachmentMeta(null);
    // Reset preview state for new conversation
    setPreviewDismissed(false);
    setPreviewAppId(null);
    setPreviewCollapsed(false);
    prevAppCountRef.current = 0;
    // Reset conversation type and scope when starting a new chat
    if (!chatId) {
      setConversationType(null);
      setIsWorkflowPolling(false);
      setNewConversationScope('shared'); // Default to shared for new conversations
      setConversationCreatorId(null);
      setConversationParticipants([]);
    }
    setIsEditingHeaderTitle(false);
    // Reset workflow-done flag whenever the conversation changes
    workflowDoneRef.current = false;
    // Only clear pending messages if we're switching to an EXISTING chat
    // (i.e., when we have no pending messages to move to the new conversation)
    // If pendingMessages exist, the next effect will move them instead
    if (chatId && pendingMessagesRef.current.length === 0) {
      setPendingMessages([]);
      setPendingThinking(false);
    }
  }, [chatId]);

  // When a new conversation is created, move pending messages to it
  useEffect(() => {
    if (localConversationId && pendingMessages.length > 0) {
      for (const msg of pendingMessages) {
        addConversationMessage(localConversationId, msg);
      }
      if (pendingThinking) {
        setConversationThinking(localConversationId, true);
      }
      // Propagate agentResponding so subsequent messages before `message_sent`
      // arrives see the correct state instead of `undefined`.
      setConversationAgentResponding(localConversationId, pendingThinking);
      // Clear pending state
      setPendingMessages([]);
      setPendingThinking(false);
    }
  }, [localConversationId, pendingMessages, pendingThinking, addConversationMessage, setConversationThinking, setConversationAgentResponding]);

  // Listen for conversation_created in parent and update localConversationId
  // This happens via the store update from AppLayout

  // Auto-focus input when on a new empty chat
  useEffect(() => {
    if (chatId === null && messages.length === 0 && !isLoading && isConnected) {
      const timer = setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [chatId, messages.length, isLoading, isConnected]);

  // Re-focus the textarea after the composer expands (collapsed → expanded swaps the DOM element)
  useEffect(() => {
    if (composerFocused) {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [composerFocused]);

  // Load conversation when selecting an existing chat from sidebar
  useEffect(() => {
    // If no chatId, this is a new chat
    if (!chatId) {
      setIsLoading(false);
      return;
    }

    // If we have pending messages, we're creating a new conversation - don't load from API
    // The pending messages will be moved to this conversation by another effect
    // Use ref to avoid re-running effect when pendingMessages changes
    if (pendingMessagesRef.current.length > 0) {
      // New conversation created by this user — set creator to self
      setConversationCreatorId(userId ?? null);
      setIsLoading(false);
      return;
    }

    // If we already have messages for this conversation in state, don't reload
    // (This handles both active tasks populating via WebSocket AND cached state)
    const existingState = useAppStore.getState().conversations[chatId];
    if (existingState && existingState.messages.length > 0) {
      // Still set conversation metadata from recentChats (skipping API fetch skips this otherwise)
      const chatInfo = useAppStore.getState().recentChats.find(c => c.id === chatId);
      if (chatInfo) {
        setConversationScope(chatInfo.scope);
        setConversationCreatorId(chatInfo.userId ?? null);
      }
      setIsLoading(false);
      return;
    }

    // Use the store's deduplicating fetcher — reuses the in-flight promise
    // if a hover-prefetch already started the same request.
    let cancelled = false;
    setIsLoading(true);

    const loadConversation = async (): Promise<void> => {
      try {
        const fetchConversationData = useAppStore.getState().fetchConversationData;
        const data = await fetchConversationData(chatId);

        if (cancelled) {
          return;
        }

        if (data) {
          // fetchConversationData already applied messages/title/hasMore/summary/
          // agentResponding to the store. Set local-only metadata here.
          setConversationType(data.type ?? null);
          setConversationScope((data.scope ?? 'shared') as 'private' | 'shared');
          setConversationCreatorId(data.user_id ?? null);
          setConversationParticipants(
            (data.participants ?? []).map((p: { id: string; name: string | null; email: string; avatar_url?: string | null }) => ({
              id: p.id,
              name: p.name,
              email: p.email,
              avatarUrl: p.avatar_url,
            }))
          );

          setTimeout(() => {
            messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
          }, 50);
        } else {
          // null = already loaded (prefetch beat us). Set local metadata from recentChats.
          const chatInfo = useAppStore.getState().recentChats.find(c => c.id === chatId);
          if (chatInfo) {
            setConversationScope(chatInfo.scope);
            setConversationCreatorId(chatInfo.userId ?? null);
          }
        }
      } catch (err) {
        const errStr: string = err instanceof Error ? err.message : String(err);
        const is404: boolean = errStr.includes('404') || errStr.toLowerCase().includes('not found');
        if (is404 && onConversationNotFound) {
          onConversationNotFound();
        } else {
          console.error('[Chat] Failed to load conversation:', errStr);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    void loadConversation();

    return () => {
      cancelled = true;
      setIsLoading(false);
    };
  }, [chatId, userId, onConversationNotFound]);

  // Mark notifications as read when opening a conversation
  useEffect(() => {
    if (!chatId) return;
    clearUnreadConversation(chatId);
    void apiRequest('/notifications/read', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: chatId }),
    });
  }, [chatId, clearUnreadConversation]);

  // Keep messagesRef in sync for polling comparison (avoids stale closure)
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Poll for updates on workflow conversations (Celery workers can't send WebSocket updates)
  useEffect(() => {
    // Only poll for workflow conversations that haven't finished yet
    if (!chatId || conversationType !== 'workflow' || workflowDoneRef.current) {
      setIsWorkflowPolling(false);
      return;
    }

    setIsWorkflowPolling(true);
    let pollCount = 0;
    const maxPolls = 300; // Poll for up to 10 minutes (300 * 2 seconds)

    const pollInterval = setInterval(async () => {
      pollCount++;
      if (pollCount > maxPolls) {
        setIsWorkflowPolling(false);
        clearInterval(pollInterval);
        return;
      }

      try {
        const { data, error } = await getConversation(chatId);
        if (data && !error) {
          const loadedMessages: ChatMessage[] = data.messages.map((msg) => ({
            id: msg.id,
            role: msg.role as 'user' | 'assistant',
            contentBlocks: msg.content_blocks,
            timestamp: new Date(msg.created_at),
          }));
          
          // Check if content has changed (not just message count)
          // Use ref to get current messages (avoids stale closure)
          const currentContent = JSON.stringify(messagesRef.current.map(m => m.contentBlocks));
          const newContent = JSON.stringify(loadedMessages.map(m => m.contentBlocks));

          if (newContent !== currentContent) {
            setConversationMessages(chatId, loadedMessages);

            // If any completed tool is write_to_system_of_record, refresh
            // the pending-changes sidebar badge (workflows don't use WS).
            const hasPendingWrite: boolean = loadedMessages.some((m) =>
              (m.contentBlocks || []).some(
                (b) =>
                  b.type === 'tool_use' &&
                  (b as ToolUseBlock).status === 'complete' &&
                  ((b as ToolUseBlock).name === 'write_to_system_of_record' ||
                   (b as ToolUseBlock).name === 'run_sql_write'),
              ),
            );
            if (hasPendingWrite) {
              window.dispatchEvent(new Event('pending-changes-updated'));
            }
          }
          
          // Stop polling when the workflow is truly finished.
          // The agent always ends with a text summary after all tool calls,
          // so we check that (a) there are no running tools AND (b) the last
          // content block is a text block (not a tool_use that might be
          // followed by more tool calls in the next orchestrator turn).
          const lastMsg = loadedMessages[loadedMessages.length - 1];
          const blocks = lastMsg?.contentBlocks || [];
          const lastBlock = blocks[blocks.length - 1];
          const hasRunningTools: boolean = lastMsg?.role === 'assistant' && blocks.some(
            (b) => b.type === 'tool_use' && (b as ToolUseBlock).status !== 'complete'
          );
          const endsWithText: boolean = lastBlock?.type === 'text' && typeof lastBlock.text === 'string' && lastBlock.text.length > 0;
          const workflowDone: boolean = loadedMessages.length >= 2 && lastMsg?.role === 'assistant' && !hasRunningTools && endsWithText;
          if (workflowDone) {
            workflowDoneRef.current = true;
            setIsWorkflowPolling(false);
            clearInterval(pollInterval);
          }
        }
      } catch (err) {
        console.error('[Chat] Polling error:', err);
      }
    }, 2000); // Poll every 2 seconds

    return () => {
      setIsWorkflowPolling(false);
      clearInterval(pollInterval);
    };
  // Note: messages.length deliberately excluded — polling is self-contained via
  // the interval and stops via workflowDoneRef. Including it caused restarts.
  }, [chatId, userId, conversationType, setConversationMessages]);

  // Track whether user is near the bottom of the scroll container.
  // Only update on user-initiated scrolls (ignore programmatic ones).
  // When user scrolls up, we "lock" the scroll position until they scroll back down.
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleScroll = (): void => {
      if (isProgrammaticScrollRef.current) return;
      const threshold = 100; // px from bottom
      const distanceFromBottom: number = container.scrollHeight - container.scrollTop - container.clientHeight;
      const isNearBottom = distanceFromBottom <= threshold;
      isUserNearBottomRef.current = isNearBottom;
      // Show "scroll to bottom" button when user has scrolled up significantly
      setShowScrollToBottom(!isNearBottom && distanceFromBottom > 200);
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  // Auto-scroll to bottom only if user is near the bottom.
  // This allows users to scroll up and read while the agent is working.
  useEffect(() => {
    // Only auto-scroll if user hasn't scrolled up
    if (!isUserNearBottomRef.current) return;
    
    const container = messagesContainerRef.current;
    if (!container) return;
    
    isProgrammaticScrollRef.current = true;
    container.scrollTop = container.scrollHeight;
    // Use a small delay to ensure the flag is cleared after the scroll event fires
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        isProgrammaticScrollRef.current = false;
      });
    });
  }, [messages, isThinking]);

  // Load earlier messages handler (pagination)
  const handleLoadOlderMessages = useCallback(async (): Promise<void> => {
    if (!chatId || isLoadingOlder || !hasMoreMessages) return;

    const container = messagesContainerRef.current;
    const previousScrollHeight = container?.scrollHeight ?? 0;

    setIsLoadingOlder(true);
    try {
      await fetchOlderMessages(chatId);
    } finally {
      setIsLoadingOlder(false);
    }

    // Preserve scroll position after prepending messages
    if (container) {
      requestAnimationFrame(() => {
        const newScrollHeight = container.scrollHeight;
        isProgrammaticScrollRef.current = true;
        container.scrollTop = newScrollHeight - previousScrollHeight;
        requestAnimationFrame(() => {
          isProgrammaticScrollRef.current = false;
        });
      });
    }
  }, [chatId, isLoadingOlder, hasMoreMessages, fetchOlderMessages]);

  // Scroll to bottom handler (for the button)
  const scrollToBottom = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    
    isProgrammaticScrollRef.current = true;
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    isUserNearBottomRef.current = true;
    setShowScrollToBottom(false);
    // Clear the flag after animation completes
    setTimeout(() => {
      isProgrammaticScrollRef.current = false;
    }, 500);
  }, []);

  const sendChatMessage = useCallback((message: string): void => {
    if ((!message.trim() && pendingAttachments.length === 0) || !isConnected) {
      return;
    }

    // Build content blocks for local display
    const contentBlocks: ChatMessage['contentBlocks'] = [];
    for (const att of pendingAttachments) {
      contentBlocks.push({
        type: 'attachment',
        filename: att.filename,
        mimeType: att.mime_type,
        size: att.size,
      } satisfies AttachmentBlock);
    }
    contentBlocks.push({ type: 'text', text: message });

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      contentBlocks,
      timestamp: new Date(),
    };

    // Get current conversation ID
    const currentConvId = localConversationId || chatId;

    const hasAgentMention: boolean = messageMentions.some((m) => m.type === 'agent');
    const hasUserMention: boolean = messageMentions.some((m) => m.type === 'user');
    const expectAgentResponse: boolean = hasAgentMention || (!hasUserMention && conversationState?.agentResponding !== false);

    if (currentConvId) {
      addConversationMessage(currentConvId, userMessage);
      if (expectAgentResponse) {
        setConversationThinking(currentConvId, true);
      }
      if (hasAgentMention || hasUserMention) {
        setConversationAgentResponding(currentConvId, expectAgentResponse);
      }
      if (crossTab.isAvailable) {
        crossTab.postMessage({
          kind: 'optimistic_message',
          payload: {
            conversationId: currentConvId,
            message: userMessage,
            setThinking: expectAgentResponse,
          },
        });
      }
    } else {
      pendingTitleRef.current = generateTitle(message);
      setPendingMessages(prev => [...prev, userMessage]);
      if (expectAgentResponse) {
        setPendingThinking(true);
      }
    }

    // Send message with conversation context, timezone info, and attachment IDs
    const attachmentIds: string[] = pendingAttachments.map((a) => a.upload_id);
    const now = new Date();
    // Build a local ISO-style string (no "Z" suffix) so the backend sees the
    // user's wall-clock time rather than UTC.
    const localIso: string = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    const mentionsPayload =
      messageMentions.length > 0
        ? messageMentions.map((m) =>
            m.type === 'agent' ? { type: 'agent' } : { type: 'user', user_id: m.userId }
          )
        : undefined;

    sendMessage({
      type: 'send_message',
      message,
      conversation_id: currentConvId,
      local_time: localIso,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
      ...(mentionsPayload ? { mentions: mentionsPayload } : {}),
      // Include scope for new conversations
      ...(!currentConvId ? { scope: newConversationScope } : {}),
    });

    setInput('');
    setMessageMentions([]);
    setPendingAttachments([]);

    // Reset textarea height to default
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
    }
  }, [
    isConnected,
    sendMessage,
    localConversationId,
    chatId,
    addConversationMessage,
    setConversationThinking,
    setConversationAgentResponding,
    pendingAttachments,
    newConversationScope,
    messageMentions,
    conversationState?.agentResponding,
  ]);

  // Handle retry: re-send the last user message
  const handleRetry = useCallback(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg?.role === 'user') {
        const text = (msg.contentBlocks ?? [])
          .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
          .map((b) => b.text)
          .join('');
        if (text.trim()) {
          sendChatMessage(text);
          return;
        }
      }
    }
  }, [messages, sendChatMessage]);

  // Consume pending chat input (from Search "Ask about" button or pipeline deal click)
  useEffect(() => {
    if (!pendingChatInput) {
      pendingAutoSendRef.current = null;
      return;
    }

    if (chatId !== null) {
      return;
    }

    setInput(pendingChatInput);

    if (pendingChatAutoSend) {
      if (pendingAutoSendRef.current === pendingChatInput) {
        return;
      }

      if (isConnected) {
        pendingAutoSendRef.current = pendingChatInput;
        sendChatMessage(pendingChatInput);
        setPendingChatInput(null);
        setPendingChatAutoSend(false);
      } else {
        console.warn('[Chat] Auto-send requested but socket not connected yet');
      }
      return;
    }

    {
      // Focus the input so user can see the pre-filled text
      setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
    }
    setPendingChatInput(null);
    setPendingChatAutoSend(false);
  }, [
    pendingChatInput,
    pendingChatAutoSend,
    chatId,
    isConnected,
    sendChatMessage,
    setPendingChatInput,
    setPendingChatAutoSend,
  ]);

  const handleSend = useCallback((): void => {
    sendChatMessage(input);
  }, [input, sendChatMessage]);

  const selectMention = useCallback(
    (item: { type: 'agent' } | { type: 'user'; userId: string }, displayName: string) => {
      const ta = inputRef.current;
      if (!ta) return;
      const cursor = ta.selectionStart ?? input.length;
      const textBefore = input.substring(0, cursor);
      const lastAt = textBefore.lastIndexOf('@');
      if (lastAt === -1) return;
      const before = input.substring(0, lastAt);
      const after = input.substring(cursor);
      const inserted = `@${displayName} `;
      setInput(before + inserted + after);
      setMessageMentions((prev) =>
        item.type === 'agent' ? [...prev, { type: 'agent' }] : [...prev, { type: 'user', userId: item.userId }]
      );
      setMentionPopover({ open: false, query: '', selectedIndex: 0 });
      requestAnimationFrame(() => {
        ta.focus();
        const newPos = lastAt + inserted.length;
        ta.setSelectionRange(newPos, newPos);
      });
    },
    [input]
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (mentionPopover.open && mentionSuggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setMentionPopover((p) => ({ ...p, selectedIndex: Math.min(p.selectedIndex + 1, mentionSuggestions.length - 1) }));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setMentionPopover((p) => ({ ...p, selectedIndex: Math.max(p.selectedIndex - 1, 0) }));
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const sel = mentionSuggestions[mentionPopover.selectedIndex];
        if (sel) {
          const displayName: string = sel.type === 'agent' ? 'Basebase' : (sel.displayName ?? sel.userId ?? '');
          const mention = sel.type === 'agent' ? { type: 'agent' as const } : { type: 'user' as const, userId: sel.userId };
          selectMention(mention, displayName);
        }
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setMentionPopover({ open: false, query: '', selectedIndex: 0 });
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const files: FileList | null = e.target.files;
    if (!files || files.length === 0) return;

    setIsUploading(true);
    try {
      const uploads: UploadResponse[] = [];
      for (const file of Array.from(files)) {
        const { data, error } = await uploadChatFile(file);
        if (error || !data) {
          console.error(`[Chat] Upload failed for ${file.name}:`, error);
          continue;
        }
        uploads.push(data);
      }
      if (uploads.length > 0) {
        setPendingAttachments((prev) => [...prev, ...uploads]);
      }
    } finally {
      setIsUploading(false);
      // Reset the input so the same file can be re-selected
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }, []);

  const handlePaste = useCallback(async (e: React.ClipboardEvent<HTMLTextAreaElement>): Promise<void> => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: File[] = [];
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }
    }
    if (imageFiles.length === 0) return;

    // Prevent the default paste (would insert garbled text for images)
    e.preventDefault();

    setIsUploading(true);
    try {
      const uploads: UploadResponse[] = [];
      for (const file of imageFiles) {
        const { data, error } = await uploadChatFile(file);
        if (error || !data) {
          console.error(`[Chat] Paste upload failed:`, error);
          continue;
        }
        uploads.push(data);
      }
      if (uploads.length > 0) {
        setPendingAttachments((prev) => [...prev, ...uploads]);
      }
    } finally {
      setIsUploading(false);
    }
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent<HTMLDivElement>): Promise<void> => {
    e.preventDefault();
    setIsDragOver(false);

    const files = Array.from(e.dataTransfer.files).filter(
      (f) => f.type.startsWith('image/') || f.type === 'application/pdf' || f.type.startsWith('text/') || ALLOWED_DROP_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)),
    );
    if (files.length === 0) return;

    setIsUploading(true);
    try {
      const uploads: UploadResponse[] = [];
      for (const file of files) {
        const { data, error } = await uploadChatFile(file);
        if (error || !data) {
          console.error(`[Chat] Drop upload failed for ${file.name}:`, error);
          continue;
        }
        uploads.push(data);
      }
      if (uploads.length > 0) {
        setPendingAttachments((prev) => [...prev, ...uploads]);
      }
    } finally {
      setIsUploading(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>): void => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>): void => {
    // Only hide if leaving the container (not entering a child)
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragOver(false);
  }, []);

  const removeAttachment = useCallback((uploadId: string): void => {
    setPendingAttachments((prev) => prev.filter((a) => a.upload_id !== uploadId));
  }, []);

  const handleStop = useCallback((): void => {
    if (!activeTaskId) {
      return;
    }

    sendMessage({
      type: 'cancel',
      task_id: activeTaskId,
    });
    
    // Clear thinking state immediately for responsiveness
    const currentConvId = localConversationId || chatId;
    if (currentConvId) {
      setConversationThinking(currentConvId, false);
    } else {
      setPendingThinking(false);
    }
  }, [activeTaskId, sendMessage, localConversationId, chatId, setConversationThinking]);

  // Drag handle for resizing preview panel
  const handlePreviewDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const container = dragContainerRef.current;
    if (!container) return;
    const containerRect = container.getBoundingClientRect();
    const startY = e.clientY;
    const startHeight = previewHeight;

    const onMouseMove = (ev: MouseEvent): void => {
      const delta = ev.clientY - startY;
      const maxH = containerRect.height * 0.7;
      const newHeight = Math.min(maxH, Math.max(150, startHeight + delta));
      setPreviewHeight(newHeight);
    };
    const onMouseUp = (): void => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  }, [previewHeight]);

  const handleSuggestionClick = (text: string): void => {
    setInput(text);
    inputRef.current?.focus();
    sendChatMessage(text);
  };

  // Copy conversation to clipboard
  const [copySuccess, setCopySuccess] = useState(false);
  const handleCopyConversation = useCallback(async () => {
    const lines: string[] = [];
    
    for (const msg of messages) {
      const role = msg.role === 'user' ? 'User' : 'Assistant';
      lines.push(`--- ${role} ---`);
      
      for (const block of msg.contentBlocks) {
        if (block.type === 'text') {
          lines.push(block.text);
        } else if (block.type === 'tool_use') {
          lines.push(`[Tool: ${block.name}]`);
          lines.push(`Input: ${JSON.stringify(block.input, null, 2)}`);
          if (block.result) {
            lines.push(`Result: ${JSON.stringify(block.result, null, 2)}`);
          }
          if (block.status) {
            lines.push(`Status: ${block.status}`);
          }
        }
      }
      lines.push('');
    }
    
    const text = lines.join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [messages]);

  const handleMenuCopyConversation = useCallback(async (): Promise<void> => {
    setShareChatLinkCopied(false);
    await handleCopyConversation();
    setChatHeaderMenuOpen(false);
  }, [handleCopyConversation]);

  const handleShareChatLink = useCallback(async (): Promise<void> => {
    if (!chatId) return;
    setCopySuccess(false);
    try {
      await navigator.clipboard.writeText(window.location.href);
      setShareChatLinkCopied(true);
      setChatHeaderMenuOpen(false);
      window.setTimeout(() => setShareChatLinkCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy share link:', err);
    }
  }, [chatId]);

  useEffect(() => {
    if (!chatHeaderMenuOpen) return;
    const onPointerDown = (e: PointerEvent): void => {
      const el: HTMLDivElement | null = chatHeaderMenuRef.current;
      if (el && !el.contains(e.target as Node)) {
        setChatHeaderMenuOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setChatHeaderMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [chatHeaderMenuOpen]);

  // Check if the current user can rename this conversation
  const canRenameHeader = chatId && (
    conversationScope === 'private' || conversationCreatorId === userId
  );

  const canToggleChatScope: boolean =
    userId != null &&
    conversationCreatorId != null &&
    conversationCreatorId === userId &&
    Boolean(chatId);

  const togglePinChat = useAppStore((s) => s.togglePinChat);
  const pinnedChatIds = useAppStore((s) => s.pinnedChatIds);
  const deleteConversation = useAppStore((s) => s.deleteConversation);
  const isCurrentChatUnread = useChatStore((s) => Boolean(chatId && s.unreadConversationIds.has(chatId)));
  const chatSearchTerm = useChatStore((s) => s.chatSearchTerm);
  const chatSearchMatchCount = useChatStore((s) => s.chatSearchMatchCount);
  const isCurrentChatPinned: boolean = Boolean(chatId && pinnedChatIds.includes(chatId));

  // When opened from search, auto-load ALL older messages so all matches are navigable.
  // Uses messages.length as a dep so it re-checks after initial load completes.
  const autoLoadedForChatRef = useRef<string | null>(null);
  useEffect(() => {
    if (!chatSearchTerm || !chatId) return;
    if (autoLoadedForChatRef.current === chatId) return;
    // Wait until we have at least some messages loaded
    const convState = useAppStore.getState().conversations[chatId];
    if (!convState || convState.messages.length === 0) return;
    if (!convState.hasMore) {
      autoLoadedForChatRef.current = chatId;
      return;
    }
    autoLoadedForChatRef.current = chatId;
    const loadAll = async (): Promise<void> => {
      let more = true;
      let safety = 0;
      while (more && safety < 50) {
        safety++;
        more = await useAppStore.getState().fetchOlderMessages(chatId);
      }
    };
    void loadAll();
  }, [chatSearchTerm, chatId, messages.length]);

  const startEditingHeaderTitle = useCallback(() => {
    if (!canRenameHeader) return;
    setHeaderTitleDraft(chatTitle);
    setIsEditingHeaderTitle(true);
    setTimeout(() => {
      headerTitleInputRef.current?.focus();
      headerTitleInputRef.current?.select();
    }, 0);
  }, [canRenameHeader, chatTitle]);

  const saveHeaderTitle = useCallback(async () => {
    setIsEditingHeaderTitle(false);
    const trimmed = headerTitleDraft.trim();
    if (!trimmed || !chatId || trimmed === chatTitle) return;
    setConversationTitle(chatId, trimmed);
    const { error } = await updateConversation(chatId, trimmed);
    if (error) {
      setConversationTitle(chatId, chatTitle);
    }
  }, [headerTitleDraft, chatId, chatTitle, setConversationTitle]);

  const cancelEditingHeaderTitle = useCallback(() => {
    setIsEditingHeaderTitle(false);
  }, []);

  const handleMenuRenameChat = useCallback((): void => {
    setChatHeaderMenuOpen(false);
    startEditingHeaderTitle();
  }, [startEditingHeaderTitle]);

  const handleMenuTogglePin = useCallback((): void => {
    if (!chatId) return;
    setChatHeaderMenuOpen(false);
    togglePinChat(chatId);
  }, [chatId, togglePinChat]);

  const handleMenuMarkAsRead = useCallback((): void => {
    if (!chatId) return;
    setChatHeaderMenuOpen(false);
    clearUnreadConversation(chatId);
  }, [chatId, clearUnreadConversation]);

  const handleMenuDeleteChat = useCallback((): void => {
    if (!chatId) return;
    setChatHeaderMenuOpen(false);
    void deleteConversation(chatId);
  }, [chatId, deleteConversation]);

  type ParticipantRow = { id: string; name: string | null; email: string; avatarUrl?: string | null };

  // Convert private conversation to shared (optimistic UI + revert on error)
  const handleMakeShared = useCallback(async () => {
    if (!chatId || scopePatchInFlightRef.current) return;
    scopePatchInFlightRef.current = true;

    const prevScope = conversationScope;
    const prevParticipants: ParticipantRow[] = conversationParticipants;
    setScopeToggleSaving(true);
    setConversationScope('shared');
    useAppStore.getState().setChatScope(chatId, 'shared');

    const revert = (): void => {
      setConversationScope(prevScope);
      setConversationParticipants(prevParticipants);
      useAppStore.getState().setChatScope(chatId, prevScope);
    };

    try {
      const { data, error } = await apiRequest<{ scope: string; participants: Array<{ id: string; name: string | null; email: string; avatar_url?: string | null }> }>(
        `/chat/conversations/${chatId}/scope`,
        { method: 'PATCH', body: JSON.stringify({ scope: 'shared' }) },
      );

      if (error || !data) {
        console.error('Failed to make shared:', error);
        revert();
        return;
      }

      setConversationParticipants(
        (data.participants ?? []).map((p) => ({
          id: p.id,
          name: p.name,
          email: p.email,
          avatarUrl: p.avatar_url,
        }))
      );
    } catch (err) {
      console.error('Failed to make shared:', err);
      revert();
    } finally {
      scopePatchInFlightRef.current = false;
      setScopeToggleSaving(false);
    }
  }, [chatId, conversationScope, conversationParticipants]);

  // Convert shared conversation to private (creator only); optimistic + revert on error
  const handleMakePrivate = useCallback(async () => {
    if (!chatId || scopePatchInFlightRef.current) return;
    scopePatchInFlightRef.current = true;

    const prevScope = conversationScope;
    const prevParticipants: ParticipantRow[] = conversationParticipants;
    setScopeToggleSaving(true);
    setConversationScope('private');
    useAppStore.getState().setChatScope(chatId, 'private');
    setConversationParticipants([]);

    const revert = (): void => {
      setConversationScope(prevScope);
      setConversationParticipants(prevParticipants);
      useAppStore.getState().setChatScope(chatId, prevScope);
    };

    try {
      const { error } = await apiRequest(
        `/chat/conversations/${chatId}/scope`,
        { method: 'PATCH', body: JSON.stringify({ scope: 'private' }) },
      );

      if (error) {
        console.error('Failed to make private:', error);
        revert();
      }
    } catch (err) {
      console.error('Failed to make private:', err);
      revert();
    } finally {
      scopePatchInFlightRef.current = false;
      setScopeToggleSaving(false);
    }
  }, [chatId, conversationScope, conversationParticipants]);

  // Search navigation state — use backend count, not DOM mark count
  const searchMatchTotal: number = chatSearchTerm ? chatSearchMatchCount : 0;
  const [searchMatchIndex, setSearchMatchIndex] = useState<number>(0);

  const scrollToSearchMatch = useCallback((idx: number) => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const marks = container.querySelectorAll('mark[data-search-highlight]');
    if (marks.length === 0) return;
    // Clamp and wrap
    const wrappedIdx = ((idx % marks.length) + marks.length) % marks.length;
    setSearchMatchIndex(wrappedIdx);
    // Style: dim all, highlight active
    marks.forEach((m, i) => {
      (m as HTMLElement).className = i === wrappedIdx
        ? 'bg-orange-400 text-black rounded-sm ring-2 ring-orange-500 font-semibold'
        : 'bg-amber-300 text-black rounded-sm';
    });
    marks[wrappedIdx]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

  // Highlight search term in message content via DOM TreeWalker.
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container || !chatSearchTerm?.trim()) {
      // Remove all highlights when search is cleared
      container?.querySelectorAll('mark[data-search-highlight]').forEach((el) => {
        const parent = el.parentNode;
        if (parent) {
          parent.replaceChild(document.createTextNode(el.textContent ?? ''), el);
          parent.normalize();
        }
      });
      setSearchMatchIndex(0);
      return;
    }

    const applyHighlights = (): void => {
      const term = chatSearchTerm.trim().toLowerCase();

      // Remove previous highlights
      container.querySelectorAll('mark[data-search-highlight]').forEach((el) => {
        const parent = el.parentNode;
        if (parent) {
          parent.replaceChild(document.createTextNode(el.textContent ?? ''), el);
          parent.normalize();
        }
      });

      // Walk text nodes and wrap ALL matches (not just first per node)
      const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
      const allMatches: { node: Text; index: number }[] = [];
      let textNode: Text | null;
      while ((textNode = walker.nextNode() as Text | null)) {
        const text = textNode.textContent?.toLowerCase() ?? '';
        let searchFrom = 0;
        let idx: number;
        while ((idx = text.indexOf(term, searchFrom)) >= 0) {
          allMatches.push({ node: textNode, index: idx });
          searchFrom = idx + term.length;
        }
      }

      // Apply marks in reverse order (so earlier indices don't shift)
      for (let i = allMatches.length - 1; i >= 0; i--) {
        const match = allMatches[i];
        if (!match) continue;
        const { node: matchNode, index } = match;
        try {
          const range = document.createRange();
          range.setStart(matchNode, index);
          range.setEnd(matchNode, index + term.length);
          const mark = document.createElement('mark');
          mark.setAttribute('data-search-highlight', '');
          mark.className = 'bg-amber-300 text-black rounded-sm';
          range.surroundContents(mark);
        } catch {
          // surroundContents can fail if range crosses element boundaries
        }
      }

      const totalMarks = container.querySelectorAll('mark[data-search-highlight]').length;
      if (totalMarks > 0) {
        setSearchMatchIndex(0);
        // Highlight first match as active
        const firstMark = container.querySelector('mark[data-search-highlight]');
        if (firstMark) {
          (firstMark as HTMLElement).className = 'bg-orange-400 text-black rounded-sm ring-2 ring-orange-500 font-semibold';
          firstMark.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
    };

    // Wait for React to paint, then apply highlights
    const rafId = requestAnimationFrame(() => {
      requestAnimationFrame(applyHighlights);
    });
    return () => cancelAnimationFrame(rafId);
  }, [chatSearchTerm, messages, isLoading]);

  if (isLoading) {
    return (
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <header className="hidden md:flex h-14 border-b border-surface-800 items-center px-4 md:px-6 flex-shrink-0">
          <div className="h-5 w-48 rounded bg-surface-800 animate-pulse" />
        </header>

        <div className="flex-1 overflow-hidden p-3 md:p-6">
          <div className="space-y-6">
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-surface-700 animate-pulse flex-shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1 max-w-[65%]">
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-4/5" />
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-3/5" />
              </div>
            </div>
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-surface-700 animate-pulse flex-shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1 max-w-[80%]">
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-full" style={{ animationDelay: '75ms' }} />
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-11/12" style={{ animationDelay: '150ms' }} />
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-3/4" style={{ animationDelay: '225ms' }} />
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-5/6" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-surface-700 animate-pulse flex-shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1 max-w-[55%]">
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-full" style={{ animationDelay: '375ms' }} />
              </div>
            </div>
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-surface-700 animate-pulse flex-shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1 max-w-[75%]">
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-full" style={{ animationDelay: '450ms' }} />
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-5/6" style={{ animationDelay: '525ms' }} />
                <div className="h-3 rounded-full bg-surface-800 animate-pulse w-2/3" style={{ animationDelay: '600ms' }} />
              </div>
            </div>
          </div>
        </div>

        <div className="flex-shrink-0 border-t border-surface-800 p-3 md:p-4">
          <div>
            <div className="h-11 rounded-xl bg-surface-800 animate-pulse" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Search navigation bar (browser-style Find) */}
      {chatSearchTerm && (
        <div className="flex h-10 bg-surface-900 border-b border-surface-700 items-center px-3 md:px-6 gap-2 md:gap-3 flex-shrink-0 overflow-x-auto">
          <button
            type="button"
            onClick={() => {
              // Keep chatSearchTerm so ChatsList restores search results
              useAppStore.getState().setCurrentView('chats');
            }}
            className="flex items-center gap-1 text-xs text-surface-400 hover:text-surface-200 font-medium"
            title="Back to search results"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
          </button>
          <div className="flex items-center gap-1 px-2 py-1 rounded bg-surface-800 border border-surface-700">
            <svg className="w-3.5 h-3.5 text-yellow-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <span className="text-xs text-surface-200 font-medium">{chatSearchTerm}</span>
          </div>
          {searchMatchTotal > 0 ? (
            <span className="text-xs text-surface-400 tabular-nums">
              {searchMatchIndex + 1} of {searchMatchTotal}
            </span>
          ) : (
            <span className="text-xs text-surface-500">No matches</span>
          )}
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => scrollToSearchMatch(searchMatchIndex - 1)}
              disabled={searchMatchTotal === 0}
              className="p-1 rounded hover:bg-surface-700 text-surface-400 hover:text-surface-200 disabled:opacity-30 disabled:cursor-not-allowed"
              title="Previous match"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => scrollToSearchMatch(searchMatchIndex + 1)}
              disabled={searchMatchTotal === 0}
              className="p-1 rounded hover:bg-surface-700 text-surface-400 hover:text-surface-200 disabled:opacity-30 disabled:cursor-not-allowed"
              title="Next match"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
          <button
            type="button"
            onClick={() => useChatStore.setState({ chatSearchTerm: null })}
            className="ml-auto p-1 rounded hover:bg-surface-700 text-surface-400 hover:text-surface-200"
            title="Close search"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
      {/* Header - hidden on mobile since AppLayout has mobile header */}
      <header className="hidden md:flex h-14 border-b border-surface-800 items-center justify-between px-4 md:px-6 flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          {/* Back to All Chats */}
          <button
            type="button"
            onClick={() => {
              if (chatSearchTerm) {
                useAppStore.getState().setCurrentView('chats');
              } else {
                useAppStore.getState().setCurrentView('chats');
              }
            }}
            className="p-1 -ml-1 rounded-md text-surface-500 hover:text-surface-200 hover:bg-surface-800 transition-colors flex-shrink-0"
            title="All Chats"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          {isEditingHeaderTitle ? (
            <input
              ref={headerTitleInputRef}
              type="text"
              value={headerTitleDraft}
              onChange={(e) => setHeaderTitleDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveHeaderTitle();
                if (e.key === 'Escape') cancelEditingHeaderTitle();
              }}
              onBlur={() => void saveHeaderTitle()}
              className="text-lg font-semibold text-surface-100 bg-transparent border-b border-primary-500 outline-none max-w-[200px] md:max-w-md"
              maxLength={100}
            />
          ) : (
            <div
              className={`flex items-center gap-1.5 group/title min-w-0 ${canRenameHeader ? 'cursor-pointer' : ''}`}
              onClick={canRenameHeader ? startEditingHeaderTitle : undefined}
              title={canRenameHeader ? 'Click to rename' : undefined}
            >
              <h1 className="text-lg font-semibold text-surface-100 truncate max-w-[200px] md:max-w-md">
                {chatTitle}
              </h1>
              {canRenameHeader && (
                <svg className="w-3.5 h-3.5 text-surface-500 opacity-0 group-hover/title:opacity-100 transition-opacity flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              )}
            </div>
          )}
          {/* Scope: read-only chip; owner changes visibility from ⋮ menu */}
          {chatId && (() => {
            const isShared: boolean = conversationScope === 'shared';
            const chipStatic: string =
              'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wide';
            const chipShared: string = `${chipStatic} bg-primary-500/15 text-primary-400/90`;
            const chipPrivate: string = `${chipStatic} bg-surface-700 text-surface-400`;

            return (
              <span
                className={`${isShared ? chipShared : chipPrivate} shrink-0 ${scopeToggleSaving ? 'opacity-70' : ''}`}
                title={
                  canToggleChatScope
                    ? isShared
                      ? 'Shared with team — use ⋮ menu to make private'
                      : 'Private — use ⋮ menu to share with team'
                    : isShared
                      ? 'Shared with team'
                      : 'Only the conversation creator can change visibility'
                }
              >
                {scopeToggleSaving ? (
                  <span
                    className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0 opacity-80 inline-block align-middle"
                    aria-hidden
                  />
                ) : null}
                {isShared ? (
                  'Shared'
                ) : (
                  <>
                    <ScopeLockIcon className="w-3 h-3 shrink-0 opacity-90" />
                    Private
                  </>
                )}
              </span>
            );
          })()}
          {/* Uncommitted changes indicator */}
          {hasUncommittedChanges && (
            <span 
              className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-yellow-500/20 text-yellow-400 cursor-pointer hover:bg-yellow-500/30 transition-colors"
              title="This conversation has uncommitted changes. Click to review."
              onClick={() => {
                const setCurrentView = useAppStore.getState().setCurrentView;
                setCurrentView('pending-changes');
              }}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400" />
              Changes
            </span>
          )}
          {(() => {
            const contextPct = (conversationState?.contextTokens ?? 0) / 200_000;
            return conversationState?.contextTokens != null ? (
              <div className="flex items-center gap-1.5 ml-2" title={`${Math.round(contextPct * 100)}% context used (${(conversationState.contextTokens / 1000).toFixed(0)}k / 200k tokens)`}>
                <div className="w-16 h-1.5 bg-surface-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      contextPct > 0.85 ? 'bg-red-400' :
                      contextPct > 0.6 ? 'bg-yellow-400' :
                      'bg-primary-400'
                    }`}
                    style={{ width: `${Math.min(contextPct * 100, 100)}%` }}
                  />
                </div>
                <span className={`text-[10px] tabular-nums ${
                  contextPct > 0.85 ? 'text-red-400' :
                  contextPct > 0.6 ? 'text-yellow-400' :
                  'text-surface-500'
                }`}>
                  {Math.round(contextPct * 100)}%
                </span>
              </div>
            ) : null;
          })()}
        </div>
        <div className="flex items-center gap-3">
          {/* Shared: participant avatars only (team-wide visibility; no invite) */}
          {conversationScope === 'shared' && conversationParticipants.length > 0 && (
            <div className="flex items-center gap-2">
              <div className="flex -space-x-2">
                {conversationParticipants.slice(0, 4).map((p, idx) => (
                  <Avatar
                    key={p.id}
                    user={p}
                    size="sm"
                    bordered
                    className="border-2 border-surface-900"
                    style={{ zIndex: 4 - idx }}
                  />
                ))}
                {conversationParticipants.length > 4 && (
                  <div
                    className="w-6 h-6 rounded-full border-2 border-surface-700 dark:border-surface-600 bg-surface-700 flex items-center justify-center text-xs font-medium text-surface-300"
                    title={`${conversationParticipants.length - 4} more participants`}
                  >
                    +{conversationParticipants.length - 4}
                  </div>
                )}
              </div>
            </div>
          )}
          {/* Private: avatars + add people */}
          {conversationScope === 'private' && (
            <div className="flex items-center gap-2">
              {conversationParticipants.length > 0 && (
                <div className="flex -space-x-2">
                  {conversationParticipants.slice(0, 4).map((p, idx) => (
                    <Avatar
                      key={p.id}
                      user={p}
                      size="sm"
                      bordered
                      className="border-2 border-surface-900"
                      style={{ zIndex: 4 - idx }}
                    />
                  ))}
                  {conversationParticipants.length > 4 && (
                    <div
                      className="w-6 h-6 rounded-full border-2 border-surface-700 dark:border-surface-600 bg-surface-700 flex items-center justify-center text-xs font-medium text-surface-300"
                      title={`${conversationParticipants.length - 4} more participants`}
                    >
                      +{conversationParticipants.length - 4}
                    </div>
                  )}
                </div>
              )}
              <button
                type="button"
                disabled={!userId}
                onClick={() => setShowInviteModal(true)}
                className="p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                title={userId ? 'Add people to this chat' : 'Sign in to add people'}
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
                </svg>
              </button>
            </div>
          )}
          <ConnectionStatus state={connectionState} />
          <div className="relative flex-shrink-0" ref={chatHeaderMenuRef}>
            <button
              type="button"
              className="p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
              title={
                copySuccess
                  ? 'Conversation copied'
                  : shareChatLinkCopied
                    ? 'Link copied'
                    : 'Chat options'
              }
              aria-haspopup="menu"
              aria-expanded={chatHeaderMenuOpen}
              onClick={() => setChatHeaderMenuOpen((o) => !o)}
            >
              {copySuccess || shareChatLinkCopied ? (
                <svg className="w-5 h-5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden>
                  <circle cx="12" cy="6" r="1.5" />
                  <circle cx="12" cy="12" r="1.5" />
                  <circle cx="12" cy="18" r="1.5" />
                </svg>
              )}
            </button>
            {chatHeaderMenuOpen && (
              <div
                className="absolute right-0 top-full mt-1 min-w-[13rem] rounded-lg border border-surface-700 bg-surface-900 py-1 shadow-xl z-[60]"
                role="menu"
              >
                <button
                  type="button"
                  role="menuitem"
                  disabled={messages.length === 0}
                  className="flex w-full items-center px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed"
                  onClick={() => void handleMenuCopyConversation()}
                >
                  Copy to clipboard
                </button>
                {chatId ? (
                  <>
                    <div className="my-1 border-t border-surface-800" role="separator" />
                    {canRenameHeader ? (
                      <button
                        type="button"
                        role="menuitem"
                        className="flex w-full items-center px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-800"
                        onClick={handleMenuRenameChat}
                      >
                        Rename chat
                      </button>
                    ) : null}
                    <button
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-800"
                      onClick={handleMenuTogglePin}
                    >
                      {isCurrentChatPinned ? 'Unpin chat' : 'Pin chat'}
                    </button>
                    {isCurrentChatUnread ? (
                      <button
                        type="button"
                        role="menuitem"
                        className="flex w-full items-center px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-800"
                        onClick={handleMenuMarkAsRead}
                      >
                        Mark as read
                      </button>
                    ) : null}
                  </>
                ) : null}
                {canToggleChatScope ? (
                  <>
                    <div className="my-1 border-t border-surface-800" role="separator" />
                    <button
                      type="button"
                      role="menuitem"
                      disabled={scopeToggleSaving || conversationScope === 'shared'}
                      className="flex w-full items-center px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed"
                      onClick={() => {
                        setChatHeaderMenuOpen(false);
                        void handleMakeShared();
                      }}
                    >
                      Make shared
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      disabled={scopeToggleSaving || conversationScope === 'private'}
                      className="flex w-full items-center px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed"
                      onClick={() => {
                        setChatHeaderMenuOpen(false);
                        void handleMakePrivate();
                      }}
                    >
                      Make private
                    </button>
                  </>
                ) : null}
                <div className="my-1 border-t border-surface-800" role="separator" />
                <button
                  type="button"
                  role="menuitem"
                  disabled={!chatId}
                  className="flex w-full items-center px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-800 disabled:opacity-40 disabled:cursor-not-allowed"
                  onClick={() => void handleShareChatLink()}
                >
                  Share chat
                </button>
                {chatId ? (
                  <>
                    <div className="my-1 border-t border-surface-800" role="separator" />
                    <button
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center px-3 py-2 text-left text-sm text-red-400 hover:bg-red-950/30 hover:text-red-300"
                      onClick={handleMenuDeleteChat}
                    >
                      Delete conversation
                    </button>
                  </>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Content area with messages and optional artifact sidebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* Messages column (vertical flex with optional app preview above) */}
        <div ref={dragContainerRef} className={`flex flex-col md:transition-[width] md:duration-300 md:ease-in-out ${currentArtifact ? 'md:w-1/2' : ''} flex-1 min-w-0 min-h-0`}>
          {/* App preview panel (above messages) */}
          {conversationApps.length > 0 && !previewDismissed && (
            <>
              <AppPreviewPanel
                apps={conversationApps}
                activeAppId={previewAppId}
                onActiveAppChange={setPreviewAppId}
                collapsed={previewCollapsed}
                onCollapsedChange={setPreviewCollapsed}
                onClose={() => setPreviewDismissed(true)}
                onAppError={(errorMsg: string) => {
                  const activeApp = conversationApps.find((a) => a.id === previewAppId) ?? conversationApps[conversationApps.length - 1];
                  if (activeApp) {
                    const fixPrompt = `The app "${activeApp.title}" has a compile/runtime error. Please fix it and create an updated version.\n\nError:\n\`\`\`\n${errorMsg}\n\`\`\``;
                    sendChatMessage(fixPrompt);
                  }
                }}
                height={previewHeight}
              />
              {/* Drag handle for resizing */}
              {!previewCollapsed && (
                <div
                  className="h-1 flex-shrink-0 cursor-row-resize bg-surface-800 hover:bg-primary-600 transition-colors group flex items-center justify-center"
                  onMouseDown={handlePreviewDragStart}
                >
                  <div className="w-8 h-0.5 rounded-full bg-surface-600 group-hover:bg-primary-400 transition-colors" />
                </div>
              )}
            </>
          )}

          {/* Messages scroll area */}
          <div className="relative flex-1 min-h-0">
            {suggestedInvites.length > 0 && (
              <div className="absolute top-0 left-0 right-0 z-10 px-3 md:px-6 pt-3 pb-4 bg-surface-900">
                <SuggestedInvitesBanner
                  invites={suggestedInvites}
                  onAdd={handleSuggestedInvitesAdd}
                  onDismiss={handleSuggestedInvitesDismiss}
                />
              </div>
            )}
            <div ref={messagesContainerRef} className="absolute inset-0 overflow-y-auto overflow-x-hidden p-3 md:p-6">
            {conversationState?.summary && <SummaryCard summary={conversationState.summary} />}
            {!userId && (
              <div className="mb-3 rounded-lg border border-amber-600/50 bg-amber-900/20 px-3 py-2 text-sm text-amber-200">
                User context is missing — artifacts and apps may not save correctly. Please refresh or re-sign in.
              </div>
            )}
            {messages.length === 0 && !isThinking ? (
              conversationType === 'workflow' ? (
                // Show loading state for workflow conversations waiting for agent to start
                <div className="flex-1 flex flex-col items-center justify-center py-20">
                  <div className="relative mb-6">
                    {/* Spinning ring */}
                    <div className="w-16 h-16 rounded-full border-4 border-surface-700 border-t-primary-500 animate-spin" />
                    {/* Center icon */}
                    <div className="absolute inset-0 flex items-center justify-center">
                      <svg className="w-6 h-6 text-primary-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    </div>
                  </div>
                  <h3 className="text-lg font-medium text-surface-200 mb-2">Running Workflow</h3>
                  <p className="text-surface-400 text-center max-w-md">
                    The agent is processing your workflow. Results will appear here momentarily...
                  </p>
                </div>
              ) : (
                <EmptyState onSuggestionClick={handleSuggestionClick} />
              )
            ) : (
              <div>
                {hasMoreMessages && (
                  <div className="flex justify-center py-2">
                    <button
                      type="button"
                      onClick={() => void handleLoadOlderMessages()}
                      disabled={isLoadingOlder}
                      className="text-xs text-surface-400 hover:text-surface-200 transition-colors disabled:opacity-50"
                    >
                      {isLoadingOlder ? 'Loading...' : 'Load earlier messages'}
                    </button>
                  </div>
                )}
                {messages.map((msg, idx) => {
                  const prevMsg: ChatMessage | undefined = idx > 0 ? messages[idx - 1] : undefined;
                  const showDivider: boolean = !!prevMsg && prevMsg.role !== msg.role;
                  const isGroupedWithPrevious: boolean = shouldGroupMessageWithPrevious(prevMsg, msg, userId);
                  return (
                    <div key={msg.id}>
                      {showDivider && <div className="h-2" />}
                      <MessageWithBlocks
                        message={msg}
                        isGroupedWithPrevious={isGroupedWithPrevious}
                        slackUserIdToName={slackUserIdToName}
                        toolApprovals={toolApprovals}
                        onArtifactClick={(a) => { setCurrentArtifactId(a.id); setCurrentAttachmentId(null); setCurrentAttachmentMeta(null); }}
                        onAttachmentClick={(id, meta) => { setCurrentAttachmentId(id); setCurrentAttachmentMeta(meta); setCurrentArtifactId(null); }}
                        onAppClick={(app: AppBlock["app"]) => { setPreviewAppId(app.id); setPreviewCollapsed(false); setPreviewDismissed(false); setCurrentArtifactId(null); setCurrentAttachmentId(null); setCurrentAttachmentMeta(null); }}
                        onToolApprove={handleToolApprove}
                        onToolCancel={handleToolCancel}
                        onToolClick={(block) => setSelectedToolCall({
                          toolName: block.name,
                          toolId: block.id,
                          input: block.input,
                          result: block.result,
                          status: block.status === 'complete' ? 'complete' : 'running',
                        })}
                        onRetry={handleRetry}
                        currentUserId={userId}
                      />
                    </div>
                  );
                })}

                {activeHumanTypers.length > 0 && (
                  <HumanTypingIndicator
                    typers={activeHumanTypers}
                    participants={conversationParticipants}
                  />
                )}
                {isThinking && <ThinkingIndicator />}

                {isWorkflowPolling && messages.length > 0 && !isThinking && (
                  <div className="group/msg flex items-center gap-3 px-5 -mx-5 py-1 text-surface-500">
                    <div className={`${CHAT_MSG_AVATAR} flex items-center justify-center`}>
                      <div className="w-4 h-4 border-2 border-surface-500 border-t-primary-500 rounded-full animate-spin" />
                    </div>
                    <span className="text-[15px] leading-[1.466]">Workflow running...</span>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>
            )}
            </div>

            {/* Scroll to bottom button */}
            {showScrollToBottom && (
              <button
                onClick={scrollToBottom}
                className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface-800 border border-surface-700 text-surface-300 hover:text-surface-100 hover:bg-surface-700 shadow-lg transition-all text-xs font-medium z-10"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                </svg>
                Scroll to bottom
              </button>
            )}
          </div>
        </div>

        {/* Artifact / attachment sidebar - overlay on mobile, sidebar on desktop */}
        {(currentArtifact || currentAttachmentId) && (
          <>
            {/* Mobile backdrop */}
            <div
              className="fixed inset-0 bg-black/50 z-40 md:hidden animate-fade-in"
              onClick={() => { setCurrentArtifactId(null); setCurrentAttachmentId(null); setCurrentAttachmentMeta(null); }}
            />
            <div className="fixed inset-y-0 right-0 w-full max-w-md z-50 animate-slide-in-right md:relative md:w-1/2 md:z-auto md:animate-none md:transition-all md:duration-300 md:ease-in-out border-l border-surface-800 bg-surface-900 p-4 overflow-y-auto">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-lg font-semibold text-surface-100 truncate">
                  {currentArtifact ? currentArtifact.title : (currentAttachmentMeta?.filename ?? 'Attachment')}
                </h2>
                <button
                  onClick={() => { setCurrentArtifactId(null); setCurrentAttachmentId(null); setCurrentAttachmentMeta(null); }}
                  className="text-surface-400 hover:text-surface-200 p-1 -mr-1"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              {currentArtifact ? (
                <ArtifactViewer artifact={currentArtifact} />
              ) : currentAttachmentId && currentAttachmentMeta ? (
                <ArtifactViewer
                  attachmentId={currentAttachmentId}
                  attachmentMeta={currentAttachmentMeta}
                />
              ) : null}
            </div>
          </>
        )}
      </div>

      {/* Composer */}
      <div className="px-3 md:px-5 pb-3 pt-1">
        <div className="relative">
          <div
            className={`absolute left-0 bottom-full mb-1 min-w-[14rem] max-w-sm max-h-44 overflow-y-auto rounded-lg border border-surface-700 bg-surface-900 shadow-xl z-50 ${
              mentionPopover.open && mentionSuggestions.length > 0 ? '' : 'hidden'
            }`}
            role="listbox"
          >
            {mentionSuggestions.slice(0, 8).map((item, idx) => (
              <button
                key={item.type === 'agent' ? 'agent' : item.userId}
                type="button"
                role="option"
                aria-selected={idx === mentionPopover.selectedIndex}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors ${
                  idx === mentionPopover.selectedIndex ? 'bg-primary-500/15 text-surface-100' : 'hover:bg-surface-800 text-surface-200'
                }`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  const displayName: string = item.type === 'agent' ? 'Basebase' : item.displayName;
                  selectMention(
                    item.type === 'agent' ? { type: 'agent' } : { type: 'user', userId: item.userId },
                    displayName
                  );
                }}
              >
                {item.type === 'agent' ? (
                  <span className="flex items-center gap-2 font-medium text-primary-400">
                    <svg className="w-4 h-4" viewBox="0 0 500 500" fill="none"><rect x="277.744" y="193.333" width="42.89" height="119.37" transform="rotate(45 277.744 193.333)" fill="currentColor"/><rect x="308.074" y="277.744" width="42.89" height="119.37" transform="rotate(135 308.074 277.744)" fill="currentColor"/><path d="M310.7 59.7c35.2-35.2 92.2-35.2 127.3 0s35.2 92.1 0 127.3c-42.4 42.4-162.6 35.3-162.6 35.3s-7.1-120.2 35.3-162.6zm66 29.6c-16.6 0-30 13.5-30 30 0 16.6 13.4 30 30 30s30-13.4 30-30c0-16.5-13.4-30-30-30z" fill="currentColor"/><path d="M59.7 187c-35.2-35.2-35.2-92.2 0-127.3s92.1-35.2 127.3 0c42.4 42.4 35.3 162.6 35.3 162.6s-120.2 7.1-162.6-35.3zm29.6-66c0 16.6 13.5 30 30 30 16.6 0 30-13.4 30-30s-13.4-30-30-30-30 13.4-30 30z" fill="currentColor"/><path d="M310.7 439.1c35.2 35.1 92.2 35.1 127.3 0s35.2-92.2 0-127.3c-42.4-42.4-162.5-35.4-162.6-35.4 0 0-7.1 120.2 35.3 162.7zm66-29.7c-16.6 0-30-13.4-30-30s13.4-30 30-30 30 13.4 30 30-13.4 30-30 30z" fill="currentColor"/><path d="M59.7 311.8c-35.2 35.2-35.2 92.2 0 127.3s92.1 35.2 127.3 0c42.4-42.4 35.3-162.5 35.3-162.6 0 0-120.2-7.1-162.6 35.3zm29.7 66c0-16.6 13.4-30 30-30s30 13.4 30 30-13.4 30-30 30-30-13.4-30-30z" fill="currentColor"/></svg>
                    Basebase
                  </span>
                ) : (
                  <span className="min-w-0 text-left">
                    <span className="font-medium">{item.displayName}</span>
                    {item.email.trim().toLowerCase() !== item.displayName.trim().toLowerCase() ? (
                      <span className="text-surface-500"> ({item.email})</span>
                    ) : null}
                  </span>
                )}
              </button>
            ))}
          </div>
          {outOfCredits && (
            <div className="mb-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm flex items-center gap-2">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              You&apos;re out of credits. Upgrade your plan to continue chatting.
            </div>
          )}
          {chatId && conversationState && conversationState.agentResponding === false && (
            <div className="mb-2 px-3 py-2 rounded-lg bg-surface-700/50 border border-surface-600 text-surface-400 text-sm">
              Basebase paused — use @Basebase to resume
            </div>
          )}
          {lowCredits && (
            <div className="mb-2 px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/30 text-yellow-300 text-sm flex items-center gap-2">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              Running low on credits ({creditsInfo?.balance} remaining).
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            accept="image/*,.pdf,.csv,.tsv,.xlsx,.docx,.pptx,.txt,.json,.md,.xml,.html,.css,.yaml,.yml,.rtf,.eml,.ics,.vcf,.sql,.log,.py,.js,.ts,.jsx,.tsx,.sh,.rb,.java,.c,.cpp,.h,.go,.rs,.swift,.kt,.r,.m"
            onChange={handleFileSelect}
          />

          {(() => {
            const composerExpanded: boolean =
              isMobile || composerFocused || input.trim().length > 0 || pendingAttachments.length > 0;

            const handleComposerBlur = (e: React.FocusEvent<HTMLDivElement>): void => {
              if (composerRef.current?.contains(e.relatedTarget as Node)) return;
              setComposerFocused(false);
            };

            const attachButton: JSX.Element = (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading || agentRunning}
                className="flex shrink-0 w-7 h-7 rounded text-surface-400 hover:text-surface-200 hover:bg-surface-700 items-center justify-center transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                title="Attach file"
              >
                {isUploading ? (
                  <div className="w-4 h-4 border-2 border-surface-600 border-t-primary-500 rounded-full animate-spin" />
                ) : (
                  <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                  </svg>
                )}
              </button>
            );

            const sendStopButton: JSX.Element = agentRunning ? (
              <button
                onClick={handleStop}
                className="shrink-0 w-7 h-7 rounded bg-red-600 text-white hover:bg-red-500 flex items-center justify-center transition-colors"
                title="Stop"
              >
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={(!input.trim() && pendingAttachments.length === 0) || !isConnected || outOfCredits}
                className={`shrink-0 w-7 h-7 rounded flex items-center justify-center transition-colors ${
                  (input.trim() || pendingAttachments.length > 0) && isConnected && !outOfCredits
                    ? 'bg-primary-600 text-white hover:bg-primary-500'
                    : 'text-surface-500 cursor-default'
                }`}
              >
                <svg className="w-[18px] h-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3 21l18-9L3 3l3 9zm0 0h8" />
                </svg>
              </button>
            );

            // Scope toggle: shown for new conversations and existing ones the user owns
            const isNewConversation: boolean = !chatId && !localConversationId;
            const activeScope: 'private' | 'shared' = isNewConversation ? newConversationScope : conversationScope;
            const showScopeToggle: boolean = isNewConversation || canToggleChatScope;
            const scopeToggle: JSX.Element | null = showScopeToggle ? (
              <div
                className="flex shrink-0 rounded border border-surface-600 p-px gap-px bg-surface-900"
                role="group"
                aria-label="Conversation visibility"
                onMouseDown={(e) => e.preventDefault()} // Prevent blur stealing click in Safari
              >
                <button
                  type="button"
                  disabled={scopeToggleSaving || activeScope === 'shared'}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    if (isNewConversation) { setNewConversationScope('shared'); }
                    else { void handleMakeShared(); }
                  }}
                  className={`flex items-center justify-center gap-0.5 px-1.5 py-0.5 rounded-l-[3px] text-[11px] font-medium transition-colors ${
                    activeScope === 'shared'
                      ? 'bg-primary-500/20 text-primary-400'
                      : 'text-surface-500 hover:bg-surface-800 hover:text-surface-300'
                  } disabled:opacity-40`}
                  title="Shared: teammates can join this conversation"
                >
                  Shared
                </button>
                <button
                  type="button"
                  disabled={scopeToggleSaving || activeScope === 'private'}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    if (isNewConversation) { setNewConversationScope('private'); }
                    else { void handleMakePrivate(); }
                  }}
                  className={`flex items-center justify-center gap-0.5 px-1.5 py-0.5 rounded-r-[3px] text-[11px] font-medium transition-colors ${
                    activeScope === 'private'
                      ? 'bg-primary-500/20 text-primary-400'
                      : 'text-surface-500 hover:bg-surface-800 hover:text-surface-300'
                  } disabled:opacity-40`}
                  title="Private: only you can see this conversation"
                >
                  <ScopeLockIcon className="w-3 h-3 shrink-0" />
                  Private
                </button>
              </div>
            ) : null;

            return (
              <div
                ref={composerRef}
                onDrop={(e) => void handleDrop(e)}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onBlur={handleComposerBlur}
                className={`relative rounded-lg border transition-all duration-150 ${
                  isDragOver
                    ? 'border-primary-500 ring-1 ring-primary-500/40 bg-surface-850'
                    : (!isConnected || outOfCredits)
                      ? 'border-surface-700 opacity-50 bg-surface-900'
                      : 'border-surface-600 focus-within:border-surface-500 bg-surface-900'
                } w-full min-w-0 overflow-hidden`}
              >
                {isDragOver && (
                  <div className="absolute inset-0 rounded-lg bg-primary-500/10 flex items-center justify-center z-10 pointer-events-none">
                    <span className="text-sm font-medium text-primary-400">Drop files here</span>
                  </div>
                )}

                {composerExpanded ? (
                  <>
                    {pendingAttachments.length > 0 && (
                      <div className="flex flex-wrap gap-2 px-3 pt-3">
                        {pendingAttachments.map((att) => (
                          <AttachmentCard
                            key={att.upload_id}
                            filename={att.filename}
                            mimeType={att.mime_type}
                            size={att.size}
                            onRemove={() => removeAttachment(att.upload_id)}
                          />
                        ))}
                      </div>
                    )}

                    <textarea
                      ref={inputRef}
                      value={input}
                      onChange={(e) => {
                        const val: string = e.target.value;
                        const cursor: number = e.target.selectionStart ?? val.length;
                        setInput(val);
                        e.target.style.height = 'auto';
                        e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`;

                        const textBefore: string = val.substring(0, cursor);
                        const lastAt: number = textBefore.lastIndexOf('@');
                        if (lastAt !== -1) {
                          const prevChar: string = lastAt > 0 ? (val[lastAt - 1] ?? ' ') : ' ';
                          if (/\s/.test(prevChar) || lastAt === 0) {
                            const query: string = textBefore.substring(lastAt + 1);
                            if (!query.includes(' ')) {
                              setMentionPopover(() => ({ open: true, query, selectedIndex: 0 }));
                              notifyTyping();
                              return;
                            }
                          }
                        }
                        setMentionPopover((prev) => (prev.open ? { ...prev, open: false } : prev));
                        notifyTyping();
                      }}
                      onKeyDown={handleKeyDown}
                      onPaste={(e) => void handlePaste(e)}
                      onFocus={() => setComposerFocused(true)}
                      placeholder={outOfCredits ? 'Out of credits — upgrade to continue' : agentRunning ? 'Agent working...' : 'Message...'}
                      className="w-full resize-none bg-transparent text-surface-100 px-3.5 pt-2.5 pb-2 text-[13px] placeholder-surface-500 focus:outline-none leading-[1.46] scrollbar-none disabled:cursor-not-allowed"
                      style={{ minHeight: '36px', maxHeight: '240px' }}
                      rows={1}
                      disabled={!isConnected || outOfCredits}
                      autoFocus={chatId === null}
                    />

                    <div className="flex items-center justify-between gap-2 border-t border-surface-700/60 px-1.5 py-1 min-w-0">
                      <div className="flex min-w-0 items-center gap-0.5">
                        {attachButton}
                        {scopeToggle && (
                          <>
                            <div className="w-px h-4 bg-surface-700 mx-0.5" />
                            {scopeToggle}
                          </>
                        )}
                      </div>
                      {sendStopButton}
                    </div>
                  </>
                ) : (
                  <div className="flex items-center gap-1 px-1.5 py-1 min-w-0">
                    <div className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
                      {attachButton}
                      {scopeToggle && (
                        <>
                          <div className="w-px h-4 bg-surface-700 mx-0.5 shrink-0" />
                          {scopeToggle}
                        </>
                      )}
                      <textarea
                        ref={inputRef}
                        value={input}
                        onChange={(e) => {
                          const val: string = e.target.value;
                          const cursor: number = e.target.selectionStart ?? val.length;
                          setInput(val);
                          e.target.style.height = 'auto';
                          e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`;

                          const textBefore: string = val.substring(0, cursor);
                          const lastAt: number = textBefore.lastIndexOf('@');
                          if (lastAt !== -1) {
                            const prevChar: string = lastAt > 0 ? (val[lastAt - 1] ?? ' ') : ' ';
                            if (/\s/.test(prevChar) || lastAt === 0) {
                              const query: string = textBefore.substring(lastAt + 1);
                              if (!query.includes(' ')) {
                                setMentionPopover(() => ({ open: true, query, selectedIndex: 0 }));
                                notifyTyping();
                                return;
                              }
                            }
                          }
                          setMentionPopover((prev) => (prev.open ? { ...prev, open: false } : prev));
                          notifyTyping();
                        }}
                        onKeyDown={handleKeyDown}
                        onPaste={(e) => void handlePaste(e)}
                        onFocus={() => setComposerFocused(true)}
                        placeholder={outOfCredits ? 'Out of credits — upgrade to continue' : agentRunning ? 'Agent working...' : 'Message...'}
                        className="flex-1 min-w-0 resize-none bg-transparent text-surface-100 py-1 text-[13px] placeholder-surface-500 focus:outline-none leading-[1.46] scrollbar-none disabled:cursor-not-allowed"
                        style={{ height: '28px' }}
                        rows={1}
                        disabled={!isConnected || outOfCredits}
                        autoFocus={chatId === null}
                      />
                    </div>
                    {sendStopButton}
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>

      {/* Tool Call Detail Modal */}
      {selectedToolCall && (
        <ToolCallModal 
          toolCall={selectedToolCall} 
          onClose={() => setSelectedToolCall(null)} 
        />
      )}

      {/* Invite Participant Modal */}
      {showInviteModal && chatId && userId && (
        <InviteParticipantModal
          conversationId={chatId}
          teamMembers={teamMembersData?.members ?? []}
          existingParticipantIds={new Set(conversationParticipants.map((p) => p.id))}
          currentUserId={userId}
          onClose={() => setShowInviteModal(false)}
          onParticipantsAdded={(participants) => {
            setConversationParticipants((prev) => {
              const seen: Set<string> = new Set(prev.map((p) => p.id));
              const merged: typeof prev = [...prev];
              for (const p of participants) {
                if (!seen.has(p.id)) {
                  seen.add(p.id);
                  merged.push(p);
                }
              }
              return merged;
            });
            // Clear any suggested invites since participants list has changed
            useChatStore.getState().clearConversationSuggestedInvites(chatId);
          }}
        />
      )}
    </div>
  );
}

type InvitedParticipant = {
  id: string;
  name: string | null;
  email: string;
  avatarUrl?: string | null;
};

/**
 * Modal for adding teammates to a private conversation (multi-select, search).
 */
function InviteParticipantModal({
  conversationId,
  teamMembers,
  existingParticipantIds,
  currentUserId,
  onClose,
  onParticipantsAdded,
}: {
  conversationId: string;
  teamMembers: readonly TeamMember[];
  existingParticipantIds: ReadonlySet<string>;
  currentUserId: string;
  onClose: () => void;
  onParticipantsAdded: (participants: InvitedParticipant[]) => void;
}): JSX.Element {
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const selectableMembers: readonly TeamMember[] = useMemo(() => {
    const q: string = searchQuery.trim().toLowerCase();
    const filtered: TeamMember[] = teamMembers.filter(
      (member) => member.id !== currentUserId && !existingParticipantIds.has(member.id),
    );
    const matched: TeamMember[] = filtered.filter((member) => {
      if (q.length === 0) return true;
      const displayName: string = (member.name ?? '').toLowerCase();
      return displayName.includes(q) || member.email.toLowerCase().includes(q);
    });
    return [...matched].sort((a, b) => {
      const an: string = (a.name ?? a.email).toLowerCase();
      const bn: string = (b.name ?? b.email).toLowerCase();
      return an.localeCompare(bn);
    });
  }, [teamMembers, existingParticipantIds, currentUserId, searchQuery]);

  const toggleSelected = useCallback((memberId: string): void => {
    setSelectedIds((prev) => {
      const next: Set<string> = new Set(prev);
      if (next.has(memberId)) {
        next.delete(memberId);
      } else {
        next.add(memberId);
      }
      return next;
    });
    setError(null);
  }, []);

  const handleAddSelected = async (): Promise<void> => {
    if (selectedIds.size === 0) return;

    setIsLoading(true);
    setError(null);

    const added: InvitedParticipant[] = [];
    let firstFailure: string | null = null;

    for (const userId of selectedIds) {
      try {
        const { data, error: inviteError } = await apiRequest<{
          participant: { id: string; name: string | null; email: string; avatar_url?: string | null };
        }>(`/chat/conversations/${conversationId}/participants`, {
          method: 'POST',
          body: JSON.stringify({ user_id: userId }),
        });

        if (inviteError || !data?.participant) {
          firstFailure = firstFailure ?? inviteError ?? 'Failed to add participant';
          continue;
        }

        added.push({
          id: data.participant.id,
          name: data.participant.name,
          email: data.participant.email,
          avatarUrl: data.participant.avatar_url,
        });
      } catch (err) {
        firstFailure =
          firstFailure ?? (err instanceof Error ? err.message : 'Failed to add participant');
      }
    }

    setIsLoading(false);

    if (added.length === 0) {
      setError(firstFailure ?? 'Failed to add participants');
      return;
    }

    onParticipantsAdded(added);

    if (firstFailure !== null && added.length < selectedIds.size) {
      setSelectedIds((prev) => {
        const next: Set<string> = new Set(prev);
        for (const p of added) {
          next.delete(p.id);
        }
        return next;
      });
      setError(`Some people could not be added. ${firstFailure}`);
      return;
    }

    onClose();
  };

  const selectedCount: number = selectedIds.size;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface-900 rounded-xl border border-surface-700 shadow-xl w-full max-w-md mx-4 max-h-[min(90vh,32rem)] flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700 flex-shrink-0">
          <h3 className="text-lg font-semibold text-surface-100">Add people</h3>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="p-4 flex flex-col min-h-0 flex-1">
          <label htmlFor="invite-teammate-search" className="block text-sm font-medium text-surface-300 mb-2">
            Search teammates
          </label>
          <input
            id="invite-teammate-search"
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filter by name or email…"
            autoComplete="off"
            className="w-full px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent flex-shrink-0"
          />
          <div className="mt-3 flex-1 min-h-0 overflow-y-auto rounded-lg border border-surface-700 bg-surface-850">
            {selectableMembers.length === 0 ? (
              <p className="px-3 py-4 text-sm text-surface-500 text-center">
                No teammates match your search.
              </p>
            ) : (
              <ul className="divide-y divide-surface-800" role="listbox" aria-multiselectable="true">
                {selectableMembers.map((member) => {
                  const checked: boolean = selectedIds.has(member.id);
                  const label: string = member.name?.trim() || member.email;
                  return (
                    <li key={member.id}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-2.5 hover:bg-surface-800/80">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleSelected(member.id)}
                          className="h-4 w-4 rounded border-surface-600 text-primary-600 focus:ring-primary-500 flex-shrink-0"
                        />
                        <Avatar user={member} size="sm" className="flex-shrink-0" />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium text-surface-200">{label}</div>
                          <div className="truncate text-xs text-surface-500">{member.email}</div>
                        </div>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          {error && <p className="mt-2 text-sm text-red-400 flex-shrink-0">{error}</p>}
          <p className="mt-2 text-xs text-surface-500 flex-shrink-0">
            Only members of your team can be added.
          </p>
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-surface-700 flex-shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-surface-100 hover:bg-surface-800 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleAddSelected()}
            disabled={selectedCount === 0 || isLoading}
            className="px-4 py-2 text-sm font-medium bg-primary-600 text-white rounded-lg hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading
              ? 'Adding…'
              : selectedCount === 0
                ? 'Add to conversation'
                : `Add ${selectedCount} ${selectedCount === 1 ? 'person' : 'people'}`}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Render a message with its content blocks (text + tool_use)
 */
function MessageWithBlocks({
  message,
  isGroupedWithPrevious = false,
  slackUserIdToName,
  toolApprovals,
  onArtifactClick,
  onAppClick,
  onAttachmentClick,
  onToolApprove,
  onToolCancel,
  onToolClick,
  onRetry,
  currentUserId,
}: {
  message: ChatMessage;
  isGroupedWithPrevious?: boolean;
  slackUserIdToName: ReadonlyMap<string, string>;
  toolApprovals: Map<string, { operationId: string; toolName: string; isProcessing: boolean; result: unknown }>;
  onArtifactClick: (artifact: AnyArtifact) => void;
  onAppClick: (app: AppBlock["app"]) => void;
  onAttachmentClick?: (id: string, meta: { filename: string; mimeType: string }) => void;
  onToolApprove: (operationId: string, options?: Record<string, unknown>) => void;
  onToolCancel: (operationId: string) => void;
  onToolClick: (block: ToolUseBlock) => void;
  onRetry?: () => void;
  currentUserId?: string | null;
}): JSX.Element {
  const blocks = message.contentBlocks ?? [];
  const isUser = message.role === 'user';
  const currentUser = useAppStore((s) => s.user);
  
  if (blocks.length === 0) {
    console.warn('[MessageWithBlocks] Empty contentBlocks for message:', message.id, message.role);
    return <></>;
  }

  const rowPad: string = isGroupedWithPrevious ? 'py-[3px]' : 'py-1';

  // For user messages, use the simple Message component (with attachment cards if any)
  if (isUser) {
    const textContent = blocks
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('');
    const attachments = blocks.filter(
      (b): b is AttachmentBlock => b.type === 'attachment',
    );
    
    // Own vs other human: for any scope, messages from another user must use message sender fields,
    // not the viewer's profile (private multi-participant used to wrongly show the logged-in user's avatar).
    const isOwnMessage: boolean =
      Boolean(currentUserId) && (!message.userId || message.userId === currentUserId);
    const showSenderInfo: boolean = !isOwnMessage && Boolean(message.userId);
    
    if (showSenderInfo) {
      const senderName = message.senderName ?? message.senderEmail ?? 'Unknown';
      const senderUser = {
        id: message.userId ?? 'unknown',
        name: message.senderName,
        email: message.senderEmail,
        avatarUrl: message.senderAvatarUrl,
      };
      
      return (
        <div className={`${CHAT_MSG_ROW} ${rowPad} animate-slide-up`}>
          {isGroupedWithPrevious ? (
            <div className={`${CHAT_MSG_AVATAR_SPACER}`} aria-hidden />
          ) : (
            <Avatar user={senderUser} size="md" className={CHAT_MSG_AVATAR} />
          )}

          <div className="flex-1 min-w-0 overflow-hidden">
            {!isGroupedWithPrevious && (
              <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0">
                <span className={CHAT_MSG_NAME}>{senderName}</span>
                <span className={CHAT_MSG_TIME}>
                  {message.timestamp.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
                </span>
              </div>
            )}
            <UserMessageTextWithMentions
              text={textContent}
              slackIdToName={slackUserIdToName}
              bodyClassName={isGroupedWithPrevious ? 'mt-0' : 'mt-px'}
            />
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-1.5">
                {attachments.map((att, i) => {
                  const attId: string | undefined =
                    att.attachmentId ?? (att as { attachment_id?: string }).attachment_id;
                  return (
                    <AttachmentCard
                      key={`att-${i}`}
                      filename={att.filename}
                      mimeType={att.mimeType}
                      size={att.size}
                      attachmentId={attId}
                      onClick={
                        attId && onAttachmentClick
                          ? () => onAttachmentClick(attId, { filename: att.filename, mimeType: att.mimeType })
                          : undefined
                      }
                    />
                  );
                })}
              </div>
            )}
          </div>
        </div>
      );
    }
    
    const meUser = currentUser
      ? {
          id: currentUser.id,
          name: currentUser.name ?? null,
          email: currentUser.email ?? null,
          avatarUrl: currentUser.avatarUrl ?? null,
        }
      : null;
    const displayName: string = currentUser?.name ?? currentUser?.email ?? 'You';
    return (
      <div className={`${CHAT_MSG_ROW} ${rowPad} animate-slide-up`}>
        {isGroupedWithPrevious ? (
          <div className={CHAT_MSG_AVATAR_SPACER} aria-hidden />
        ) : meUser ? (
          <Avatar user={meUser} size="md" className={CHAT_MSG_AVATAR} />
        ) : (
          <div className={`${CHAT_MSG_AVATAR} flex items-center justify-center bg-primary-500`}>
            <svg className="w-[18px] h-[18px] text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
          </div>
        )}

        <div className="flex-1 min-w-0 overflow-hidden">
          {!isGroupedWithPrevious && (
            <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0">
              <span className={CHAT_MSG_NAME}>{displayName}</span>
              <span className={CHAT_MSG_TIME}>
                {message.timestamp.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
              </span>
            </div>
          )}
          <UserMessageTextWithMentions
            text={textContent}
            slackIdToName={slackUserIdToName}
            bodyClassName={isGroupedWithPrevious ? 'mt-0' : 'mt-px'}
          />
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-1.5">
              {attachments.map((att, i) => {
                const attId: string | undefined =
                  att.attachmentId ?? (att as { attachment_id?: string }).attachment_id;
                return (
                  <AttachmentCard
                    key={`att-${i}`}
                    filename={att.filename}
                    mimeType={att.mimeType}
                    size={att.size}
                    attachmentId={attId}
                    onClick={
                      attId && onAttachmentClick
                        ? () => onAttachmentClick(attId, { filename: att.filename, mimeType: att.mimeType })
                        : undefined
                    }
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  }

  // For assistant messages, render blocks in order (interleaved)
  const lastTextIndex: number = blocks.reduce((lastIdx, block, idx) =>
    block.type === 'text' ? idx : lastIdx, -1);
  const firstTextBlockIndex: number = blocks.findIndex((b) => b.type === 'text');

  const renderToolBlock = (block: ToolUseBlock): JSX.Element => {
    // Check if this is a pending_approval response from any tool
    const result = block.result as Record<string, unknown> | undefined;
    const isPendingApproval = result?.type === 'pending_approval' || result?.status === 'pending_approval';
    
    if (isPendingApproval && result) {
      const operationId = result.operation_id as string;
      const toolName = (result.tool_name as string) || block.name;
      const approvalState = toolApprovals.get(operationId);
      
      // Check if we have a final result stored (completed/failed/canceled)
      const storedStatus = result?.status as string | undefined;
      const isFinalState = storedStatus && ['completed', 'failed', 'canceled', 'expired'].includes(storedStatus);
      
      const finalResult = isFinalState
        ? (result as unknown as ApprovalResult)
        : (approvalState?.result as ApprovalResult | null) ?? null;

      return (
        <div key={block.id} className="my-1">
          <PendingApprovalCard
            data={{
              type: 'pending_approval',
              status: (result.status as string) ?? 'pending_approval',
              operation_id: operationId,
              tool_name: toolName,
              preview: (result.preview as Record<string, unknown>) ?? {},
              message: (result.message as string) ?? '',
              target_system: result.target_system as string | undefined,
              record_type: result.record_type as string | undefined,
              operation: result.operation as string | undefined,
            }}
            onApprove={onToolApprove}
            onCancel={onToolCancel}
            isProcessing={approvalState?.isProcessing ?? false}
            result={finalResult}
          />
        </div>
      );
    }

    return (
      <ToolBlockIndicator
        key={block.id}
        block={block}
        onClick={() => onToolClick(block)}
      />
    );
  };

  return (
    <div className={`${CHAT_MSG_ROW} ${rowPad}`}>
      {isGroupedWithPrevious ? (
        <div className={CHAT_MSG_AVATAR_SPACER} aria-hidden />
      ) : (
        <img src={AGENT_AVATAR_PATH} alt={APP_NAME} className={`${CHAT_MSG_AVATAR} object-cover`} />
      )}

      <div className="flex-1 min-w-0 overflow-hidden -mt-px">
        {!isGroupedWithPrevious && (
          <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0">
            <span className={CHAT_MSG_NAME}>{APP_NAME}</span>
            <span className={CHAT_MSG_TIME}>
              {message.timestamp.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
            </span>
          </div>
        )}
        {(() => {
          const elements: JSX.Element[] = [];
          let toolRunStart: number = -1;

          const flushToolRun = (endBefore: number): void => {
            if (toolRunStart < 0) return;
            const toolBlocks: ToolUseBlock[] = [];
            for (let j = toolRunStart; j < endBefore; j++) {
              const b = blocks[j];
              if (b?.type !== 'tool_use') continue;
              const tb = b as ToolUseBlock;
              if (!tb.status && !tb.result) continue;
              toolBlocks.push(tb);
            }
            if (toolBlocks.length > 0) {
              elements.push(
                <div key={`tools-${toolRunStart}`} className="flex flex-wrap items-center gap-1 my-1">
                  {toolBlocks.map((tb) => renderToolBlock(tb))}
                </div>,
              );
            }
            toolRunStart = -1;
          };

          for (let index = 0; index < blocks.length; index++) {
            const block = blocks[index]!;

            if (block.type === 'tool_use') {
              const tb = block as ToolUseBlock;
              if (!tb.status && !tb.result) continue;
              if (toolRunStart < 0) toolRunStart = index;
              continue;
            }

            if (block.type === 'thinking' && !block.text && !block.isStreaming) continue;
            if (block.type === 'text' && !block.text?.trim()) continue;

            flushToolRun(index);

            if (block.type === 'thinking') {
              elements.push(
                <div key={`thinking-${index}`} className={index > 0 ? 'mt-1' : ''}>
                  <ThinkingBlockIndicator block={block} />
                </div>,
              );
            } else if (block.type === 'text') {
              const isLast: boolean = index === lastTextIndex;
              const textTop: string =
                index > 0
                  ? 'mt-1'
                  : isGroupedWithPrevious && index === firstTextBlockIndex
                    ? 'mt-0'
                    : 'mt-px';
              elements.push(
                <div key={`text-${index}`} className={textTop}>
                  <AssistantTextBlock
                    text={block.text}
                    isStreaming={isLast && message.isStreaming}
                    slackUserIdToName={slackUserIdToName}
                  />
                </div>,
              );
            } else if (block.type === 'error') {
              elements.push(
                <div key={`error-${index}`} className="my-0.5">
                  <ErrorBlockIndicator block={block} onRetry={onRetry} />
                </div>,
              );
            } else if (block.type === 'artifact') {
              elements.push(
                <div key={`artifact-${block.artifact.id}`} className="my-2">
                  <ArtifactTile artifact={block.artifact} onClick={() => onArtifactClick(block.artifact)} />
                </div>,
              );
            } else if (block.type === 'app') {
              elements.push(
                <div key={`app-${block.app.id}`} className="my-2">
                  <AppTile app={block.app} onClick={() => onAppClick(block.app)} />
                </div>,
              );
            }
          }
          flushToolRun(blocks.length);
          return elements;
        })()}
      </div>
    </div>
  );
}

/**
 * Collapsible thinking block — shows Claude's reasoning process.
 * Open (expanded) while streaming with capped height and internal scroll;
 * auto-collapses when thinking completes and the next content appears.
 */
function ThinkingBlockIndicator({
  block,
}: {
  block: ThinkingBlockType;
}): JSX.Element {
  const [collapsed, setCollapsed] = useState<boolean>(!block.isStreaming);

  // Auto-close when thinking finishes so the next message takes focus
  useEffect(() => {
    if (!block.isStreaming) {
      setCollapsed(true);
    }
  }, [block.isStreaming]);

  // While streaming, keep expanded so the user sees the thinking box open
  const isExpanded: boolean = block.isStreaming || !collapsed;

  return (
    <div className="my-1">
      <button
        onClick={() => setCollapsed((prev) => !prev)}
        className="flex items-center gap-1 text-xs text-surface-500 hover:text-surface-300 transition-colors cursor-pointer"
      >
        <svg
          className={`w-3 h-3 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span>
          {block.isStreaming
            ? `Thinking${block.text.length > 0 ? ` (${formatStreamingChars(block.text.length)})` : ''}…`
            : `Thought process (${formatStreamingChars(block.text.length)})`}
        </span>
        {block.isStreaming && (
          <span className="inline-block w-1 h-1 rounded-full bg-primary-400 animate-pulse" />
        )}
      </button>
      {isExpanded && (
        <div className="mt-1 ml-4 pl-2 border-l border-surface-700 text-xs text-surface-500 whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto">
          {block.text || (block.isStreaming ? '' : null)}
        </div>
      )}
    </div>
  );
}

/** Renders Slack-style @mentions and tight paragraph spacing (bypasses typography plugin defaults). */
const ASSISTANT_MARKDOWN_COMPONENTS: Components = {
  p({ children }) {
    return (
      <p className="!mt-0 !mb-1.5 text-[15px] leading-[1.466] text-surface-100 dark:text-surface-200 last:!mb-0">
        {children}
      </p>
    );
  },
  a({ href, children, node, ...rest }) {
    void node;
    if (href?.startsWith('mention:')) {
      return (
        <span className="inline-flex items-center rounded bg-primary-500/15 text-primary-700 dark:text-primary-300 px-1 py-px text-[14px] font-semibold align-baseline mx-px">
          {children}
        </span>
      );
    }
    return (
      <a href={href} rel="noopener noreferrer" target="_blank" {...rest}>
        {children}
      </a>
    );
  },
};

/**
 * Assistant text block - renders markdown without avatar (avatar is at parent level)
 */
function AssistantTextBlock({
  text,
  isStreaming,
  slackUserIdToName,
}: {
  text: string;
  isStreaming?: boolean;
  slackUserIdToName: ReadonlyMap<string, string>;
}): JSX.Element {
  const raw: string = isStreaming ? text.trimEnd() : text;
  const displayText: string = useMemo(() => {
    const withMentions: string = preprocessSlackMentionsForMarkdown(raw, slackUserIdToName);
    return collapseExcessiveMarkdownBlankLines(withMentions);
  }, [raw, slackUserIdToName]);

  /** Tight Slack-like vertical rhythm: single-direction margins, no empty-p gaps, smaller heading/table gaps than default prose-sm. */
  const proseBody: string = [
    'prose prose-sm max-w-none overflow-x-auto',
    '[&>:first-child]:mt-0 [&>:last-child]:mb-0',
    '[&_p:empty]:hidden',
    '[&>p+p]:!mt-0',
    'prose-headings:scroll-mt-4 prose-headings:font-extrabold prose-headings:text-surface-50 dark:prose-headings:text-surface-50',
    'prose-h1:mt-0 prose-h1:mb-2 prose-h2:mt-3 prose-h2:mb-1 prose-h3:mt-2 prose-h3:mb-1 prose-h4:mt-2 prose-h4:mb-1',
    'prose-ul:mt-0 prose-ul:mb-2 prose-ol:mt-0 prose-ol:mb-2 prose-ul:pl-5 prose-ol:pl-5',
    'prose-li:my-0 prose-li:py-0.5 prose-li:text-surface-100 dark:prose-li:text-surface-200',
    'prose-pre:my-2',
    'prose-code:text-primary-600 dark:prose-code:text-primary-300 prose-code:bg-surface-800 dark:prose-code:bg-surface-800/70 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs',
    'prose-pre:bg-surface-800 dark:prose-pre:bg-surface-800/70 prose-pre:text-xs prose-pre:border prose-pre:border-surface-300 dark:prose-pre:border-surface-700',
    'prose-strong:text-surface-50 dark:prose-strong:text-surface-100',
    'prose-table:mt-0 prose-table:mb-2 prose-table:text-[15px] prose-table:text-surface-100 dark:prose-table:text-surface-200',
    'prose-th:text-surface-50 dark:prose-th:text-surface-200 prose-th:bg-surface-800/80 dark:prose-th:bg-surface-700/50 prose-th:px-2 prose-th:py-1',
    'prose-td:text-surface-100 dark:prose-td:text-surface-200 prose-td:px-2 prose-td:py-1 prose-td:border-surface-300 dark:prose-td:border-surface-700 prose-th:border-surface-300 dark:prose-th:border-surface-700',
    '[&_a]:text-primary-600 dark:[&_a]:text-primary-400 [&_a:hover]:text-primary-500 dark:[&_a:hover]:text-primary-300',
  ].join(' ');

  return (
    <div className="max-w-full text-[15px] leading-[1.466] text-surface-100 dark:text-surface-200">
      <div className={`${proseBody} ${isStreaming ? '[&>p:last-of-type]:inline [&>p:last-of-type]:mb-0' : ''}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={ASSISTANT_MARKDOWN_COMPONENTS}>
          {displayText}
        </ReactMarkdown>
        {isStreaming && (
          <span className="inline-block w-1.5 h-3 bg-surface-600 dark:bg-surface-200 animate-pulse ml-0.5 align-middle" />
        )}
      </div>
    </div>
  );
}

/**
 * Tool block indicator - clickable to show details
 */
function ToolBlockIndicator({
  block,
  onClick,
}: {
  block: ToolUseBlock;
  onClick: () => void;
}): JSX.Element {
  const isComplete: boolean = block.status === 'complete';
  const hasError: boolean = isComplete && !!(block.result as Record<string, unknown> | undefined)?.error;
  const statusText: string = getToolStatusText(
    block.name,
    block.input,
    isComplete,
    block.result,
    block.statusText,
  );

  const pillBg: string = isComplete
    ? hasError
      ? 'bg-red-500/10 dark:bg-red-500/10 border-red-400/30 dark:border-red-400/20'
      : 'bg-surface-800 dark:bg-surface-800/60 border-surface-200/80 dark:border-surface-700'
    : 'bg-primary-500/10 dark:bg-primary-500/10 border-primary-400/30 dark:border-primary-500/20';

  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] leading-tight transition-colors cursor-pointer group ${pillBg} hover:brightness-95 dark:hover:brightness-110`}
    >
      {isComplete ? (
        hasError ? (
          <svg className="w-3 h-3 text-red-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        ) : (
          <svg className="w-3 h-3 text-green-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
          </svg>
        )
      ) : (
        <svg className="w-3 h-3 text-primary-500 dark:text-primary-400 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      )}
      <span className={`truncate max-w-[200px] ${hasError ? 'text-red-400/90 dark:text-red-400/80' : isComplete ? 'text-surface-500 dark:text-surface-400' : 'text-primary-600 dark:text-primary-400'}`}>
        {statusText}
      </span>
    </button>
  );
}

/**
 * Error block indicator - shows errors in a compact, non-intrusive style
 */
function ErrorBlockIndicator({
  block,
  onRetry,
}: {
  block: ErrorBlock;
  onRetry?: () => void;
}): JSX.Element {
  // Parse the error message to extract a user-friendly summary
  const errorSummary = getErrorSummary(block.message);

  return (
    <div className="flex items-center gap-1.5 py-0.5 text-xs text-red-400/80">
      <svg className="w-3.5 h-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
      <span className="italic">{errorSummary}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-red-400 hover:text-red-300 underline ml-1"
        >
          Retry
        </button>
      )}
    </div>
  );
}

/**
 * Extract a user-friendly error summary from error messages
 */
function getErrorSummary(errorMessage: string): string {
  // Check for common error patterns
  if (errorMessage.includes('overloaded_error') || errorMessage.includes('Overloaded')) {
    return 'Service temporarily unavailable. Please try again.';
  }
  if (errorMessage.includes('rate_limit')) {
    return 'Rate limit reached. Please wait a moment and try again.';
  }
  if (
    errorMessage.includes('prompt is too long') ||
    errorMessage.includes('context window') ||
    (errorMessage.includes('exceeds') && errorMessage.includes('context'))
  ) {
    return 'This conversation is too long. Please start a new conversation.';
  }
  if (errorMessage.includes('timeout') || errorMessage.includes('Timeout')) {
    return 'Request timed out. Please try again.';
  }
  if (errorMessage.includes('connection') || errorMessage.includes('network')) {
    return 'Connection error. Please check your network and try again.';
  }
  
  // For other errors, truncate if too long
  const maxLength = 80;
  if (errorMessage.length > maxLength) {
    return errorMessage.slice(0, maxLength) + '...';
  }
  
  return errorMessage || 'An error occurred. Please try again.';
}

/**
 * Generate user-friendly status text for tool calls
 */
function formatStreamingChars(chars: number): string {
  if (chars < 1000) return `${chars} chars`;
  return `${(chars / 1000).toFixed(1)}k chars`;
}

function getToolStatusText(
  toolName: string,
  input: Record<string, unknown> | undefined,
  isComplete: boolean,
  result: Record<string, unknown> | undefined,
  statusTextFromBlock?: string,
): string {
  if (statusTextFromBlock != null && statusTextFromBlock.trim() !== "") {
    return statusTextFromBlock;
  }
  const streamingChars: number | undefined = typeof input?._streaming_chars === 'number' ? input._streaming_chars : undefined;

  switch (toolName) {
    case 'think': {
      return isComplete ? 'Thinking' : 'Thinking...';
    }
    case 'web_search': {
      const query = typeof input?.query === 'string' ? input.query : '';
      const truncatedQuery = query.length > 40 ? query.slice(0, 40) + '...' : query;
      if (isComplete) {
        const sources = Array.isArray(result?.sources) ? result.sources.length : 0;
        const sourceText = sources > 0 ? ` (${sources} source${sources === 1 ? '' : 's'})` : '';
        return `Searched the web for '${truncatedQuery}'${sourceText}`;
      }
      return `Searching the web for '${truncatedQuery}'...`;
    }
    case 'run_sql_query': {
      // Extract table names from the SQL query for a more descriptive message
      const query = typeof input?.query === 'string' ? input.query.toLowerCase() : '';
      const tableNames: string[] = [];
      const knownTables = [
        'deals', 'accounts', 'contacts', 'activities', 'integrations',
        'users', 'organizations', 'pipelines', 'pipeline_stages', 'meetings',
      ];
      for (const table of knownTables) {
        if (query.includes(table)) {
          tableNames.push(table === 'pipeline_stages' ? 'stages' : table);
        }
      }
      const tableDesc: string =
        tableNames.length > 0 ? tableNames.join(' and ') : 'synced data';

      if (isComplete) {
        if (result?.error) {
          return `Query to ${tableDesc} failed`;
        }
        const rowCount: number =
          typeof result?.row_count === 'number' ? result.row_count : 0;
        return `Queried ${tableDesc} (${rowCount} row${rowCount === 1 ? '' : 's'})`;
      }
      return `Querying ${tableDesc}...`;
    }
    case 'write_to_system_of_record': {
      const targetSystem = typeof input?.target_system === 'string' ? input.target_system : '';
      const recordType = typeof input?.record_type === 'string' ? input.record_type : 'record';
      const recordCount = Array.isArray(input?.records) ? input.records.length : 0;
      const systemLabel = targetSystem || 'system';
      if (recordCount === 0) {
        return `Preparing ${recordType}s for ${systemLabel}...`;
      }
      const pluralType = recordCount === 1 ? recordType : `${recordType}s`;
      const DIRECT_WRITE_THRESHOLD = 5;
      if (isComplete) {
        const verb = typeof input?.operation === 'string' && input.operation === 'update' ? 'Updated' : 'Created';
        return recordCount > DIRECT_WRITE_THRESHOLD
          ? `Prepared ${recordCount} ${pluralType} for review`
          : `${verb} ${recordCount} ${pluralType} in ${systemLabel}`;
      }
      return `Writing ${recordCount} ${pluralType} to ${systemLabel}...`;
    }
    case 'foreach': {
      const opName: string = typeof result?.operation_name === 'string'
        ? result.operation_name
        : (typeof result?.workflow_name === 'string'
          ? result.workflow_name
          : (typeof input?.operation_name === 'string' ? input.operation_name : 'foreach'));
      const total: number = typeof result?.total === 'number' ? result.total
        : (typeof result?.total_items === 'number' ? result.total_items
          : (Array.isArray(input?.items) ? input.items.length : 0));
      const completed: number = typeof result?.completed === 'number' ? result.completed : 0;
      const succeeded: number = typeof result?.succeeded === 'number' ? result.succeeded
        : (typeof result?.succeeded_items === 'number' ? result.succeeded_items : 0);
      const failed: number = typeof result?.failed === 'number' ? result.failed
        : (typeof result?.failed_items === 'number' ? result.failed_items : 0);

      if (isComplete) {
        if (failed > 0) {
          return `Completed ${opName}: ${succeeded}/${total} succeeded, ${failed} failed`;
        }
        return `Completed ${opName}: ${total} item${total === 1 ? '' : 's'} processed`;
      }

      if (total > 0) {
        const pct: number = Math.round((completed / total) * 100);
        const progressText: string = failed > 0
          ? `${completed}/${total} (${pct}%) — ${succeeded} ok, ${failed} failed`
          : `${completed}/${total} (${pct}%)`;
        return `Running ${opName}... ${progressText}`;
      }
      return `Running ${opName}...`;
    }
    case 'run_workflow': {
      const workflowName = typeof result?.workflow_name === 'string'
        ? result.workflow_name
        : (typeof input?.workflow_name === 'string' ? input.workflow_name : 'workflow');
      if (isComplete) {
        return `Completed ${workflowName}`;
      }
      return `Running ${workflowName}...`;
    }
    case 'get_connector_docs': {
      const docsConnector: string = typeof input?.connector === 'string' ? input.connector : '';
      const docsLabel: string = docsConnector
        ? docsConnector.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
        : 'connector';
      if (isComplete) {
        return result?.error ? `Failed to load ${docsLabel} docs` : `Completed ${docsLabel.toLowerCase()} docs`;
      }
      return `Loading ${docsLabel.toLowerCase()} docs...`;
    }
    case 'list_connected_connectors': {
      if (isComplete) {
        const connectorCount: number = Array.isArray(result?.connectors) ? result.connectors.length : 0;
        return `Found ${connectorCount} connected connector${connectorCount === 1 ? '' : 's'}`;
      }
      return 'Listing connected connectors...';
    }
    case 'query_on_connector': {
      const connectorSlug: string = typeof input?.connector === 'string' ? input.connector : '';
      const connectorLabel: string = connectorSlug
        ? connectorSlug.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
        : 'connector';
      if (isComplete) {
        if (result?.error) return `Query to ${connectorLabel} failed`;
        const count = typeof result?.count === 'number' ? result.count : (Array.isArray(result?.files) ? result.files.length : undefined);
        const isSingleFileRead: boolean = connectorSlug === 'google_drive' && result?.file_name != null && result?.content != null;
        if (count !== undefined && connectorSlug === 'google_drive') {
          return count === 1 ? `Read 1 file from ${connectorLabel}` : `Read ${count} files from ${connectorLabel}`;
        }
        if (isSingleFileRead) return `Read 1 file from ${connectorLabel}`;
        return `Queried ${connectorLabel}`;
      }
      return `Querying ${connectorLabel}...`;
    }
    case 'write_on_connector': {
      const writeConnector: string = typeof input?.connector === 'string' ? input.connector : '';
      const writeOp: string = typeof input?.operation === 'string' ? input.operation : 'write';
      const data: Record<string, unknown> = typeof input?.data === 'object' && input?.data !== null ? input.data as Record<string, unknown> : {};
      const errorMsg: string = typeof result?.error === 'string' ? result.error : '';
      if (streamingChars !== undefined) {
        return `Writing to connector (${formatStreamingChars(streamingChars)} generated)...`;
      }
      if (writeConnector === 'artifacts') {
        const artifactTitle: string = typeof data?.title === 'string' ? data.title : '';
        const titleSuffix: string = artifactTitle ? `: ${artifactTitle}` : '';
        if (isComplete) {
          if (errorMsg) return `Failed to create artifact${titleSuffix}`;
          return writeOp === 'update' ? `Updated artifact${titleSuffix}` : `Created artifact${titleSuffix}`;
        }
        return writeOp === 'update' ? `Updating artifact${titleSuffix}...` : `Creating artifact${titleSuffix}...`;
      }
      if (writeConnector === 'apps') {
        const appTitle: string = typeof data?.title === 'string' ? data.title : (typeof result?.title === 'string' ? result.title : 'app');
        if (writeOp === 'create') {
          if (isComplete) return result?.error ? 'Failed to create app' : `Created app: ${appTitle}`;
          return `Creating app: ${appTitle}...`;
        }
        if (writeOp === 'update') {
          if (isComplete) return result?.error ? 'Failed to update app' : 'Updated app';
          return 'Updating app...';
        }
        if (writeOp === 'test_query') {
          const queryName: string = typeof data?.query_name === 'string' ? data.query_name : 'query';
          const rowCount: number | undefined = typeof result?.row_count === 'number' ? result.row_count : undefined;
          if (isComplete) return result?.error ? 'Query test failed' : `Tested query "${queryName}" (${rowCount ?? 0} rows)`;
          return `Testing query "${queryName}"...`;
        }
      }
      const connectorLabel: string = writeConnector ? writeConnector.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase()) : 'connector';
      const opLabel: string = writeOp.replace(/_/g, ' ');
      if (isComplete) {
        return errorMsg ? `Write to ${connectorLabel} failed` : `Wrote to ${connectorLabel} (${opLabel})`;
      }
      return `Writing to ${connectorLabel} (${opLabel})...`;
    }
    case 'run_on_connector': {
      const actionConnector: string = typeof input?.connector === 'string' ? input.connector : '';
      const actionName: string = typeof input?.action === 'string' ? input.action : '';
      const connectorLabel: string = actionConnector ? actionConnector.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase()) : '';
      const actionLabel: string = actionName ? actionName.replace(/_/g, ' ') : '';
      if (connectorLabel && actionLabel) {
        if (isComplete) {
          return result?.error ? `Action on ${connectorLabel} failed` : `Ran ${actionLabel} on ${connectorLabel}`;
        }
        return `Running ${actionLabel} on ${connectorLabel}...`;
      }
      if (isComplete) {
        return result?.error ? 'Connector action failed' : 'Completed connector action';
      }
      return 'Running action (details when available)...';
    }
    default:
      if (streamingChars !== undefined) {
        return `Generating ${toolName} input (${formatStreamingChars(streamingChars)})...`;
      }
      return isComplete ? `Completed ${toolName}` : `Running ${toolName}...`;
  }
}

/**
 * Tool call indicator - clickable to show details
 */
/**
 * Modal for showing tool call details
 */
function ToolCallModal({ 
  toolCall, 
  onClose 
}: { 
  toolCall: ToolCallData; 
  onClose: () => void;
}): JSX.Element {
  const isComplete: boolean = toolCall.status === 'complete';
  const hasError: boolean = isComplete && !!(toolCall.result as Record<string, unknown> | undefined)?.error;
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div 
        className="bg-surface-900 border border-surface-700 rounded-xl max-w-2xl w-full max-h-[80vh] overflow-hidden shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-surface-700">
          <div className="flex items-center gap-3">
            {isComplete ? (
              hasError ? (
                <div className="w-8 h-8 rounded-lg bg-red-500/20 flex items-center justify-center">
                  <svg className="w-4 h-4 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
              ) : (
                <div className="w-8 h-8 rounded-lg bg-green-500/20 flex items-center justify-center">
                  <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
              )
            ) : (
              <div className="w-8 h-8 rounded-lg bg-primary-500/20 flex items-center justify-center">
                <svg className="w-4 h-4 text-primary-400 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              </div>
            )}
            <div>
              <h3 className="text-lg font-semibold text-surface-100">{toolCall.toolName}</h3>
              <p className={`text-sm ${hasError ? 'text-red-400' : 'text-surface-400'}`}>
                {isComplete ? (hasError ? 'Failed' : 'Completed') : 'Running...'}
              </p>
            </div>
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
        <div className="p-4 overflow-y-auto max-h-[calc(80vh-80px)] space-y-4">
          {/* Input */}
          <div>
            <h4 className="text-sm font-medium text-surface-300 mb-2">Input</h4>
            {toolCall.input && Object.keys(toolCall.input).length > 0 ? (
              <pre className="bg-surface-800 rounded-lg p-3 text-sm text-surface-200 overflow-x-auto">
                {JSON.stringify(toolCall.input, null, 2)}
              </pre>
            ) : (
              <p className="text-surface-500 text-sm italic">
                Parameters not yet available. They will appear when the request is fully received or after it completes.
              </p>
            )}
          </div>

          {/* Result */}
          {toolCall.result && (
            <div>
              <h4 className="text-sm font-medium text-surface-300 mb-2">Result</h4>
              <pre className="bg-surface-800 rounded-lg p-3 text-sm text-surface-200 overflow-x-auto max-h-96 overflow-y-auto">
                {JSON.stringify(toolCall.result, null, 2)}
              </pre>
            </div>
          )}

          {/* Tool ID for debugging */}
          <div className="text-xs text-surface-500 pt-2 border-t border-surface-800">
            Tool ID: {toolCall.toolId}
          </div>
        </div>
      </div>
    </div>
  );
}

interface HumanTypingParticipant {
  id: string;
  name: string | null;
  email: string;
  avatarUrl?: string | null;
}

/** Shows when another human is typing in a shared conversation (WebSocket `user_typing`). */
function HumanTypingIndicator({
  typers,
  participants,
}: {
  typers: Array<{ userId: string; name: string }>;
  participants: readonly HumanTypingParticipant[];
}): JSX.Element {
  const first: { userId: string; name: string } = typers[0]!;
  const p: HumanTypingParticipant | undefined = participants.find((x) => x.id === first.userId);
  const avatarUser = {
    id: first.userId,
    name: p?.name ?? first.name,
    email: p?.email ?? '',
    avatarUrl: p?.avatarUrl ?? null,
  };
  const extraCount: number = typers.length - 2;
  const label: string =
    typers.length === 1
      ? `${first.name} is typing…`
      : typers.length === 2
        ? `${typers[0]!.name} and ${typers[1]!.name} are typing…`
        : `${typers[0]!.name}, ${typers[1]!.name}, and ${extraCount} other${extraCount === 1 ? '' : 's'} are typing…`;

  return (
    <div className={`${CHAT_MSG_ROW} py-1`}>
      <Avatar user={avatarUser} size="md" className={CHAT_MSG_AVATAR} />
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0">
          <span className={CHAT_MSG_NAME}>{label}</span>
        </div>
        <span className="text-xs text-surface-400 animate-shimmer">Typing…</span>
      </div>
    </div>
  );
}

/**
 * Thinking indicator - shows while waiting for assistant response
 */
function ThinkingIndicator(): JSX.Element {
  return (
    <div className={`${CHAT_MSG_ROW} py-1`}>
      <img src={AGENT_AVATAR_PATH} alt={APP_NAME} className={`${CHAT_MSG_AVATAR} object-cover`} />

      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0">
          <span className={`${CHAT_MSG_NAME} !leading-none`}>{APP_NAME}</span>
        </div>
        <span className="mt-px text-xs leading-none text-surface-400 animate-shimmer">Getting ready…</span>
      </div>
    </div>
  );
}

/**
 * Generate a chat title from the first message.
 */
function generateTitle(message: string): string {
  const cleaned = message.trim().replace(/\n/g, ' ');

  if (cleaned.endsWith('?') && cleaned.length <= 50) {
    return cleaned;
  }

  const words = cleaned.split(' ').slice(0, 6);
  let title = words.join(' ');

  if (title.length > 40) {
    title = title.slice(0, 40);
  }

  if (cleaned.length > title.length) {
    title += '...';
  }

  return title || 'New Chat';
}

function ConnectionStatus({
  state,
}: {
  state: 'connecting' | 'connected' | 'disconnected' | 'error';
}): JSX.Element | null {
  if (state === 'connected') return null;

  const statusConfig = {
    connecting: { color: 'bg-yellow-500', text: 'Connecting...' },
    disconnected: { color: 'bg-surface-500', text: 'Disconnected' },
    error: { color: 'bg-red-500', text: 'Error' },
  };

  const config = statusConfig[state];

  return (
    <div className="flex items-center gap-2 text-sm text-surface-400">
      <div className={`w-2 h-2 rounded-full ${config.color}`} />
      <span>{config.text}</span>
    </div>
  );
}

/**
 * Get a short file-type label from a mime type or filename extension.
 */
function getFileTypeLabel(filename: string, mimeType: string): string {
  const ext: string = filename.split('.').pop()?.toLowerCase() ?? '';
  const extMap: Record<string, string> = {
    pdf: 'PDF', csv: 'CSV', tsv: 'TSV', xlsx: 'Excel', docx: 'Word', pptx: 'PowerPoint',
    json: 'JSON', md: 'Markdown', xml: 'XML', html: 'HTML', css: 'CSS', txt: 'Text',
    yaml: 'YAML', yml: 'YAML', rtf: 'RTF', eml: 'Email', ics: 'Calendar', vcf: 'Contact',
    sql: 'SQL', log: 'Log',
    py: 'Python', js: 'JavaScript', ts: 'TypeScript', jsx: 'JSX', tsx: 'TSX',
    sh: 'Shell', rb: 'Ruby', java: 'Java', c: 'C', cpp: 'C++', h: 'Header',
    go: 'Go', rs: 'Rust', swift: 'Swift', kt: 'Kotlin', r: 'R', m: 'Obj-C',
    png: 'PNG', jpg: 'JPEG', jpeg: 'JPEG', gif: 'GIF', webp: 'WebP', svg: 'SVG',
  };
  if (ext && ext in extMap) return extMap[ext] as string;
  if (mimeType.startsWith('image/')) return 'Image';
  if (mimeType.startsWith('text/')) return 'Text';
  return ext.toUpperCase() || 'File';
}

/**
 * Format file size in human-readable form.
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Loads a chat attachment image via authenticated fetch and shows a thumbnail (Slack-style).
 */
function ChatAttachmentImageThumbnail({
  attachmentId,
  mimeType,
}: {
  attachmentId: string;
  mimeType: string;
}): JSX.Element {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<boolean>(false);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!mimeType.startsWith('image/')) return;
    let cancelled = false;
    void (async () => {
      try {
        const hdrs = await getAuthenticatedRequestHeaders();
        const response: Response = await fetch(
          `${API_BASE}/chat/attachments/${encodeURIComponent(attachmentId)}`,
          { headers: hdrs },
        );
        if (!response.ok || cancelled) throw new Error('attachment fetch failed');
        const blob: Blob = await response.blob();
        if (cancelled) return;
        const nextUrl: string = URL.createObjectURL(blob);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = nextUrl;
        setObjectUrl(nextUrl);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };
  }, [attachmentId, mimeType]);

  if (!mimeType.startsWith('image/') || failed) {
    return <></>;
  }
  if (!objectUrl) {
    return (
      <div
        className="w-full aspect-[4/3] rounded-md bg-surface-700/80 animate-pulse border border-surface-600"
        aria-hidden
      />
    );
  }
  return (
    <img
      src={objectUrl}
      alt=""
      className="w-full aspect-[4/3] rounded-md object-cover object-left-top"
    />
  );
}

/**
 * File-type icon color based on extension.
 */
function getFileIconColor(filename: string, mimeType: string): string {
  const ext: string = filename.split('.').pop()?.toLowerCase() ?? '';
  if (['csv', 'xlsx', 'xls'].includes(ext)) return 'bg-emerald-700 text-emerald-200';
  if (ext === 'pdf') return 'bg-red-800 text-red-200';
  if (ext === 'json') return 'bg-yellow-800 text-yellow-200';
  if (mimeType.startsWith('image/')) return 'bg-violet-800 text-violet-200';
  return 'bg-surface-700 text-surface-300';
}

/**
 * Attachment card — used in both pending input and sent message bubbles.
 * Pass `onRemove` to show a dismiss button (for pending attachments).
 * Pass `attachmentId` + `onClick` to make the card clickable (view attachment).
 */
function AttachmentCard({
  filename,
  mimeType,
  size,
  onRemove,
  attachmentId,
  onClick,
}: {
  filename: string;
  mimeType: string;
  size: number;
  onRemove?: () => void;
  attachmentId?: string | null;
  onClick?: () => void;
}): JSX.Element {
  const label: string = getFileTypeLabel(filename, mimeType);
  const sizeStr: string = formatFileSize(size);
  const iconColor: string = getFileIconColor(filename, mimeType);
  const isClickable: boolean = Boolean(attachmentId && onClick);
  const showImageThumb: boolean = mimeType.startsWith('image/') && Boolean(attachmentId);

  const clickHandler = isClickable ? onClick : undefined;
  const keyHandler =
    isClickable && onClick
      ? (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick();
          }
        }
      : undefined;

  // Full-bleed image card: image fills the tile, filename overlaid
  if (showImageThumb && attachmentId) {
    return (
      <div
        className={`relative group rounded-xl overflow-hidden border border-surface-700 w-[180px] ${isClickable ? "cursor-pointer hover:border-surface-500 transition-colors" : ""}`}
        role={isClickable ? "button" : undefined}
        tabIndex={isClickable ? 0 : undefined}
        onClick={clickHandler}
        onKeyDown={keyHandler}
      >
        <ChatAttachmentImageThumbnail attachmentId={attachmentId} mimeType={mimeType} />
        {/* Filename overlay with readable text treatment */}
        <div className="absolute inset-x-0 bottom-0 px-2 py-1.5 bg-gradient-to-t from-black/70 to-transparent">
          <span
            className="text-[11px] font-medium text-white truncate block"
            style={{ textShadow: '0 1px 3px rgba(0,0,0,0.8)' }}
          >
            {filename}
          </span>
          <span
            className="text-[9px] text-white/70 block"
            style={{ textShadow: '0 1px 2px rgba(0,0,0,0.8)' }}
          >
            {sizeStr}
          </span>
        </div>
        {onRemove && (
          <button
            type="button"
            onClick={(e: React.MouseEvent) => { e.stopPropagation(); onRemove(); }}
            className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-surface-700 border border-surface-600 text-surface-400 hover:text-surface-100 hover:bg-surface-600 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>
    );
  }

  // Non-image attachment card (unchanged layout)
  return (
    <div
      className={`relative group inline-flex items-center gap-2.5 rounded-xl bg-surface-800 border border-surface-700 px-3 py-2 max-w-[220px]${isClickable ? " cursor-pointer hover:bg-surface-750 hover:border-surface-600 transition-colors" : ""}`}
      role={isClickable ? "button" : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onClick={clickHandler}
      onKeyDown={keyHandler}
    >
      <div className={`flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center text-[10px] font-bold ${iconColor}`}>
        {label}
      </div>
      <div className="min-w-0 flex flex-col">
        <span className="text-xs text-surface-200 font-medium truncate">{filename}</span>
        <span className="text-[10px] text-surface-500">{sizeStr}</span>
      </div>
      {onRemove && (
        <button
          type="button"
          onClick={(e: React.MouseEvent) => { e.stopPropagation(); onRemove(); }}
          className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-surface-700 border border-surface-600 text-surface-400 hover:text-surface-100 hover:bg-surface-600 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  );
}

function buildSuggestions(connected: Integration[]): string[] {
  const providers = new Set(connected.map((i) => i.provider));
  const suggestions: string[] = [];

  if (providers.has('hubspot') || providers.has('salesforce'))
    suggestions.push('What deals are closing this month?', 'Show me my pipeline by stage');
  if (providers.has('gmail') || providers.has('microsoft_mail'))
    suggestions.push('Summarize my unread emails from today');
  if (providers.has('google_calendar') || providers.has('microsoft_calendar') || providers.has('zoom'))
    suggestions.push('What meetings do I have this week?');
  if (providers.has('github') || providers.has('linear') || providers.has('jira') || providers.has('asana'))
    suggestions.push('Show me open issues assigned to me');
  if (providers.has('slack'))
    suggestions.push('What are the latest messages in my Slack channels?');

  if (suggestions.length < 3)
    return ['What can you help me with?', 'What data sources can I connect?', 'Show me what you can do'];

  return suggestions.slice(0, 5);
}

interface EmptyStateProps {
  onSuggestionClick: (text: string) => void;
}

function EmptyState({ onSuggestionClick }: EmptyStateProps): JSX.Element {
  const connected = useConnectedIntegrations();
  const suggestions = buildSuggestions(connected);

  return (
    <div className="h-full flex items-center justify-center px-4">
      <div className="text-center max-w-lg">
        <div className="w-16 h-16 md:w-20 md:h-20 rounded-2xl bg-primary-500/10 flex items-center justify-center mx-auto mb-4 md:mb-6">
          <img 
            src={LOGO_PATH} 
            alt={APP_NAME} 
            className="w-8 h-8 md:w-10 md:h-10" 
          />
        </div>
        <h2 className="text-xl md:text-2xl font-bold text-surface-50 mb-2">
          Ask me anything
        </h2>
        <p className="text-surface-400 mb-6 md:mb-8 text-sm md:text-base">
          Get instant insights from your connected data sources
        </p>
        <div className="flex flex-wrap gap-2 justify-center">
          {suggestions.map((text) => (
            <button
              key={text}
              onClick={() => onSuggestionClick(text)}
              className="px-3 md:px-4 py-1.5 md:py-2 rounded-full bg-surface-800 hover:bg-surface-700 text-surface-300 text-xs md:text-sm transition-colors border border-surface-700"
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
