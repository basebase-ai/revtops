/**
 * Search field used on Apps and Documents galleries: full-width with optional clear control.
 */

import type { KeyboardEventHandler } from "react";

export interface GallerySearchInputProps {
  value: string;
  onChange: (next: string) => void;
  placeholder: string;
  "aria-label": string;
  /** Optional, e.g. Enter to submit search, Escape to clear. */
  onKeyDown?: KeyboardEventHandler<HTMLInputElement>;
}

export function GallerySearchInput({
  value,
  onChange,
  placeholder,
  "aria-label": ariaLabel,
  onKeyDown,
}: GallerySearchInputProps): JSX.Element {
  const hasValue: boolean = value.length > 0;

  return (
    <div className="relative flex-1 max-w-md min-w-0">
      <input
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        className={`w-full py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500 ${
          hasValue ? "pl-3 pr-9" : "px-3"
        }`}
        aria-label={ariaLabel}
      />
      {hasValue ? (
        <button
          type="button"
          onClick={() => onChange("")}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
          title="Clear search"
          aria-label="Clear search"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      ) : null}
    </div>
  );
}
