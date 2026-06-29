"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  sendChatMessage,
  toSafeChatErrorMessage,
} from "@/lib/api-client";
import { DemoDisclaimer } from "@/components/demo-disclaimer";
import {
  clearDemoConversationId,
  loadDemoConversationId,
  saveDemoConversationId,
} from "@/lib/demo-session";

const SUGGESTED_PROMPTS = [
  "I need a dermatology appointment next Tuesday morning",
  "Show me available times tomorrow afternoon",
  "Can I book an appointment on Sunday?",
  "I want to reschedule my appointment",
  "Cancel my appointment",
  "This is an emergency",
] as const;

const MIN_TYPING_INDICATOR_MS = 700;

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  variant?: "error";
};

type ChatPanelProps = {
  onExit: () => void;
};

function TypingIndicator() {
  return (
    <div className="flex justify-start" aria-live="polite" aria-label="Receptionist is typing">
      <div className="flex max-w-[85%] items-center gap-2 rounded-2xl rounded-bl-md bg-zinc-100 px-4 py-3 text-sm text-zinc-500">
        <span></span>
        <span className="inline-flex items-end gap-0.5 pb-0.5" aria-hidden="true">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:0ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:150ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:300ms]" />
        </span>
      </div>
    </div>
  );
}

function waitForMinimumTypingDuration(startedAt: number): Promise<void> {
  const elapsed = Date.now() - startedAt;
  const remaining = MIN_TYPING_INDICATOR_MS - elapsed;
  if (remaining <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    setTimeout(resolve, remaining);
  });
}

export function ChatPanel({ onExit }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(() =>
    loadDemoConversationId(),
  );
  const [isLoading, setIsLoading] = useState(false);
  const [sessionRestored, setSessionRestored] = useState(
    () => loadDemoConversationId() !== null,
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const localMessageIdRef = useRef(0);
  const isSubmittingRef = useRef(false);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  async function submitMessage(message: string) {
    const trimmed = message.trim();
    if (!trimmed || isLoading || isSubmittingRef.current) {
      return;
    }

    isSubmittingRef.current = true;
    const typingStartedAt = Date.now();

    localMessageIdRef.current += 1;
    const userMessageId = `user-${localMessageIdRef.current}`;

    setMessages((current) => [
      ...current,
      {
        id: userMessageId,
        role: "user",
        content: trimmed,
      },
    ]);
    setInput("");
    setIsLoading(true);

    try {
      const response = await sendChatMessage({
        message: trimmed,
        conversation_id: conversationId,
        conversation_metadata: conversationId
          ? {}
          : { source: "public_demo" },
      });

      await waitForMinimumTypingDuration(typingStartedAt);

      saveDemoConversationId(response.conversation_id);
      setConversationId(response.conversation_id);
      setSessionRestored(false);

      setMessages((current) => [
        ...current,
        {
          id: response.assistant_message_id,
          role: "assistant",
          content: response.reply,
        },
      ]);
    } catch (submitError) {
      if (
        submitError instanceof ApiClientError &&
        submitError.code === "conversation_not_found"
      ) {
        clearDemoConversationId();
        setConversationId(null);
      }

      localMessageIdRef.current += 1;
      setMessages((current) => [
        ...current,
        {
          id: `error-${localMessageIdRef.current}`,
          role: "assistant",
          content: toSafeChatErrorMessage(submitError),
          variant: "error",
        },
      ]);
      setInput(trimmed);
    } finally {
      isSubmittingRef.current = false;
      setIsLoading(false);
      inputRef.current?.focus();
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitMessage(input);
  }

  return (
    <section className="mx-auto flex h-full w-full max-w-3xl flex-1 flex-col px-4 py-6 sm:px-6">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-white-900">Demo chat</h2>
          <p className="text-sm text-white-500">
            Messages are handled by the backend receptionist API.
          </p>
        </div>
        <button
          type="button"
          onClick={onExit}
          className="inline-flex h-10 shrink-0 items-center justify-center rounded-full border border-zinc-300 bg-white px-4 text-sm font-medium text-zinc-700 transition-colors hover:border-zinc-400 hover:bg-zinc-50"
        >
          Exit chat
        </button>
      </div>

      <div className="mb-4">
        <DemoDisclaimer />
      </div>

      <div className="flex min-h-[20rem] flex-1 flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm">
        <div className="flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          {sessionRestored && messages.length === 0 ? (
            <p className="text-sm text-zinc-500">
              Session restored. Send a message to continue the conversation.
            </p>
          ) : null}

          {messages.length === 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-zinc-500">Try one of these prompts:</p>
              <div className="flex flex-wrap gap-2">
                {SUGGESTED_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    disabled={isLoading}
                    onClick={() => void submitMessage(prompt)}
                    className="rounded-full border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-left text-sm text-zinc-700 transition-colors hover:border-teal-200 hover:bg-teal-50 hover:text-teal-900 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {messages.map((message) => (
            <div
              key={message.id}
              className={
                message.role === "user" ? "flex justify-end" : "flex justify-start"
              }
            >
              <div
                className={
                  message.role === "user"
                    ? "max-w-[85%] whitespace-pre-line rounded-2xl rounded-br-md bg-teal-700 px-4 py-3 text-sm leading-6 text-white"
                    : message.variant === "error"
                      ? "max-w-[85%] whitespace-pre-line rounded-2xl rounded-bl-md border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800"
                      : "max-w-[85%] whitespace-pre-line rounded-2xl rounded-bl-md bg-zinc-100 px-4 py-3 text-sm leading-6 text-zinc-800"
                }
                role={message.variant === "error" ? "alert" : undefined}
              >
                {message.content}
              </div>
            </div>
          ))}

          {isLoading ? <TypingIndicator /> : null}

          <div ref={messagesEndRef} />
        </div>

        <form
          onSubmit={handleSubmit}
          className="border-t border-zinc-200 p-4 sm:p-6"
        >
          <div className="flex flex-col gap-3 sm:flex-row">
            <label htmlFor="chat-input" className="sr-only">
              Message
            </label>
            <input
              ref={inputRef}
              id="chat-input"
              type="text"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              disabled={isLoading}
              placeholder="Type your message..."
              maxLength={2000}
              aria-busy={isLoading}
              className="min-w-0 flex-1 rounded-full border border-zinc-300 px-4 py-3 text-sm text-zinc-900 outline-none transition-colors focus:border-teal-500 disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-500"
            />
            <button
              type="submit"
              disabled={isLoading || input.trim().length === 0}
              aria-busy={isLoading}
              className="inline-flex h-12 items-center justify-center rounded-full bg-teal-700 px-6 text-sm font-medium text-white transition-colors hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isLoading ? "Sending..." : "Send"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
