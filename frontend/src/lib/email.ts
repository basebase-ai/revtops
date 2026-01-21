/**
 * Email validation utilities.
 * 
 * Used to enforce work email requirement and extract company domain.
 */

// Blocked personal email domains
export const BLOCKED_EMAIL_DOMAINS = [
  'gmail.com',
  'googlemail.com',
  'hotmail.com',
  'hotmail.co.uk',
  'outlook.com',
  'outlook.co.uk',
  'live.com',
  'msn.com',
  'yahoo.com',
  'yahoo.co.uk',
  'yahoo.fr',
  'ymail.com',
  'aol.com',
  'icloud.com',
  'me.com',
  'mac.com',
  'protonmail.com',
  'proton.me',
  'zoho.com',
  'mail.com',
  'gmx.com',
  'gmx.net',
  'yandex.com',
  'fastmail.com',
  'tutanota.com',
  'hey.com',
];

export function getEmailDomain(email: string): string {
  return email.split('@')[1]?.toLowerCase() || '';
}

export function isPersonalEmail(email: string): boolean {
  const domain = getEmailDomain(email);
  return BLOCKED_EMAIL_DOMAINS.includes(domain);
}

export function suggestCompanyName(domain: string): string {
  return domain
    .replace(/\.(com|co|io|org|net|ai|app|dev|xyz|tech|software|solutions)(\.[a-z]{2})?$/i, '')
    .split(/[.-]/)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}
