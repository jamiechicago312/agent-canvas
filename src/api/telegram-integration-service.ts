import { getAgentServerSessionApiKey } from "./agent-server-config";

export type TelegramIntegrationMode = "polling" | "webhook";

export interface TelegramIntegrationConfig {
  enabled: boolean;
  configured: boolean;
  mode: TelegramIntegrationMode;
  owner_chat_id: string | null;
  webhook_url: string | null;
}

export interface TelegramIntegrationStatus extends TelegramIntegrationConfig {
  status: string;
  running: boolean;
  last_error: string | null;
}

export interface TelegramIntegrationConfigPayload {
  enabled: boolean;
  mode: TelegramIntegrationMode;
  owner_chat_id: string | null;
  webhook_url: string | null;
}

function buildHeaders(): HeadersInit {
  const sessionApiKey = getAgentServerSessionApiKey();

  return {
    "Content-Type": "application/json",
    ...(sessionApiKey ? { "X-Session-API-Key": sessionApiKey } : {}),
  };
}

function getTelegramErrorMessage(payload: unknown, fallback: string): string {
  if (
    payload &&
    typeof payload === "object" &&
    "detail" in payload &&
    typeof payload.detail === "string"
  ) {
    return payload.detail;
  }

  if (
    payload &&
    typeof payload === "object" &&
    "detail" in payload &&
    Array.isArray(payload.detail)
  ) {
    const parts = payload.detail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const path =
          "loc" in item && Array.isArray(item.loc)
            ? item.loc
                .filter(
                  (part: unknown): part is string => typeof part === "string",
                )
                .join(".")
            : null;
        const message =
          "msg" in item && typeof item.msg === "string" ? item.msg : null;
        if (path && message) return `${path}: ${message}`;
        return message;
      })
      .filter((part): part is string => !!part);

    if (parts.length > 0) {
      return parts.join("; ");
    }
  }

  return fallback;
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
    const responseText = await response.text();
    const fallbackMessage =
      responseText || `Telegram request failed: ${response.status}`;
    let errorMessage = fallbackMessage;

    try {
      const payload = JSON.parse(responseText) as unknown;
      errorMessage = getTelegramErrorMessage(payload, fallbackMessage);
    } catch {
      errorMessage = fallbackMessage;
    }

    throw new Error(errorMessage);
  }

  return response.json() as Promise<T>;
}

export function getTelegramIntegrationConfig() {
  return fetchTelegramIntegration<TelegramIntegrationConfig>(
    "/api/integrations/telegram/config",
  );
}

export function saveTelegramIntegrationConfig(
  payload: TelegramIntegrationConfigPayload,
) {
  return fetchTelegramIntegration<TelegramIntegrationStatus>(
    "/api/integrations/telegram/config",
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
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
