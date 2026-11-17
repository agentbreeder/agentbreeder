import { defineConfig, defineDocs, defineCollections, frontmatterSchema } from 'fumadocs-mdx/config';
import { z } from 'zod';
import { brandShikiTheme } from './lib/shiki-theme';

export const docs = defineDocs({
  dir: 'content/docs',
});

export const blog = defineCollections({
  type: 'doc',
  dir: 'content/blog',
  schema: frontmatterSchema.extend({
    author: z.string(),
    date: z.string().date(),
    tags: z.array(z.string()).optional(),
    image: z.string().optional(),
  }),
});

export default defineConfig({
  mdxOptions: {
    rehypeCodeOptions: {
      themes: { light: brandShikiTheme, dark: brandShikiTheme },
    },
  },
});
