/**
 * Public layout for unauthenticated pages (landing, blog)
 * Provides consistent navigation header across public pages
 */

import { ReactNode } from 'react';

interface PublicLayoutProps {
  children: ReactNode;
  onNavigate: (page: 'landing' | 'blog') => void;
  onSignIn: () => void;
  currentPage: 'landing' | 'blog';
}

export function PublicLayout({
  children,
  onNavigate,
  onSignIn,
  currentPage,
}: PublicLayoutProps): JSX.Element {
  return (
    <div className="min-h-screen bg-surface-950">
      {/* Navigation */}
      <nav className="sticky top-0 z-50 bg-surface-950/80 backdrop-blur-lg border-b border-surface-800">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          {/* Logo */}
          <button
            onClick={() => onNavigate('landing')}
            className="flex items-center gap-2 hover:opacity-80 transition-opacity"
          >
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <img src="/logo.svg" alt="Revtops" className="w-5 h-5 invert" />
            </div>
            <span className="text-xl font-bold text-surface-50">Revtops</span>
          </button>

          {/* Navigation Links */}
          <div className="flex items-center gap-6">
            <button
              onClick={() => onNavigate('blog')}
              className={`px-3 py-2 text-sm font-medium transition-colors ${
                currentPage === 'blog'
                  ? 'text-primary-400'
                  : 'text-surface-300 hover:text-white'
              }`}
            >
              Blog
            </button>
            <button
              onClick={onSignIn}
              className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-white transition-colors"
            >
              Sign In
            </button>
          </div>
        </div>
      </nav>

      {/* Page Content */}
      {children}
    </div>
  );
}
