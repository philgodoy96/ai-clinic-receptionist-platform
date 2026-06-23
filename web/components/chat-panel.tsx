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

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type ChatPanelProps = {
  onExit: () => void;
};

export function ChatPanel({ onExit }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(() =>
    loadDemoConversationId(),
  );
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionRestored, setSessionRestored] = useState(
    () => loadDemoConversationId() !== null,
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
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
    setError(null);
    setIsLoading(true);

    try {
      const response = await sendChatMessage({
        message: trimmed,
        conversation_id: conversationId,
        conversation_metadata: conversationId
          ? {}
          : { source: "public_demo" },
      });

      localMessageIdRef.current += 1;

      saveDemoConversationId(response.conversation_id);
      setConversationId(response.conversation_id);
      setSessionRestored(false);
      setInput("");

      setMessages((current) => [
        ...current,
        {
          id: `user-${localMessageIdRef.current}`,
          role: "user",
          content: trimmed,
        },
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

      setInput(trimmed);
      setError(toSafeChatErrorMessage(submitError));
    } finally {
      isSubmittingRef.current = false;
      setIsLoading(false);
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

      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      ) : null}

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
                    ? "max-w-[85%] rounded-2xl rounded-br-md bg-teal-700 px-4 py-3 text-sm leading-6 text-white"
                    : "max-w-[85%] rounded-2xl rounded-bl-md bg-zinc-100 px-4 py-3 text-sm leading-6 text-zinc-800"
                }
              >
                {message.content}
              </div>
            </div>
          ))}

          {isLoading ? (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-md bg-zinc-100 px-4 py-3 text-sm text-zinc-500">
                Receptionist is typing...
              </div>
            </div>
          ) : null}

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
              id="chat-input"
              type="text"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              disabled={isLoading}
              placeholder="Type your message..."
              maxLength={2000}
              className="min-w-0 flex-1 rounded-full border border-zinc-300 px-4 py-3 text-sm text-zinc-900 outline-none transition-colors focus:border-teal-500 disabled:cursor-not-allowed disabled:bg-zinc-50"
            />
            <button
              type="submit"
              disabled={isLoading || input.trim().length === 0}
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
