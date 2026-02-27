/**
 * Central brand config: name, logo, and colors (from logo.svg).
 * Update this file to rebrand the webapp.
 */

export const BRAND = {
  name: 'Basebase',
  logoPath: '/logo.svg',
  /** Primary gradient colors from logo (green) */
  colors: {
    primary: '#22c55e',
    primaryLight: '#4ade80',
  },
} as const;

export const APP_NAME: string = BRAND.name;
export const LOGO_PATH: string = BRAND.logoPath;
