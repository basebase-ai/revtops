/**
 * Email helpers.
 */

export function getEmailDomain(email: string): string {
  return email.split('@')[1]?.toLowerCase() || '';
}

/** Extract hostname/domain from a URL (e.g. https://www.orangeco.com → orangeco.com). Returns empty string if invalid. */
export function getDomainFromUrl(url: string): string {
  const trimmed = (url || '').trim();
  if (!trimmed) return '';
  try {
    let s = trimmed;
    if (!/^[a-z][-a-z0-9+.]*:\/\//i.test(s)) s = `https://${s}`;
    const host = new URL(s).hostname?.toLowerCase() ?? '';
    return host.startsWith('www.') ? host.slice(4) : host;
  } catch {
    return '';
  }
}

export function suggestCompanyName(domain: string): string {
  return domain
    .replace(/\.(com|co|io|org|net|ai|app|dev|xyz|tech|software|solutions)(\.[a-z]{2})?$/i, '')
    .split(/[.-]/)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}
