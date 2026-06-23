const DEMO_CONVERSATION_KEY = "clinic_demo_conversation_id";

export function loadDemoConversationId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return sessionStorage.getItem(DEMO_CONVERSATION_KEY);
}

export function saveDemoConversationId(conversationId: string): void {
  sessionStorage.setItem(DEMO_CONVERSATION_KEY, conversationId);
}

export function clearDemoConversationId(): void {
  sessionStorage.removeItem(DEMO_CONVERSATION_KEY);
}
