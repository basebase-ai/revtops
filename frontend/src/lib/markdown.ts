/**
 * Markdown and frontmatter parsing utilities
 */

export interface PostMetadata {
  slug: string;
  title: string;
  date: string;
  author?: string;
  tags?: string[];
  excerpt?: string;
  [key: string]: unknown;
}

export interface BlogIndex {
  posts: PostMetadata[];
  generated: string;
  count: number;
}

export interface ParsedMarkdown {
  metadata: Record<string, unknown>;
  content: string;
}

/**
 * Parse frontmatter from markdown content
 */
export function parseFrontmatter(rawContent: string): ParsedMarkdown {
  const frontmatterRegex = /^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$/;
  const match = rawContent.match(frontmatterRegex);

  if (!match) {
    return { metadata: {}, content: rawContent };
  }

  const [, frontmatterStr, content] = match;

  if (!frontmatterStr || !content) {
    return { metadata: {}, content: rawContent };
  }

  const metadata: Record<string, unknown> = {};

  // Parse YAML-like frontmatter
  frontmatterStr.split('\n').forEach((line) => {
    const colonIndex = line.indexOf(':');
    if (colonIndex === -1) return;

    const key = line.substring(0, colonIndex).trim();
    let value: string | string[] = line.substring(colonIndex + 1).trim();

    // Remove quotes
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    // Parse arrays
    if (value.startsWith('[') && value.endsWith(']')) {
      value = value
        .slice(1, -1)
        .split(',')
        .map((v) => {
          v = v.trim();
          return v.startsWith('"') ? v.slice(1, -1) : v;
        });
    }

    metadata[key] = value;
  });

  return { metadata, content: content.trim() };
}

/**
 * Fetch blog index
 */
export async function fetchBlogIndex(): Promise<BlogIndex> {
  const response = await fetch('/blog/index.json');
  if (!response.ok) {
    throw new Error('Failed to fetch blog index');
  }
  return response.json();
}

/**
 * Fetch a single blog post
 */
export async function fetchBlogPost(slug: string): Promise<ParsedMarkdown & { slug: string }> {
  const response = await fetch(`/blog/posts/${slug}.md`);
  if (!response.ok) {
    throw new Error(`Failed to fetch blog post: ${slug}`);
  }
  const content = await response.text();
  const parsed = parseFrontmatter(content);
  return { ...parsed, slug };
}

/**
 * Format date for display
 * Handles YYYY-MM-DD format as local date (no timezone conversion)
 */
export function formatDate(dateStr: string): string {
  try {
    // Parse YYYY-MM-DD as local date to avoid timezone issues
    const match = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (match && match[1] && match[2] && match[3]) {
      const year = parseInt(match[1], 10);
      const month = parseInt(match[2], 10) - 1; // Month is 0-indexed
      const day = parseInt(match[3], 10);
      const date = new Date(year, month, day);

      return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
    }

    // Fallback for other date formats
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return dateStr;
  }
}
