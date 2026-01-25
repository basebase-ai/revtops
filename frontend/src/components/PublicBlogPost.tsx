/**
 * Public individual blog post viewer (no authentication required)
 */

import { useQuery } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchBlogPost, formatDate } from '../lib/markdown';
import type { Components } from 'react-markdown';

interface PublicBlogPostProps {
  slug: string;
  onBack: () => void;
}

export function PublicBlogPost({ slug, onBack }: PublicBlogPostProps): JSX.Element {
  const { data, isLoading, error } = useQuery({
    queryKey: ['blog-post', slug],
    queryFn: () => fetchBlogPost(slug),
  });

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-950">
        <div className="text-surface-400">Loading post...</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-950">
        <div className="text-center">
          <p className="text-red-400 mb-4">Failed to load blog post</p>
          <button onClick={onBack} className="btn-secondary">
            Back to blog
          </button>
        </div>
      </div>
    );
  }

  const { metadata, content } = data;
  const title = (metadata.title as string) ?? 'Untitled';
  const date = (metadata.date as string) ?? '';
  const author = metadata.author as string | undefined;
  const tags = (metadata.tags as string[]) ?? [];

  return (
    <div className="min-h-screen bg-surface-950">
      {/* Gradient background effect */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-600/20 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-emerald-600/10 to-transparent blur-3xl" />
      </div>

      <div className="relative z-10 max-w-3xl mx-auto px-6 py-12">
        {/* Back button */}
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-surface-400 hover:text-surface-200 mb-8 transition-colors group"
        >
          <svg
            className="w-5 h-5 group-hover:-translate-x-1 transition-transform"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15 19l-7-7 7-7"
            />
          </svg>
          Back to blog
        </button>

        {/* Post header */}
        <header className="mb-12">
          <h1 className="text-5xl font-bold text-surface-50 mb-6 leading-tight">{title}</h1>

          <div className="flex items-center gap-4 text-surface-400">
            {date && <time dateTime={date}>{formatDate(date)}</time>}
            {author && (
              <>
                <span>•</span>
                <span>{author}</span>
              </>
            )}
          </div>

          {tags.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-6">
              {tags.map((tag) => (
                <span
                  key={tag}
                  className="px-3 py-1 text-xs rounded-full bg-surface-800 text-surface-300 border border-surface-700"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </header>

        {/* Post content */}
        <article className="prose prose-invert prose-lg max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {content}
          </ReactMarkdown>
        </article>

        {/* Footer CTA */}
        <div className="mt-16 pt-8 border-t border-surface-800">
          <div className="text-center">
            <p className="text-surface-400 mb-4">Ready to get started?</p>
            <button
              onClick={onBack}
              className="btn-secondary mr-3"
            >
              Read more posts
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Custom components for markdown rendering
 */
const markdownComponents: Components = {
  // Headings
  h1: ({ children }) => (
    <h1 className="text-4xl font-bold text-surface-50 mt-12 mb-6 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-3xl font-semibold text-surface-100 mt-10 mb-5">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-2xl font-semibold text-surface-100 mt-8 mb-4">{children}</h3>
  ),

  // Paragraphs and text
  p: ({ children }) => <p className="text-surface-300 mb-6 leading-relaxed text-lg">{children}</p>,

  // Links
  a: ({ href, children }) => (
    <a
      href={href}
      className="text-primary-400 hover:text-primary-300 underline transition-colors"
      target={href?.startsWith('http') ? '_blank' : undefined}
      rel={href?.startsWith('http') ? 'noopener noreferrer' : undefined}
    >
      {children}
    </a>
  ),

  // Lists
  ul: ({ children }) => (
    <ul className="list-disc list-inside mb-6 space-y-2 text-surface-300">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside mb-6 space-y-2 text-surface-300">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,

  // Code
  code: ({ className, children, ...props }) => {
    const match = /language-(\w+)/.exec(className || '');
    const isInline = !match && !className;

    if (isInline) {
      return (
        <code
          className="px-1.5 py-0.5 rounded bg-surface-800 text-primary-300 text-sm font-mono"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <code
        className={`block p-4 rounded-lg bg-surface-900 text-surface-100 text-sm font-mono overflow-x-auto mb-6 ${className ?? ''}`}
        {...props}
      >
        {children}
      </code>
    );
  },

  // Blockquotes
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-primary-500 pl-6 py-2 mb-6 italic text-surface-300">
      {children}
    </blockquote>
  ),

  // Images
  img: ({ src, alt }) => (
    <img
      src={src}
      alt={alt}
      className="rounded-lg w-full my-8 border border-surface-800"
    />
  ),

  // Horizontal rule
  hr: () => <hr className="border-surface-800 my-10" />,

  // Tables
  table: ({ children }) => (
    <div className="overflow-x-auto mb-6">
      <table className="min-w-full divide-y divide-surface-800">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-surface-900">{children}</thead>,
  tbody: ({ children }) => (
    <tbody className="divide-y divide-surface-800 bg-surface-950">{children}</tbody>
  ),
  tr: ({ children }) => <tr>{children}</tr>,
  th: ({ children }) => (
    <th className="px-4 py-3 text-left text-xs font-medium text-surface-300 uppercase tracking-wider">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-4 py-3 text-sm text-surface-400">{children}</td>
  ),
};
