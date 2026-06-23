export type SafeApiErrorCategory =
  | "backend_unavailable"
  | "timeout"
  | "rate_limited"
  | "unknown";

export const SAFE_API_ERROR_MESSAGES: Record<SafeApiErrorCategory, string> = {
  backend_unavailable:
    "The receptionist service is unavailable right now. Please try again shortly.",
  timeout: "The request took too long. Please try again.",
  rate_limited:
    "Demo rate limit reached. Please wait a moment and try again.",
  unknown: "Something went wrong. Please try again.",
};

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
  }
}

const BACKEND_UNAVAILABLE_CODES = new Set([
  "network_error",
  "demo_guardrail_store_unavailable",
]);

const RATE_LIMITED_CODES = new Set([
  "demo_guardrail_limit_exceeded",
  "rate_limited",
]);

const KNOWN_SAFE_CODES: Record<string, string> = {
  conversation_not_found:
    "This chat session expired. Send a new message to start again.",
  invalid_conversation_message:
    "That message could not be sent. Please try again.",
  validation_error:
    "That message could not be sent. Please check your input.",
};

export function categorizeApiError(error: unknown): SafeApiErrorCategory {
  if (error instanceof ApiClientError) {
    if (error.code === "timeout") {
      return "timeout";
    }

    if (RATE_LIMITED_CODES.has(error.code) || error.status === 429) {
      return "rate_limited";
    }

    if (
      BACKEND_UNAVAILABLE_CODES.has(error.code) ||
      error.status === 0 ||
      error.status === 502 ||
      error.status === 503 ||
      error.status === 504 ||
      error.status >= 500
    ) {
      return "backend_unavailable";
    }

    return "unknown";
  }

  if (error instanceof DOMException && error.name === "AbortError") {
    return "timeout";
  }

  return "unknown";
}

export function toSafeApiErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError && KNOWN_SAFE_CODES[error.code]) {
    return KNOWN_SAFE_CODES[error.code];
  }

  return SAFE_API_ERROR_MESSAGES[categorizeApiError(error)];
}
