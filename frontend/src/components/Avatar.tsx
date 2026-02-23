/**
 * Centralized Avatar component for displaying user avatars.
 * Supports multiple sizes and falls back to colored initials when no image available.
 */

import { useState } from 'react';

const AVATAR_COLORS = [
  '#6366f1', // indigo
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#f43f5e', // rose
  '#f97316', // orange
  '#eab308', // yellow
  '#22c55e', // green
  '#14b8a6', // teal
  '#06b6d4', // cyan
  '#3b82f6', // blue
];

/** Generate a deterministic color based on user ID */
export function getAvatarColor(userId: string): string {
  let hash = 0;
  for (let i = 0; i < userId.length; i++) {
    hash = ((hash << 5) - hash) + userId.charCodeAt(i);
    hash = hash & hash;
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

/** User-like object that can be rendered as an avatar */
export interface AvatarUser {
  id: string;
  name?: string | null;
  email?: string | null;
  avatarUrl?: string | null;
}

export type AvatarSize = 'xs' | 'sm' | 'md' | 'lg';

const SIZE_CONFIG: Record<AvatarSize, { container: string; text: string }> = {
  xs: { container: 'w-5 h-5', text: 'text-[10px]' },
  sm: { container: 'w-[30px] h-[30px]', text: 'text-xs' },
  md: { container: 'w-8 h-8', text: 'text-sm' },
  lg: { container: 'w-10 h-10', text: 'text-base' },
};

interface AvatarProps {
  user: AvatarUser;
  size?: AvatarSize;
  className?: string;
  style?: React.CSSProperties;
  /** Whether to show a border (useful for stacked avatars) */
  bordered?: boolean;
}

export function Avatar({
  user,
  size = 'md',
  className = '',
  style,
  bordered = false,
}: AvatarProps): JSX.Element {
  const [imgError, setImgError] = useState(false);
  const { container, text } = SIZE_CONFIG[size];
  const displayName = user.name ?? user.email ?? 'Unknown';
  const borderClass = bordered ? 'border border-surface-800' : '';

  if (user.avatarUrl && !imgError) {
    return (
      <img
        src={user.avatarUrl}
        alt={displayName}
        className={`${container} rounded-full object-cover ${borderClass} ${className}`}
        style={style}
        title={displayName}
        referrerPolicy="no-referrer"
        onError={() => setImgError(true)}
      />
    );
  }

  return (
    <div
      className={`${container} ${text} rounded-full flex items-center justify-center font-medium ${borderClass} ${className}`}
      style={{
        backgroundColor: getAvatarColor(user.id),
        color: 'white',
        ...style,
      }}
      title={displayName}
    >
      {displayName.charAt(0).toUpperCase()}
    </div>
  );
}
