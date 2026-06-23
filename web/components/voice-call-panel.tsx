"use client";

import { useEffect, useRef, useState } from "react";

import { createRetellWebCall } from "@/lib/api-client";
import { publicConfig } from "@/lib/config";
import {
  loadDemoConversationId,
  saveDemoConversationId,
} from "@/lib/demo-session";
import { loadRetellWebClient, type RetellVoiceClient } from "@/lib/retell-voice-client";
import { toSafeVoiceErrorMessage } from "@/lib/voice-api-errors";
import {
  VOICE_CALL_STATE_LABELS,
  type VoiceCallState,
} from "@/lib/voice-call";

type VoiceCallPanelProps = {
  onExit: () => void;
};

function VoiceDisabledState({ onExit }: { onExit: () => void }) {
  return (
    <div className="space-y-4">
      <p className="text-lg font-medium text-zinc-900">
        Voice demo is being configured.
      </p>
      <p className="leading-7 text-zinc-600">
        The backend already exposes Retell tool routes, verified webhooks, and
        voice conversation persistence. This public UI entry point will connect
        once the Retell dashboard and agent setup are complete.
      </p>
      <p className="text-sm text-zinc-500">
        No microphone access is requested while voice remains disabled.
      </p>
      <button
        type="button"
        onClick={onExit}
        className="inline-flex h-10 items-center justify-center rounded-full border border-zinc-300 bg-white px-4 text-sm font-medium text-zinc-700 transition-colors hover:border-zinc-400 hover:bg-zinc-50"
      >
        Back to landing
      </button>
    </div>
  );
}

