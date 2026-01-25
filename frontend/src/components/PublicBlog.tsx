/**
 * Public blog listing page (no authentication required)
 */

import { useQuery } from '@tanstack/react-query';
import { fetchBlogIndex, formatDate } from '../lib/markdown';
import type { PostMetadata } from '../lib/markdown';

interface PublicBlogProps {
  onSelectPost: (slug: string) => void;
}

export function PublicBlog({ onSelectPost }: PublicBlogProps): JSX.Element {
  const { data, isLoading, error } = useQuery({
    queryKey: ['blog-index'],
    queryFn: fetchBlogIndex,
  });

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-950">
        <div className="text-surface-400">Loading blog posts...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-950">
        <div className="text-center">
          <p className="text-red-400 mb-2">Failed to load blog</p>
          <p className="text-surface-400 text-sm">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </div>
      </div>
    );
  }

  const posts = data?.posts ?? [];

  return (
    <div className="min-h-screen bg-surface-950">
      {/* Gradient background effect */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-600/20 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-emerald-600/10 to-transparent blur-3xl" />
      </div>

      <div className="relative z-10 max-w-4xl mx-auto px-6 py-12">
        <div className="mb-12">
          <h1 className="text-5xl font-bold text-surface-50 mb-4">Blog</h1>
          <p className="text-xl text-surface-400">
            Insights, updates, and learnings from the Revtops team
          </p>
        </div>

        {posts.length === 0 ? (
          <div className="text-center py-12">
            <p className="text-surface-400">No blog posts yet. Check back soon!</p>
          </div>
        ) : (
          <div className="space-y-8">
            {posts.map((post) => (
              <BlogPostCard key={post.slug} post={post} onSelect={onSelectPost} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function BlogPostCard({
  post,
  onSelect,
}: {
  post: PostMetadata;
  onSelect: (slug: string) => void;
}): JSX.Element {
  return (
    <article
      onClick={() => onSelect(post.slug)}
      className="group cursor-pointer bg-surface-900/50 rounded-xl p-8 border border-surface-800 hover:border-primary-500/50 transition-all hover:shadow-xl hover:shadow-primary-500/5"
    >
      <div className="flex items-start justify-between gap-4 mb-3">
        <h2 className="text-3xl font-semibold text-surface-50 group-hover:text-primary-400 transition-colors">
          {post.title}
        </h2>
      </div>

      <div className="flex items-center gap-4 text-sm text-surface-400 mb-4">
        <time dateTime={post.date}>{formatDate(post.date)}</time>
        {post.author && (
          <>
            <span>•</span>
            <span>{post.author}</span>
          </>
        )}
      </div>

      {post.excerpt && (
        <p className="text-surface-300 mb-4 line-clamp-3 text-lg">{post.excerpt}</p>
      )}

      {post.tags && post.tags.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {post.tags.map((tag) => (
            <span
              key={tag}
              className="px-3 py-1 text-xs rounded-full bg-surface-800 text-surface-300 border border-surface-700"
            >
              {tag}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
