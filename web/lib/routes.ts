import { icons } from "@/components/icons";

export interface RouteDef {
  path: string; title: string; desc: string; section: string;
  icon: () => React.JSX.Element; access?: "tenantAdmin" | "platformAdmin";
}

export const routes: RouteDef[] = [
  { path: "/overview", title: "Overview", desc: "Delivery metrics across the tenant", section: "Monitor", icon: icons.overview },
  { path: "/live", title: "Live Stream", desc: "In-app notifications over SSE, as they arrive", section: "Monitor", icon: icons.live },
  { path: "/inbox", title: "Inbox", desc: "A recipient's persistent in-app inbox", section: "Monitor", icon: icons.inbox },
  { path: "/notifications", title: "Notifications", desc: "Inspect a notification and its delivery attempts", section: "Monitor", icon: icons.lookup },
  { path: "/events", title: "Events", desc: "Events accepted by the pipeline", section: "Monitor", icon: icons.events },
  { path: "/dead-letters", title: "Dead Letters", desc: "Failed deliveries and manual redrive", section: "Monitor", icon: icons.alert },
  { path: "/send", title: "Send Event", desc: "Publish an event into the notification pipeline", section: "Trigger", icon: icons.send },
  { path: "/workflows", title: "Workflows", desc: "Event-to-channel routing with fallbacks", section: "Configure", icon: icons.workflows },
  { path: "/templates", title: "Templates", desc: "Channel message templates", section: "Configure", icon: icons.templates },
  { path: "/providers", title: "Providers", desc: "Per-channel delivery providers", section: "Configure", icon: icons.providers },
  { path: "/preferences", title: "Preferences", desc: "Per-recipient channel opt-ins and quiet hours", section: "Configure", icon: icons.preferences },
  { path: "/suppressions", title: "Suppressions", desc: "Addresses that must not be sent to", section: "Configure", icon: icons.block },
  { path: "/devices", title: "Devices", desc: "Register push device tokens", section: "Configure", icon: icons.devices },
  { path: "/team", title: "Team", desc: "Who can access this tenant, and as what role", section: "Access", icon: icons.team, access: "tenantAdmin" },
  { path: "/audit-log", title: "Audit Log", desc: "Who changed what in this tenant", section: "Access", icon: icons.audit, access: "tenantAdmin" },
  { path: "/api-keys", title: "API Keys", desc: "Machine ingress credentials for this tenant", section: "Access", icon: icons.key, access: "tenantAdmin" },
  { path: "/tenants", title: "Tenants", desc: "Every customer organization on the platform", section: "Platform", icon: icons.building, access: "platformAdmin" },
  { path: "/users", title: "Users", desc: "Every account on the platform", section: "Platform", icon: icons.users, access: "platformAdmin" },
];
