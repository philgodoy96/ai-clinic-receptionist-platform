export type VoiceCallState =
  | "idle"
  | "requesting_microphone"
  | "connecting"
  | "connected"
  | "ending"
  | "ended"
  | "error";

export const VOICE_CALL_STATE_LABELS: Record<VoiceCallState, string> = {
  idle: "Ready to start",
  requesting_microphone: "Requesting microphone access",
  connecting: "Connecting to receptionist",
  connected: "Call in progress",
  ending: "Ending call",
  ended: "Call ended",
  error: "Call unavailable",
};
