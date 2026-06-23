import { publicConfig } from "@/lib/config";
import { ApiClientError } from "@/lib/api-errors";

export const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

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
  };
};

export { ApiClientError, toSafeApiErrorMessage as toSafeChatErrorMessage } from "@/lib/api-errors";

function getApiBasePath(): string {
  if (typeof window !== "undefined") {
    return "/api/v1";
  }

  return `${publicConfig.apiBaseUrl.replace(/\/$/, "")}/api/v1`;
}

function createRequestSignal(
  timeoutMs: number,
  externalSignal?: AbortSignal,
): { signal: AbortSignal; clear: () => void } {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener("abort", onExternalAbort, { once: true });
    }
  }

  return {
    signal: controller.signal,
    clear: () => {
      clearTimeout(timeoutId);
      externalSignal?.removeEventListener("abort", onExternalAbort);
    },
  };
}

export async function sendChatMessage(
  payload: ChatMessageRequest,
  options?: {
    signal?: AbortSignal;
    timeoutMs?: number;
  },
): Promise<ChatMessageResponse> {
  const timeoutMs = options?.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const { signal, clear } = createRequestSignal(timeoutMs, options?.signal);

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
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiClientError(0, "timeout", "Request timed out.");
    }

    throw new ApiClientError(0, "network_error", "Network request failed.");
  } finally {
    clear();
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
      body?.error?.message ?? "Request failed.",
    );
  }

  return (await response.json()) as ChatMessageResponse;
}
