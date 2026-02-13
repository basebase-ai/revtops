/**
 * Date formatting for display.
 * - Datetime strings (ISO8601 with Z or offset): pass through to new Date(iso), then use
 *   toLocaleString() / toLocaleDateString() so the browser shows local time.
 * - Date-only strings (YYYY-MM-DD): parse as local calendar date so the displayed day
 *   doesn't shift in timezones west of UTC (e.g. "2025-06-08" stays June 8, not June 7).
 */

/**
 * Format a date-only string (YYYY-MM-DD) as local calendar date.
 * Use for close_date, etc. Avoids new Date("2025-06-08") being UTC midnight (shows as previous day in PST).
 */
export function formatDateOnly(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  const match = String(dateStr).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (match && match[1] && match[2] && match[3]) {
    const year = parseInt(match[1], 10);
    const month = parseInt(match[2], 10) - 1;
    const day = parseInt(match[3], 10);
    const date = new Date(year, month, day);
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}
