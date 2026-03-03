/**
 * Central brand config: name, logo, and colors (from logo.svg).
 * Update this file to rebrand the webapp.
 */

export const BRAND = {
  name: "Basebase",
  logoPath: "/basebase_logo.svg",
  /** Primary gradient colors from logo (green) */
  colors: {
    primary: "#FF9F1C",
    primaryLight: "#FFB347",
  },
} as const;

export const APP_NAME: string = BRAND.name;
export const LOGO_PATH: string = BRAND.logoPath;
