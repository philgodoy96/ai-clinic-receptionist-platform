export type VoiceCallState =
  | "idle"
  | "requesting_microphone"
  | "connecting"
  | "connected"
  | "ending"
  | "ended"
  | "error";

export async function requestMicrophone(): Promise<MediaStream> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone access is not supported in this browser.");
  }

  return navigator.mediaDevices.getUserMedia({ audio: true });
}

export function stopMediaStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => {
    track.stop();
  });
}

export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export const VOICE_CALL_STATE_LABELS: Record<VoiceCallState, string> = {
  idle: "Ready to start",
  requesting_microphone: "Requesting microphone access",
  connecting: "Connecting to receptionist",
  connected: "Call in progress",
  ending: "Ending call",
  ended: "Call ended",
  error: "Call unavailable",
};
