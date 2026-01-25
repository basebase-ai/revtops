/**
 * Individual blog post viewer
 */

import { useQuery } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchBlogPost, formatDate } from '../lib/markdown';
import type { Components } from 'react-markdown';

interface BlogPostProps {
  slug: string;
  onBack: () => void;
}

export function BlogPost({ slug, onBack }: BlogPostProps): JSX.Element {
  const { data, isLoading, error } = useQuery({
    queryKey: ['blog-post', slug],
    queryFn: () => fetchBlogPost(slug),
  });

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-surface-400">Loading post...</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex-1 flex items-center justify-center">
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
    <div className="flex-1 overflow-auto">
      <div className="max-w-3xl mx-auto px-6 py-8">
        {/* Back button */}
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-surface-400 hover:text-surface-200 mb-8 transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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
        <header className="mb-8">
          <h1 className="text-4xl font-bold text-surface-50 mb-4">{title}</h1>

          <div className="flex items-center gap-4 text-sm text-surface-400">
            {date && <time dateTime={date}>{formatDate(date)}</time>}
            {author && (
              <>
                <span>•</span>
                <span>{author}</span>
              </>
            )}
          </div>

          {tags.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-4">
              {tags.map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-1 text-xs rounded bg-surface-800 text-surface-300"
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
      </div>
    </div>
  );
}

/**
 * Custom components for markdown rendering
 * These use your site's styling and can be customized
 */
const markdownComponents: Components = {
  // Headings
  h1: ({ children }) => (
    <h1 className="text-3xl font-bold text-surface-50 mt-8 mb-4 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-2xl font-semibold text-surface-100 mt-8 mb-4">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-xl font-semibold text-surface-100 mt-6 mb-3">{children}</h3>
  ),

  // Paragraphs and text
  p: ({ children }) => <p className="text-surface-300 mb-4 leading-relaxed">{children}</p>,

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
    <ul className="list-disc list-inside mb-4 space-y-2 text-surface-300">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside mb-4 space-y-2 text-surface-300">
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
        className={`block p-4 rounded-lg bg-surface-900 text-surface-100 text-sm font-mono overflow-x-auto mb-4 ${className ?? ''}`}
        {...props}
      >
        {children}
      </code>
    );
  },

  // Blockquotes
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-primary-500 pl-4 py-2 mb-4 italic text-surface-300">
      {children}
    </blockquote>
  ),

  // Images
  img: ({ src, alt }) => (
    <img
      src={src}
      alt={alt}
      className="rounded-lg w-full my-6 border border-surface-800"
    />
  ),

  // Horizontal rule
  hr: () => <hr className="border-surface-800 my-8" />,

  // Tables
  table: ({ children }) => (
    <div className="overflow-x-auto mb-4">
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
