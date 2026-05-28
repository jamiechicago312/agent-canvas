import { useTranslation } from "react-i18next";
import { Typography } from "#/ui/typography";
import { I18nKey } from "#/i18n/declaration";
import { SettingsNavRenderedItem } from "#/hooks/use-settings-nav-items";
import { SidebarNavLink } from "#/components/features/sidebar/sidebar-nav-link";
import { BackendSyncedSettingsBadge } from "#/components/features/settings/backend-synced-settings-badge";
import { SettingsNavHeader } from "./settings-nav-header";
import { SettingsNavDivider } from "./settings-nav-divider";

interface SettingsMobileHubProps {
  navigationItems: SettingsNavRenderedItem[];
}

export function SettingsMobileHub({ navigationItems }: SettingsMobileHubProps) {
  const { t } = useTranslation("openhands");

  return (
    <div
      data-testid="settings-mobile-hub"
      className="flex flex-col gap-4 px-4 py-2 md:hidden"
    >
      <Typography.H2>{t(I18nKey.SETTINGS$TITLE)}</Typography.H2>
      <nav className="flex flex-col gap-0.5">
        {navigationItems.map((renderedItem, index) => {
          if (renderedItem.type === "header") {
            return (
              <SettingsNavHeader
                key={`hub-header-${renderedItem.text}`}
                text={renderedItem.text}
                className={index === 0 ? "pt-0" : "pt-4"}
              />
            );
          }

          if (renderedItem.type === "divider") {
            return (
              <SettingsNavDivider
                key={`hub-divider-${index}`}
                className="my-2"
              />
            );
          }

          return (
            <SidebarNavLink
              key={renderedItem.item.to}
              to={renderedItem.item.to}
              label={t(renderedItem.item.text as I18nKey)}
              end
              testId={`sidebar-settings-${renderedItem.item.to}`}
              icon={renderedItem.item.icon}
              disabled={renderedItem.disabled}
              disabledReason={
                renderedItem.disabled && renderedItem.disabledAgentName
                  ? t(I18nKey.SETTINGS$AGENT_DISABLED_TOOLTIP, {
                      agentName: renderedItem.disabledAgentName,
                    })
                  : undefined
              }
            />
          );
        })}
      </nav>
      <div className="pt-1">
        <BackendSyncedSettingsBadge />
      </div>
    </div>
  );
}
