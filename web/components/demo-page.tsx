"use client";

import { useEffect, useState } from "react";

import { ChatPanel } from "@/components/chat-panel";
import { DemoLanding } from "@/components/demo-landing";
import { VoiceCallPanel } from "@/components/voice-call-panel";
import { clearDemoConversationId } from "@/lib/demo-session";

type DemoView = "landing" | "chat" | "voice";

export function DemoPage() {
  const [view, setView] = useState<DemoView>("landing");

  // This public demo has no login or conversation history. On every page load
  // the app starts at the landing page, so any conversation id left over from a
  // previous session (e.g. a refresh mid-booking) must not be reused.
  useEffect(() => {
    clearDemoConversationId();
  }, []);

  // Exit always returns to the landing page and ends the current session, so the
  // next chat/voice session starts from a fresh backend conversation.
  function handleExit() {
    clearDemoConversationId();
    setView("landing");
  }

  if (view === "chat") {
    return (
      <main className="flex min-h-[calc(100vh-8rem)] flex-1 flex-col">
        <ChatPanel onExit={handleExit} />
      </main>
    );
  }

  if (view === "voice") {
    return (
      <main className="flex min-h-[calc(100vh-8rem)] flex-1 flex-col">
        <VoiceCallPanel onExit={handleExit} />
      </main>
    );
  }

  return (
    <main className="flex flex-1 flex-col">
      <DemoLanding
        onStartChat={() => setView("chat")}
        onStartVoice={() => setView("voice")}
      />
    </main>
  );
}