function VoiceEnabledState({ onExit }: { onExit: () => void }) {
  const [callState, setCallState] = useState<VoiceCallState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isAgentTalking, setIsAgentTalking] = useState(false);
  const sessionActiveRef = useRef(false);
  const retellClientRef = useRef<RetellVoiceClient | null>(null);

  useEffect(() => {
    return () => {
      sessionActiveRef.current = false;
      retellClientRef.current?.stopCall();
      retellClientRef.current = null;
    };
  }, []);

  const callInProgress =
    callState === "requesting_microphone" ||
    callState === "connecting" ||
    callState === "connected" ||
    callState === "ending";

  function detachRetellListeners(client: RetellVoiceClient) {
    client.removeAllListeners();
  }

  function handleRetellCallEnded() {
    sessionActiveRef.current = false;
    setIsAgentTalking(false);

    if (retellClientRef.current) {
      detachRetellListeners(retellClientRef.current);
      retellClientRef.current = null;
    }

    setCallState("ended");
  }

  async function handleStartCall() {
    if (callInProgress) {
      return;
    }

    setErrorMessage(null);
    setIsAgentTalking(false);
    sessionActiveRef.current = true;
    setCallState("connecting");

    try {
      const conversationId = loadDemoConversationId();
      const webCall = await createRetellWebCall({
        conversation_id: conversationId,
      });

      if (!sessionActiveRef.current) {
        return;
      }

      if (webCall.conversation_id) {
        saveDemoConversationId(webCall.conversation_id);
      }

      const client = await loadRetellWebClient();
      if (!sessionActiveRef.current) {
        client.stopCall();
        return;
      }

      retellClientRef.current = client;

      client.on("call_started", () => {
        if (!sessionActiveRef.current) {
          return;
        }

        setCallState("connected");
      });

      client.on("call_ended", () => {
        handleRetellCallEnded();
      });

      client.on("agent_start_talking", () => {
        if (sessionActiveRef.current) {
          setIsAgentTalking(true);
        }
      });

      client.on("agent_stop_talking", () => {
        setIsAgentTalking(false);
      });

      client.on("error", () => {
        if (!sessionActiveRef.current) {
          return;
        }

        sessionActiveRef.current = false;
        setIsAgentTalking(false);
        detachRetellListeners(client);
        retellClientRef.current = null;
        setErrorMessage(
          "Unable to connect the voice call. Please try again.",
        );
        setCallState("error");
      });

      setCallState("requesting_microphone");
      await client.startCall({
        accessToken: webCall.access_token,
      });
    } catch (error) {
      if (!sessionActiveRef.current) {
        return;
      }

      sessionActiveRef.current = false;
      retellClientRef.current?.stopCall();
      retellClientRef.current = null;
      setIsAgentTalking(false);
      setErrorMessage(toSafeVoiceErrorMessage(error));
      setCallState("error");
    }
  }

  function handleEndCall() {
    if (
      callState === "idle" ||
      callState === "ended" ||
      callState === "ending" ||
      callState === "error"
    ) {
      return;
    }

    sessionActiveRef.current = false;
    setCallState("ending");
    setIsAgentTalking(false);

    if (retellClientRef.current) {
      retellClientRef.current.stopCall();
      return;
    }

    setCallState("ended");
  }

  function handleRetry() {
    setErrorMessage(null);
    setIsAgentTalking(false);
    setCallState("idle");
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-zinc-200 bg-zinc-50 px-4 py-3">
        <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          Call status
        </p>
        <p className="mt-1 text-sm font-medium text-zinc-900">
          {VOICE_CALL_STATE_LABELS[callState]}
        </p>
        {callState === "connected" && isAgentTalking ? (
          <p className="mt-2 text-sm text-teal-800">Receptionist is speaking</p>
        ) : null}
      </div>

      <p className="text-sm leading-6 text-zinc-600">
        Start a live voice session with the clinic receptionist. Your microphone
        is used only during an active call.
      </p>

      {errorMessage ? (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {errorMessage}
        </div>
      ) : null}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        {(callState === "idle" || callState === "ended" || callState === "error") && (
          <button
            type="button"
            onClick={() => void handleStartCall()}
            className="inline-flex h-12 items-center justify-center rounded-full bg-teal-700 px-6 text-sm font-medium text-white transition-colors hover:bg-teal-800"
          >
            Start call
          </button>
        )}

        {callInProgress ? (
          <button
            type="button"
            onClick={handleEndCall}
            disabled={callState === "ending"}
            className="inline-flex h-12 items-center justify-center rounded-full border border-zinc-300 bg-white px-6 text-sm font-medium text-zinc-900 transition-colors hover:border-zinc-400 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            End call
          </button>
        ) : null}

        {callState === "error" ? (
          <button
            type="button"
            onClick={handleRetry}
            className="inline-flex h-12 items-center justify-center rounded-full border border-zinc-300 bg-white px-6 text-sm font-medium text-zinc-900 transition-colors hover:border-zinc-400 hover:bg-zinc-50"
          >
            Try again
          </button>
        ) : null}

        <button
          type="button"
          onClick={onExit}
          disabled={callState === "ending"}
          className="inline-flex h-12 items-center justify-center rounded-full border border-zinc-300 bg-white px-6 text-sm font-medium text-zinc-700 transition-colors hover:border-zinc-400 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Back to landing
        </button>
      </div>
    </div>
  );
}

export function VoiceCallPanel({ onExit }: VoiceCallPanelProps) {
  const voiceEnabled = publicConfig.voiceDemoEnabled;

  return (
    <section className="mx-auto flex h-full w-full max-w-3xl flex-1 flex-col px-4 py-6 sm:px-6">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-white-900">Voice demo</h2>
          <p className="text-sm text-white-500">
            {voiceEnabled
              ? "Live Retell voice session via the public demo API."
              : "Feature flag disabled — configuration in progress."}
          </p>
        </div>
        {!voiceEnabled ? (
          <button
            type="button"
            onClick={onExit}
            className="inline-flex h-10 shrink-0 items-center justify-center rounded-full border border-zinc-300 bg-white px-4 text-sm font-medium text-zinc-700 transition-colors hover:border-zinc-400 hover:bg-zinc-50"
          >
            Exit voice
          </button>
        ) : null}
      </div>

      <div className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm sm:p-8">
        {voiceEnabled ? (
          <VoiceEnabledState onExit={onExit} />
        ) : (
          <VoiceDisabledState onExit={onExit} />
        )}
      </div>
    </section>
  );
}
