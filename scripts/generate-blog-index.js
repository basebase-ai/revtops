#!/usr/bin/env node

/**
 * Generate blog index from markdown files
 * Scans frontend/public/blog/posts/*.md and creates index.json
 */

import { readFileSync, writeFileSync, readdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const BLOG_DIR = join(__dirname, '../frontend/public/blog');
const POSTS_DIR = join(BLOG_DIR, 'posts');
const INDEX_FILE = join(BLOG_DIR, 'index.json');

/**
 * Parse frontmatter from markdown content
 * Expected format:
 * ---
 * title: "Post Title"
 * date: 2026-01-15
 * author: "Author Name"
 * tags: ["tag1", "tag2"]
 * ---
 * Content here...
 */
function parseFrontmatter(content) {
  const frontmatterRegex = /^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$/;
  const match = content.match(frontmatterRegex);

  if (!match) {
    return { metadata: {}, content: content };
  }

  const [, frontmatterStr, markdownContent] = match;
  const metadata = {};

  // Parse YAML-like frontmatter
  frontmatterStr.split('\n').forEach(line => {
    const colonIndex = line.indexOf(':');
    if (colonIndex === -1) return;

    const key = line.substring(0, colonIndex).trim();
    let value = line.substring(colonIndex + 1).trim();

    // Remove quotes
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }

    // Parse arrays
    if (value.startsWith('[') && value.endsWith(']')) {
      value = value.slice(1, -1).split(',').map(v => {
        v = v.trim();
        return v.startsWith('"') ? v.slice(1, -1) : v;
      });
    }

    metadata[key] = value;
  });

  return { metadata, content: markdownContent.trim() };
}

/**
 * Generate excerpt from markdown content
 */
function generateExcerpt(content, maxLength = 200) {
  // Remove markdown syntax for excerpt
  let text = content
    .replace(/^#+\s+/gm, '') // Remove headers
    .replace(/\*\*(.+?)\*\*/g, '$1') // Remove bold
    .replace(/\*(.+?)\*/g, '$1') // Remove italic
    .replace(/\[(.+?)\]\(.+?\)/g, '$1') // Remove links
    .replace(/`(.+?)`/g, '$1') // Remove inline code
    .replace(/^\s*[-*+]\s+/gm, '') // Remove list markers
    .trim();

  if (text.length > maxLength) {
    text = text.substring(0, maxLength).trim() + '...';
  }

  return text;
}

/**
 * Main function
 */
function generateBlogIndex() {
  try {
    console.log('Scanning for markdown files in:', POSTS_DIR);

    const files = readdirSync(POSTS_DIR).filter(f => f.endsWith('.md'));
    console.log(`Found ${files.length} markdown files`);

    const posts = files.map(filename => {
      const filepath = join(POSTS_DIR, filename);
      const content = readFileSync(filepath, 'utf-8');
      const { metadata, content: markdownContent } = parseFrontmatter(content);

      // Generate slug from filename (remove .md extension)
      const slug = filename.replace(/\.md$/, '');

      // Extract excerpt if not provided
      const excerpt = metadata.excerpt || generateExcerpt(markdownContent);

      console.log(`  - ${slug}: ${metadata.title || 'Untitled'}`);

      return {
        slug,
        title: metadata.title || 'Untitled',
        date: metadata.date || new Date().toISOString().split('T')[0],
        author: metadata.author || 'Anonymous',
        tags: metadata.tags || [],
        excerpt,
        ...metadata // Include any additional metadata fields
      };
    });

    // Sort by date + time (newest first)
    // Combine date and time for accurate sorting
    posts.sort((a, b) => {
      const dateA = a.date || '1970-01-01';
      const timeA = a.time || '00:00:00';
      const dateB = b.date || '1970-01-01';
      const timeB = b.time || '00:00:00';

      // Normalize time format (add :00 seconds if not present)
      const normalizeTime = (time) => {
        const parts = time.split(':');
        if (parts.length === 2) return `${time}:00`;
        return time;
      };

      const dateTimeA = new Date(`${dateA}T${normalizeTime(timeA)}`);
      const dateTimeB = new Date(`${dateB}T${normalizeTime(timeB)}`);

      return dateTimeB - dateTimeA;
    });

    const index = {
      posts,
      generated: new Date().toISOString(),
      count: posts.length
    };

    writeFileSync(INDEX_FILE, JSON.stringify(index, null, 2));
    console.log(`\n✓ Generated index.json with ${posts.length} posts`);
    console.log(`  Output: ${INDEX_FILE}`);

  } catch (error) {
    console.error('Error generating blog index:', error.message);
    process.exit(1);
  }
}

generateBlogIndex();
