import { blog } from '@/.source/server';

export type BlogPost = (typeof blog)[number];

export function getSlug(post: BlogPost): string {
  return post.info.path.replace(/\.mdx$/, '');
}

export function getBlogPosts(): BlogPost[] {
  return [...blog].sort(
    (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime(),
  );
}

export function getBlogPost(slug: string): BlogPost | undefined {
  return blog.find((post) => getSlug(post) === slug);
}
