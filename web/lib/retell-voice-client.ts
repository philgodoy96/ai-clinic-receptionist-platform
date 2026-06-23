import type { RetellWebClient } from "retell-client-js-sdk";

export type RetellVoiceClient = RetellWebClient;

export async function loadRetellWebClient(): Promise<RetellVoiceClient> {
  const { RetellWebClient } = await import("retell-client-js-sdk");
  return new RetellWebClient();
}
