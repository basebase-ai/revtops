# Revtops Blog System

This directory contains the markdown-based blog system for Revtops.

## How It Works

1. **Write** markdown files in `posts/` directory
2. **Push** to GitHub (on the main branch)
3. **GitHub Action** automatically generates `index.json`
4. **React app** fetches and renders posts client-side

## Writing Blog Posts

### File Structure

Create a new markdown file in the `posts/` directory with a descriptive slug as the filename:

```
posts/
  └── my-awesome-post.md
```

### Frontmatter

Each post must start with YAML frontmatter:

```markdown
---
title: "Your Post Title"
date: 2026-01-24
author: "Your Name"
tags: ["tag1", "tag2"]
excerpt: "Optional custom excerpt (auto-generated if not provided)"
---

# Your Content Here

Write your post content in markdown...
```

### Required Fields

- `title`: The post title (displayed in listing and post header)
- `date`: Publication date in YYYY-MM-DD format (used for sorting)

### Optional Fields

- `author`: Author name (defaults to "Anonymous")
- `tags`: Array of tags (displayed as badges)
- `excerpt`: Custom excerpt (auto-generated from content if not provided)

### Markdown Features

The blog supports full GitHub-flavored markdown:

- **Headers**: `# H1`, `## H2`, etc.
- **Bold/Italic**: `**bold**`, `*italic*`
- **Links**: `[text](url)`
- **Lists**: Ordered and unordered
- **Code blocks**: With syntax highlighting
- **Tables**: Markdown tables
- **Images**: `![alt](url)`
- **Blockquotes**: `> quote`

### Example Post

```markdown
---
title: "Getting Started with Revtops"
date: 2026-01-24
author: "John Doe"
tags: ["tutorial", "getting-started"]
---

# Getting Started with Revtops

This is an introduction to using Revtops...

## Key Features

- Feature 1
- Feature 2

### Code Example

\`\`\`javascript
const example = "Hello World";
\`\`\`
```

## Local Development

### Generating the Index Locally

Run the generation script manually:

```bash
node scripts/generate-blog-index.js
```

This creates/updates `index.json` with all posts from the `posts/` directory.

### Testing

1. Generate the index: `node scripts/generate-blog-index.js`
2. Start the dev server: `cd frontend && npm run dev`
3. Navigate to the Blog section in the app

## Automatic Deployment

### GitHub Action

The blog index is automatically regenerated when:

- Markdown files in `frontend/public/blog/posts/` are modified
- Changes are pushed to the `main` branch
- The generation script is modified

The workflow file: `.github/workflows/generate-blog-index.yml`

### Manual Trigger

You can also trigger the workflow manually from the GitHub Actions tab.

## File Structure

```
frontend/public/blog/
├── README.md           ← This file
├── index.json          ← Auto-generated index (don't edit manually)
└── posts/
    ├── post-1.md
    ├── post-2.md
    └── ...
```

## Styling

Blog posts automatically use your site's styling through custom markdown components defined in `frontend/src/components/BlogPost.tsx`.

To customize the styling, edit the `markdownComponents` object in that file.

## Tips

1. **Use descriptive slugs**: The filename becomes the URL slug (e.g., `my-post.md` → `/blog/my-post`)
2. **Date format matters**: Use YYYY-MM-DD format for proper sorting
3. **Test locally**: Generate the index and test in dev mode before pushing
4. **Keep excerpts short**: Aim for 1-2 sentences if providing custom excerpts
5. **Tag consistently**: Use lowercase, hyphenated tags for consistency

## Troubleshooting

### Index not updating after push

Check the GitHub Actions tab to see if the workflow ran successfully. The workflow only triggers on changes to markdown files in `posts/` or the generation script.

### Post not showing up

1. Ensure the frontmatter is valid YAML
2. Verify the file is in the `posts/` directory
3. Check that `index.json` was regenerated
4. Hard refresh the browser (Cmd+Shift+R / Ctrl+Shift+R)

### Markdown not rendering correctly

The blog uses `react-markdown` with GitHub-flavored markdown support. Check the [react-markdown documentation](https://github.com/remarkjs/react-markdown) for supported syntax.
