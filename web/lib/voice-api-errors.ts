import {
  ApiClientError,
  SAFE_API_ERROR_MESSAGES,
  categorizeApiError,
  toSafeApiErrorMessage,
} from "@/lib/api-errors";

const VOICE_SAFE_ERROR_CODES: Record<string, string> = {
  voice_demo_disabled: "Voice demo is not available right now.",
  rate_limited: SAFE_API_ERROR_MESSAGES.rate_limited,
  provider_unavailable:
    "The voice receptionist is temporarily unavailable. Please try again shortly.",
  configuration_error: "Voice demo could not be started. Please try again.",
  temporary_failure: SAFE_API_ERROR_MESSAGES.backend_unavailable,
};

export function toSafeVoiceErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError && VOICE_SAFE_ERROR_CODES[error.code]) {
    return VOICE_SAFE_ERROR_CODES[error.code];
  }

  if (error instanceof Error && error.message === "microphone_unavailable") {
    return "Microphone access is not supported in this browser.";
  }

  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "PermissionDeniedError") {
      return "Microphone permission was denied. Allow access to try the voice demo.";
    }
    if (error.name === "NotFoundError") {
      return "No microphone was found on this device.";
    }
  }

  const category = categorizeApiError(error);
  if (category === "rate_limited") {
    return SAFE_API_ERROR_MESSAGES.rate_limited;
  }

  if (category === "backend_unavailable") {
    return VOICE_SAFE_ERROR_CODES.provider_unavailable;
  }

  return toSafeApiErrorMessage(error);
}
