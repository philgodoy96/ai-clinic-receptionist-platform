"use client";

import { useState } from "react";

import { ChatPanel } from "@/components/chat-panel";
import { DemoLanding } from "@/components/demo-landing";
import { VoiceCallPanel } from "@/components/voice-call-panel";

type DemoView = "landing" | "chat" | "voice";

export function DemoPage() {
  const [view, setView] = useState<DemoView>("landing");

  if (view === "chat") {
    return (
      <main className="flex min-h-[calc(100vh-8rem)] flex-1 flex-col">
        <ChatPanel onExit={() => setView("landing")} />
      </main>
    );
  }

  if (view === "voice") {
    return (
      <main className="flex min-h-[calc(100vh-8rem)] flex-1 flex-col">
        <VoiceCallPanel onExit={() => setView("landing")} />
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
