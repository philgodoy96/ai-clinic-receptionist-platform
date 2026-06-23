"use client";

import { useEffect, useRef, useState } from "react";

import { publicConfig } from "@/lib/config";
import {
  requestMicrophone,
  stopMediaStream,
  VOICE_CALL_STATE_LABELS,
  VoiceCallState,
  wait,
} from "@/lib/voice-call-mock";

type VoiceCallPanelProps = {
  onExit: () => void;
};

function toSafeMicrophoneError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "PermissionDeniedError") {
      return "Microphone permission was denied. Allow access to try the voice demo.";
    }
    if (error.name === "NotFoundError") {
      return "No microphone was found on this device.";
    }
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return "Unable to start the voice demo. Please try again.";
}

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
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const sessionActiveRef = useRef(false);

  useEffect(() => {
    return () => {
      sessionActiveRef.current = false;
      stopMediaStream(mediaStreamRef.current);
      mediaStreamRef.current = null;
    };
  }, []);

  const callInProgress =
    callState === "requesting_microphone" ||
    callState === "connecting" ||
    callState === "connected" ||
    callState === "ending";

  async function handleStartCall() {
    if (callInProgress) {
      return;
    }

    setErrorMessage(null);
    sessionActiveRef.current = true;
    setCallState("requesting_microphone");

    try {
      const stream = await requestMicrophone();
      if (!sessionActiveRef.current) {
        stopMediaStream(stream);
        return;
      }

      mediaStreamRef.current = stream;
      setCallState("connecting");
      await wait(1200);

      if (!sessionActiveRef.current) {
        return;
      }

      setCallState("connected");
    } catch (error) {
      if (!sessionActiveRef.current) {
        return;
      }

      stopMediaStream(mediaStreamRef.current);
      mediaStreamRef.current = null;
      setErrorMessage(toSafeMicrophoneError(error));
      setCallState("error");
    }
  }

  async function handleEndCall() {
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
    stopMediaStream(mediaStreamRef.current);
    mediaStreamRef.current = null;
    await wait(600);
    setCallState("ended");
  }

  function handleRetry() {
    setErrorMessage(null);
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
      </div>

      <p className="text-sm leading-6 text-zinc-600">
        This is a placeholder voice session. A future Retell Web SDK integration
        will replace the mock connection without changing the public demo flow.
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
            onClick={() => void handleEndCall()}
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
          <h2 className="text-lg font-semibold text-zinc-900">Voice demo</h2>
          <p className="text-sm text-zinc-500">
            {voiceEnabled
              ? "Feature flag enabled — mock call flow only."
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
