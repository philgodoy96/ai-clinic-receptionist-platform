"use client";

import { useState } from "react";

import { ChatPanel } from "@/components/chat-panel";
import { DemoLanding } from "@/components/demo-landing";

export function DemoPage() {
  const [chatOpen, setChatOpen] = useState(false);

  if (chatOpen) {
    return (
      <main className="flex min-h-[calc(100vh-8rem)] flex-1 flex-col">
        <ChatPanel onExit={() => setChatOpen(false)} />
      </main>
    );
  }

  return (
    <main className="flex flex-1 flex-col">
      <DemoLanding onStartChat={() => setChatOpen(true)} />
    </main>
  );
}
