import React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Navigate } from "react-router";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { SecretsService } from "#/api/secrets-service";
import {
  getTelegramIntegrationConfig,
  getTelegramIntegrationStatus,
  saveTelegramIntegrationConfig,
  TelegramIntegrationConfig,
  TelegramIntegrationMode,
  TelegramIntegrationStatus,
} from "#/api/telegram-integration-service";
import { useSearchSecrets } from "#/hooks/query/use-get-secrets";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { cn } from "#/utils/utils";
import { useActiveBackend } from "#/contexts/active-backend-context";

const TELEGRAM_SECRET_NAME = "TELEGRAM_BOT_TOKEN";
const TELEGRAM_SECRET_DESCRIPTION =
  "Telegram bot token for Agent Canvas local integration";
const TELEGRAM_STATUS_QUERY_KEY = ["telegram-integration-status"] as const;

type TelegramFormState = {
  enabled: boolean;
  ownerChatId: string;
  mode: TelegramIntegrationMode;
  webhookUrl: string;
  botToken: string;
};

type TelegramSettingsValue = {
  enabled: boolean;
  ownerChatId: string;
  mode: TelegramIntegrationMode;
  webhookUrl: string;
};

function getTelegramSettings(
  config?: TelegramIntegrationConfig,
): TelegramSettingsValue {
  return {
    enabled: config?.enabled === true,
    ownerChatId:
      typeof config?.owner_chat_id === "string" ? config.owner_chat_id : "",
    mode: config?.mode === "webhook" ? "webhook" : "polling",
    webhookUrl:
      typeof config?.webhook_url === "string" ? config.webhook_url : "",
  };
}

function getStatusLabelKey(
  status?: TelegramIntegrationStatus,
  hasQueryError: boolean = false,
): I18nKey {
  if (hasQueryError) return I18nKey.SETTINGS$TELEGRAM_STATUS_ERROR;
  if (!status) return I18nKey.SETTINGS$TELEGRAM_STATUS_LOADING;
  switch (status.status) {
    case "running_polling":
      return I18nKey.SETTINGS$TELEGRAM_STATUS_RUNNING_POLLING;
    case "running_webhook":
      return I18nKey.SETTINGS$TELEGRAM_STATUS_RUNNING_WEBHOOK;
    case "not_configured":
      return I18nKey.SETTINGS$TELEGRAM_STATUS_NOT_CONFIGURED;
    case "error":
      return I18nKey.SETTINGS$TELEGRAM_STATUS_ERROR;
    case "disabled":
    default:
      return I18nKey.SETTINGS$TELEGRAM_STATUS_DISABLED;
  }
}

function getStatusClassName(
  status?: TelegramIntegrationStatus,
  hasQueryError: boolean = false,
) {
  if (hasQueryError) {
    return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  }

  if (!status) {
    return "border-[var(--oh-border)] bg-[var(--oh-surface-raised)] text-[var(--oh-text-muted)]";
  }

  if (
    status.status === "running_polling" ||
    status.status === "running_webhook"
  ) {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
  }

  if (status.status === "error") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  }

  return "border-[var(--oh-border)] bg-[var(--oh-surface-raised)] text-[var(--oh-text-muted)]";
}

