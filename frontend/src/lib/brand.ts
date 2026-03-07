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

/**
 * Release stage configuration.
 * Update this when moving to Beta, GA, etc.
 */
export const RELEASE_STAGE = {
  /** Current release stage: 'alpha' | 'beta' | 'ga' | null */
  stage: 'alpha' as 'alpha' | 'beta' | 'ga' | null,
  /** Message shown to users about the current stage */
  message: 'Private Alpha',
  /** Description text for the stage */
  description: "You're part of an exclusive group helping shape the product. The product is evolving quickly, and your feedback will directly influence what we build next.",
} as const;
