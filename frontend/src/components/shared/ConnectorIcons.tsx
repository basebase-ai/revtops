/**
 * Shared connector icon + color config used by DataSources and DailyDigestGrid.
 */
import type { IconType } from "react-icons";
import {
  SiSalesforce,
  SiHubspot,
  SiSlack,
  SiZoom,
  SiGooglecalendar,
  SiGmail,
  SiGoogledrive,
  SiGithub,
  SiLinear,
  SiJira,
  SiAsana,
} from "react-icons/si";
import {
  HiOutlineCalendar,
  HiOutlineMail,
  HiGlobeAlt,
  HiDeviceMobile,
  HiMicrophone,
  HiLightningBolt,
  HiDocumentText,
  HiCube,
  HiLink,
} from "react-icons/hi";

const ApolloIcon: IconType = ({ className, ...props }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    className={className}
    {...props}
  >
    <line x1="12" y1="2" x2="12" y2="22" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
    <line x1="19.07" y1="4.93" x2="4.93" y2="19.07" />
  </svg>
);

export const CONNECTOR_ICON_MAP: Record<string, IconType> = {
  hubspot: SiHubspot,
  salesforce: SiSalesforce,
  slack: SiSlack,
  zoom: SiZoom,
  "google-calendar": SiGooglecalendar,
  google_calendar: SiGooglecalendar,
  gmail: SiGmail,
  "microsoft-calendar": HiOutlineCalendar,
  microsoft_calendar: HiOutlineCalendar,
  "microsoft-mail": HiOutlineMail,
  microsoft_mail: HiOutlineMail,
  fireflies: HiMicrophone,
  google_drive: SiGoogledrive,
  apollo: ApolloIcon,
  github: SiGithub,
  linear: SiLinear,
  jira: SiJira,
  asana: SiAsana,
  globe: HiGlobeAlt,
  terminal: HiLightningBolt,
  sms: HiDeviceMobile,
  artifacts: HiDocumentText,
  apps: HiCube,
  plug: HiLink,
};

export interface ConnectorDisplay {
  icon: string;
  color: string;
  label: string;
}

export const CONNECTOR_DISPLAY: Record<string, ConnectorDisplay> = {
  hubspot: { icon: "hubspot", color: "from-orange-500 to-orange-600", label: "HubSpot" },
  salesforce: { icon: "salesforce", color: "from-blue-500 to-blue-600", label: "Salesforce" },
  slack: { icon: "slack", color: "from-purple-500 to-purple-600", label: "Slack" },
  zoom: { icon: "zoom", color: "from-blue-400 to-blue-500", label: "Zoom" },
  google_calendar: { icon: "google_calendar", color: "from-green-500 to-green-600", label: "Google Calendar" },
  gmail: { icon: "gmail", color: "from-red-500 to-red-600", label: "Gmail" },
  microsoft_calendar: { icon: "microsoft_calendar", color: "from-sky-500 to-sky-600", label: "Microsoft Calendar" },
  microsoft_mail: { icon: "microsoft_mail", color: "from-sky-500 to-sky-600", label: "Microsoft Mail" },
  fireflies: { icon: "fireflies", color: "from-violet-500 to-violet-600", label: "Fireflies" },
  granola: { icon: "/connector-icons/granola.png", color: "from-lime-500 to-green-600", label: "Granola" },
  google_drive: { icon: "google_drive", color: "from-yellow-500 to-amber-500", label: "Google Drive" },
  apollo: { icon: "apollo", color: "from-yellow-400 to-yellow-500", label: "Apollo" },
  github: { icon: "github", color: "from-gray-600 to-gray-700", label: "GitHub" },
  linear: { icon: "linear", color: "from-indigo-500 to-violet-600", label: "Linear" },
  jira: { icon: "jira", color: "from-blue-500 to-blue-600", label: "Jira" },
  asana: { icon: "asana", color: "from-fuchsia-500 to-pink-600", label: "Asana" },
  web_search: { icon: "globe", color: "from-emerald-500 to-teal-600", label: "Web Search" },
  code_sandbox: { icon: "terminal", color: "from-amber-500 to-orange-600", label: "Code Sandbox" },
  twilio: { icon: "sms", color: "from-red-500 to-pink-600", label: "Twilio" },
  artifacts: { icon: "artifacts", color: "from-slate-500 to-slate-600", label: "Artifacts" },
  apps: { icon: "apps", color: "from-violet-500 to-purple-600", label: "Apps" },
  mcp: { icon: "plug", color: "from-cyan-500 to-blue-600", label: "MCP" },
  ispot_tv: { icon: "globe", color: "from-emerald-500 to-teal-600", label: "iSpot.tv" },
  meetings: { icon: "fireflies", color: "from-violet-500 to-violet-600", label: "Meeting Notes" },
};

export const DEFAULT_CONNECTOR_ICON = "globe";
export const DEFAULT_CONNECTOR_COLOR = "from-gray-500 to-gray-600";

export function isImageIcon(iconId: string): boolean {
  return iconId.startsWith("/") || iconId.startsWith("http");
}

export function getConnectorColorClass(color: string): string {
  const colorMap: Record<string, string> = {
    "from-orange-500 to-orange-600": "bg-orange-500",
    "from-blue-500 to-blue-600": "bg-blue-500",
    "from-blue-400 to-blue-500": "bg-blue-400",
    "from-purple-500 to-purple-600": "bg-purple-500",
    "from-green-500 to-green-600": "bg-green-500",
    "from-sky-500 to-sky-600": "bg-sky-500",
    "from-red-500 to-red-600": "bg-red-500",
    "from-violet-500 to-violet-600": "bg-violet-500",
    "from-yellow-400 to-yellow-500": "bg-yellow-400",
    "from-yellow-500 to-amber-500": "bg-yellow-500",
    "from-indigo-500 to-violet-600": "bg-indigo-500",
    "from-gray-600 to-gray-700": "bg-gray-600",
    "from-gray-500 to-gray-600": "bg-gray-500",
    "from-emerald-500 to-teal-600": "bg-emerald-500",
    "from-lime-500 to-green-600": "bg-lime-500",
    "from-fuchsia-500 to-pink-600": "bg-fuchsia-500",
    "from-amber-500 to-orange-600": "bg-amber-500",
    "from-red-500 to-pink-600": "bg-red-500",
    "from-slate-500 to-slate-600": "bg-slate-500",
    "from-cyan-500 to-blue-600": "bg-cyan-500",
  };
  return colorMap[color] ?? "bg-surface-600";
}

export function renderConnectorIcon(iconId: string, sizeClass: string): JSX.Element {
  if (isImageIcon(iconId)) {
    return <img src={iconId} alt="" className={`${sizeClass} rounded object-cover`} />;
  }
  const IconComponent = CONNECTOR_ICON_MAP[iconId] ?? HiGlobeAlt;
  return <IconComponent className={sizeClass} />;
}
