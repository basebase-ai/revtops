/**
 * Password strength helpers used by email/password auth flows.
 *
 * These checks are intentionally opinionated and align with common guidance:
 * - Prefer longer passwords (12+ chars)
 * - Require multiple character classes
 * - Block obvious weak patterns
 */

export type PasswordValidationResult = {
  isValid: boolean;
  errors: string[];
};

const COMMON_WEAK_PASSWORDS = new Set([
  'password',
  'password123',
  'qwerty',
  'qwerty123',
  'letmein',
  'admin',
  'welcome',
  '123456',
  '12345678',
  '123456789',
  '111111',
  'abc123',
]);

/**
 * Returns true for obvious keyboard/sequential runs often found in weak passwords.
 */
function hasSequentialPattern(value: string): boolean {
  const lower = value.toLowerCase();
  const sequences = [
    'abcdefghijklmnopqrstuvwxyz',
    'qwertyuiopasdfghjklzxcvbnm',
    '0123456789',
  ];

  return sequences.some((sequence) => {
    for (let i = 0; i <= sequence.length - 4; i += 1) {
      const slice = sequence.slice(i, i + 4);
      const reversed = slice.split('').reverse().join('');
      if (lower.includes(slice) || lower.includes(reversed)) {
        return true;
      }
    }

    return false;
  });
}

/**
 * Validate password against baseline "generally considered good" rules.
 */
export function validateGoodPassword(password: string, email?: string): PasswordValidationResult {
  const errors: string[] = [];

  if (password.length < 12) {
    errors.push('Use at least 12 characters.');
  }

  const hasLower = /[a-z]/.test(password);
  const hasUpper = /[A-Z]/.test(password);
  const hasNumber = /\d/.test(password);
  const hasSymbol = /[^A-Za-z0-9]/.test(password);

  const classCount = [hasLower, hasUpper, hasNumber, hasSymbol].filter(Boolean).length;
  if (classCount < 3) {
    errors.push('Use at least 3 of: uppercase, lowercase, number, symbol.');
  }

  if (/(.)\1{2,}/.test(password)) {
    errors.push('Avoid repeating the same character 3+ times in a row.');
  }

  if (hasSequentialPattern(password)) {
    errors.push('Avoid common keyboard or sequential patterns (like 1234 or abcd).');
  }

  if (COMMON_WEAK_PASSWORDS.has(password.toLowerCase())) {
    errors.push('This password is too common. Choose something less predictable.');
  }

  if (email) {
    const localPart = email.split('@')[0]?.trim().toLowerCase();
    if (localPart && localPart.length >= 3 && password.toLowerCase().includes(localPart)) {
      errors.push('Avoid including your email name in your password.');
    }
  }

  return {
    isValid: errors.length === 0,
    errors,
  };
}