export default function TelegramSettingsScreen() {
  const { t } = useTranslation("openhands");
  const queryClient = useQueryClient();
  const { backend } = useActiveBackend();
  const { data: secrets = [], isLoading: secretsLoading } = useSearchSecrets();

  const configQuery = useQuery({
    queryKey: ["telegram-integration-config"],
    queryFn: getTelegramIntegrationConfig,
    retry: false,
    enabled: backend.kind === "local",
  });
  const statusQuery = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: getTelegramIntegrationStatus,
    refetchInterval: 5000,
    retry: false,
    enabled: backend.kind === "local",
  });

  const telegramSettings = React.useMemo(
    () => getTelegramSettings(configQuery.data),
    [configQuery.data],
  );

  const [formState, setFormState] = React.useState<TelegramFormState>({
    enabled: false,
    ownerChatId: "",
    mode: "polling",
    webhookUrl: "",
    botToken: "",
  });
  const [tokenError, setTokenError] = React.useState<string | undefined>();
  const [webhookError, setWebhookError] = React.useState<string | undefined>();
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  React.useEffect(() => {
    setFormState((current) => ({
      enabled: telegramSettings.enabled,
      ownerChatId: telegramSettings.ownerChatId,
      mode: telegramSettings.mode,
      webhookUrl: telegramSettings.webhookUrl,
      botToken: current.botToken,
    }));
  }, [telegramSettings]);

  if (backend.kind !== "local") {
    return <Navigate to="/settings/app" replace />;
  }

  const hasSavedToken = secrets.some(
    (secret) => secret.name === TELEGRAM_SECRET_NAME,
  );
  const status = statusQuery.data;
  const statusError =
    statusQuery.error instanceof Error ? statusQuery.error.message : null;
  const isLoading = configQuery.isLoading || secretsLoading;
  const isBusy = isLoading || isSubmitting;
  const formIsDirty =
    formState.enabled !== telegramSettings.enabled ||
    formState.ownerChatId !== telegramSettings.ownerChatId ||
    formState.mode !== telegramSettings.mode ||
    formState.webhookUrl !== telegramSettings.webhookUrl ||
    formState.botToken.trim().length > 0;

  const updateFormState = <K extends keyof TelegramFormState>(
    key: K,
    value: TelegramFormState[K],
  ) => {
    setFormState((current) => ({ ...current, [key]: value }));
  };

  const saveTelegramSettings = async () => {
    const trimmedToken = formState.botToken.trim();
    const trimmedOwnerChatId = formState.ownerChatId.trim();
    const trimmedWebhookUrl = formState.webhookUrl.trim();

    const nextTokenError =
      formState.enabled && !hasSavedToken && trimmedToken.length === 0
        ? t(I18nKey.SETTINGS$TELEGRAM_TOKEN_REQUIRED)
        : undefined;
    const nextWebhookError =
      formState.enabled &&
      formState.mode === "webhook" &&
      trimmedWebhookUrl.length === 0
        ? t(I18nKey.SETTINGS$TELEGRAM_WEBHOOK_REQUIRED)
        : undefined;

    setTokenError(nextTokenError);
    setWebhookError(nextWebhookError);

    if (nextTokenError || nextWebhookError) {
      return;
    }

    setIsSubmitting(true);

    try {
      if (trimmedToken.length > 0) {
        await SecretsService.createSecret(
          TELEGRAM_SECRET_NAME,
          trimmedToken,
          TELEGRAM_SECRET_DESCRIPTION,
        );
      }

      const nextStatus = await saveTelegramIntegrationConfig({
        enabled: formState.enabled,
        owner_chat_id: trimmedOwnerChatId || null,
        mode: formState.mode,
        webhook_url:
          formState.mode === "webhook" ? trimmedWebhookUrl || null : null,
      });

      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["telegram-integration-config"],
        }),
        queryClient.invalidateQueries({ queryKey: ["secrets"] }),
        queryClient.invalidateQueries({ queryKey: TELEGRAM_STATUS_QUERY_KEY }),
      ]);

      setFormState((current) => ({ ...current, botToken: "" }));
      setTokenError(undefined);
      setWebhookError(undefined);

      if (nextStatus.status === "error" && nextStatus.last_error) {
        displayErrorToast(nextStatus.last_error);
        return;
      }

      displaySuccessToast(t(I18nKey.SETTINGS$TELEGRAM_SAVE_SUCCESS));
    } catch (error) {
      displayErrorToast(
        (isAxiosError(error) && retrieveAxiosErrorMessage(error)) ||
          (error instanceof Error && error.message) ||
          t(I18nKey.SETTINGS$TELEGRAM_SAVE_FAILURE),
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div data-testid="telegram-settings-screen" className="flex flex-col gap-6">
      <div className="rounded-2xl border border-[var(--oh-border)] bg-[var(--oh-surface)] p-5">
        <p className="text-sm leading-6 text-tertiary-light">
          {t(I18nKey.SETTINGS$TELEGRAM_OVERVIEW)}
        </p>

        <div className="mt-4 flex flex-col gap-2">
          <span className="text-sm text-[var(--oh-text-muted)]">
            {t(I18nKey.SETTINGS$TELEGRAM_STATUS)}
          </span>
          <div className="flex flex-wrap items-center gap-3">
            <span
              className={cn(
                "inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium",
                getStatusClassName(status, statusQuery.isError),
              )}
            >
              {t(getStatusLabelKey(status, statusQuery.isError))}
            </span>
            {status?.owner_chat_id && (
              <span className="text-xs text-[var(--oh-text-muted)]">
                {t(I18nKey.SETTINGS$TELEGRAM_OWNER_CHAT_ID)}:{" "}
                {status.owner_chat_id}
              </span>
            )}
          </div>
          {(status?.last_error || statusError) && (
            <p className="text-xs leading-5 text-amber-200">
              {t(I18nKey.SETTINGS$TELEGRAM_LAST_ERROR)}:{" "}
              {status?.last_error || statusError}
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-6 rounded-2xl border border-[var(--oh-border)] bg-[var(--oh-surface)] p-5">
        <div className="flex flex-col gap-2">
          <SettingsSwitch
            testId="telegram-enabled-switch"
            isToggled={formState.enabled}
            onToggle={(value) => updateFormState("enabled", value)}
            togglePosition="right"
          >
            {t(I18nKey.SETTINGS$TELEGRAM_ENABLE)}
          </SettingsSwitch>
          <p className="text-sm leading-6 text-tertiary-light">
            {t(I18nKey.SETTINGS$TELEGRAM_ENABLE_HELP)}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <SettingsInput
            testId="telegram-token-input"
            type="password"
            label={t(I18nKey.SETTINGS$TELEGRAM_TOKEN)}
            value={formState.botToken}
            onChange={(value) => {
              updateFormState("botToken", value);
              setTokenError(undefined);
            }}
            error={tokenError}
            inputClassName="font-mono"
          />
          <p className="text-sm leading-6 text-tertiary-light">
            {hasSavedToken
              ? t(I18nKey.SETTINGS$TELEGRAM_TOKEN_SAVED_HELP)
              : t(I18nKey.SETTINGS$TELEGRAM_TOKEN_HELP)}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <SettingsInput
            testId="telegram-owner-chat-id-input"
            type="text"
            label={t(I18nKey.SETTINGS$TELEGRAM_OWNER_CHAT_ID)}
            value={formState.ownerChatId}
            onChange={(value) => updateFormState("ownerChatId", value)}
            inputClassName="font-mono"
          />
          <p className="text-sm leading-6 text-tertiary-light">
            {t(I18nKey.SETTINGS$TELEGRAM_OWNER_CHAT_ID_HELP)}
          </p>
        </div>

        <SettingsDropdownInput
          testId="telegram-mode-input"
          name="telegram-mode-input"
          label={t(I18nKey.SETTINGS$TELEGRAM_MODE)}
          items={[
            {
              key: "polling",
              label: t(I18nKey.SETTINGS$TELEGRAM_MODE_POLLING),
            },
            {
              key: "webhook",
              label: t(I18nKey.SETTINGS$TELEGRAM_MODE_WEBHOOK),
            },
          ]}
          selectedKey={formState.mode}
          onSelectionChange={(key) =>
            updateFormState("mode", key === "webhook" ? "webhook" : "polling")
          }
        />

        {formState.mode === "webhook" && (
          <div className="flex flex-col gap-2">
            <SettingsInput
              testId="telegram-webhook-url-input"
              type="url"
              label={t(I18nKey.SETTINGS$TELEGRAM_WEBHOOK_URL)}
              value={formState.webhookUrl}
              onChange={(value) => {
                updateFormState("webhookUrl", value);
                setWebhookError(undefined);
              }}
              error={webhookError}
              inputClassName="font-mono"
            />
            <p className="text-sm leading-6 text-tertiary-light">
              {t(I18nKey.SETTINGS$TELEGRAM_WEBHOOK_URL_HELP)}
            </p>
          </div>
        )}

        <div className="flex justify-start pt-2">
          <BrandButton
            testId="telegram-save-button"
            variant="primary"
            type="button"
            isDisabled={isBusy || !formIsDirty}
            onClick={saveTelegramSettings}
            aria-busy={isBusy}
          >
            {!isBusy && t(I18nKey.SETTINGS$SAVE_CHANGES)}
            {isBusy && t(I18nKey.SETTINGS$SAVING)}
          </BrandButton>
        </div>
      </div>
    </div>
  );
}
