import type { ChatMessage } from "../store";

export type CrossTabEvent =
  | {
      origin: string;
      kind: "ws-event";
      payload: { message: string };
    }
  | {
      origin: string;
      kind: "optimistic_message";
      payload: {
        conversationId: string;
        message: ChatMessage;
        setThinking: boolean;
      };
    };

const channelName = "revtops-chat-sync";
const isBrowser =
  typeof window !== "undefined" && typeof window.BroadcastChannel !== "undefined";
const channel = isBrowser ? new BroadcastChannel(channelName) : null;
const tabId =
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

export const crossTab = {
  isAvailable: Boolean(channel),
  tabId,
  postMessage(event: Omit<CrossTabEvent, "origin">): void {
    if (!channel) {
      return;
    }
    channel.postMessage({ ...event, origin: tabId });
  },
};

export function subscribeCrossTab(
  handler: (event: CrossTabEvent) => void,
): () => void {
  if (!channel) {
    return () => {};
  }

  const listener = (event: MessageEvent<CrossTabEvent>): void => {
    const data = event.data;
    if (!data || data.origin === tabId) {
      return;
    }
    handler(data);
  };

  channel.addEventListener("message", listener);
  return () => channel.removeEventListener("message", listener);
}
