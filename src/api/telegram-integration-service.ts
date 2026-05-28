import { getAgentServerSessionApiKey } from "./agent-server-config";

export type TelegramIntegrationMode = "polling" | "webhook";

export interface TelegramIntegrationStatus {
  status: string;
  enabled: boolean;
  configured: boolean;
  running: boolean;
  mode: TelegramIntegrationMode;
  owner_chat_id: string | null;
  webhook_url: string | null;
  last_error: string | null;
}

function buildHeaders(): HeadersInit {
  const sessionApiKey = getAgentServerSessionApiKey();

  return {
    "Content-Type": "application/json",
    ...(sessionApiKey ? { "X-Session-API-Key": sessionApiKey } : {}),
  };
}

async function fetchTelegramIntegration<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...buildHeaders(),
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Telegram request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getTelegramIntegrationStatus() {
  return fetchTelegramIntegration<TelegramIntegrationStatus>(
    "/api/integrations/telegram/status",
  );
}

export function reloadTelegramIntegration() {
  return fetchTelegramIntegration<TelegramIntegrationStatus>(
    "/api/integrations/telegram/reload",
    { method: "POST" },
  );
}
