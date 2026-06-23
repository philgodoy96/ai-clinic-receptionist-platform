import { publicConfig } from "@/lib/config";

export type ChatMessageRequest = {
  message: string;
  conversation_id?: string | null;
  patient_id?: string | null;
  conversation_metadata?: Record<string, unknown>;
};

export type ChatMessageResponse = {
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string;
  intent: string;
  reply: string;
  appointment_id: string | null;
  booking_confirmed: boolean;
  confirmation_email_queued?: boolean | null;
};

type ApiErrorBody = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    request_id?: string;
    correlation_id?: string;
  };
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

function getApiBasePath(): string {
  if (typeof window !== "undefined") {
    return "/api/v1";
  }

  return `${publicConfig.apiBaseUrl.replace(/\/$/, "")}/api/v1`;
}

export function toSafeChatErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    switch (error.code) {
      case "demo_guardrail_limit_exceeded":
        return "Demo rate limit reached. Please wait a moment and try again.";
      case "demo_guardrail_store_unavailable":
        return "The demo is temporarily unavailable. Please try again later.";
      case "conversation_not_found":
        return "This chat session expired. Send a new message to start again.";
      case "invalid_conversation_message":
        return "That message could not be sent. Please try again.";
      case "validation_error":
        return "That message could not be sent. Please check your input.";
      default:
        if (error.status === 0 || error.status >= 500) {
          return "Unable to reach the receptionist service. Please try again.";
        }
        return error.message;
    }
  }

  return "Unable to reach the receptionist service. Please try again.";
}

export async function sendChatMessage(
  payload: ChatMessageRequest,
  signal?: AbortSignal,
): Promise<ChatMessageResponse> {
  let response: Response;

  try {
    response = await fetch(`${getApiBasePath()}/chat/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        message: payload.message,
        conversation_id: payload.conversation_id ?? null,
        patient_id: payload.patient_id ?? null,
        conversation_metadata: payload.conversation_metadata ?? {},
      }),
      signal,
    });
  } catch {
    throw new ApiClientError(
      0,
      "network_error",
      "Unable to reach the receptionist service. Please try again.",
    );
  }

  if (!response.ok) {
    let body: ApiErrorBody | null = null;

    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = null;
    }

    throw new ApiClientError(
      response.status,
      body?.error?.code ?? "request_failed",
      body?.error?.message ?? "Something went wrong. Please try again.",
    );
  }

  return (await response.json()) as ChatMessageResponse;
}
