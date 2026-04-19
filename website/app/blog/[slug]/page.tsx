import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';
import { getBlogPosts, getBlogPost, getSlug } from '@/lib/blog';
import defaultMdxComponents from 'fumadocs-ui/mdx';

interface Props {
  params: Promise<{ slug: string }>;
}

export async function generateStaticParams() {
  return getBlogPosts().map((post) => ({ slug: getSlug(post) }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) notFound();

  const url = `https://agent-breeder.com/blog/${slug}`;
  return {
    title: post.title,
    description: post.description,
    authors: [{ name: post.author }],
    openGraph: {
      type: 'article',
      title: post.title,
      description: post.description,
      url,
      publishedTime: post.date,
      authors: [post.author],
      ...(post.image ? { images: [{ url: post.image }] } : {}),
    },
    twitter: {
      card: 'summary_large_image',
      title: post.title,
      description: post.description,
    },
    alternates: { canonical: url },
  };
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

const TAG_PALETTE: Record<string, { bg: string; text: string }> = {
  enterprise: { bg: 'rgba(34,197,94,0.12)', text: '#22c55e' },
  'ai-agents': { bg: 'rgba(167,139,250,0.12)', text: '#a78bfa' },
  'open-source': { bg: 'rgba(34,197,94,0.12)', text: '#22c55e' },
  'platform-engineering': { bg: 'rgba(96,165,250,0.12)', text: '#60a5fa' },
  governance: { bg: 'rgba(251,146,60,0.12)', text: '#fb923c' },
};

function tagStyle(tag: string) {
  return TAG_PALETTE[tag] ?? { bg: 'rgba(255,255,255,0.06)', text: 'var(--text-muted)' };
}

export default async function BlogPostPage({ params }: Props) {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) notFound();

  const MDX = post.body;

  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[760px] px-6 py-16">
        {/* Back */}
        <Link
          href="/blog"
          className="mb-10 inline-flex items-center gap-1.5 text-[13px] no-underline transition-colors hover:text-white"
          style={{ color: 'var(--text-muted)' }}
        >
          ← All posts
        </Link>

        {/* Tags */}
        {post.tags && post.tags.length > 0 && (
          <div className="mb-5 flex flex-wrap gap-2">
            {post.tags.map((tag) => {
              const c = tagStyle(tag);
              return (
                <span
                  key={tag}
                  className="rounded-full px-2.5 py-0.5 text-[11px] font-medium"
                  style={{ background: c.bg, color: c.text }}
                >
                  {tag}
                </span>
              );
            })}
          </div>
        )}

        {/* Title */}
        <h1 className="mb-5 text-[36px] font-bold leading-[1.2] tracking-tight text-white">
          {post.title}
        </h1>

        {/* Description */}
        <p className="mb-8 text-[18px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          {post.description}
        </p>

        {/* Author + date */}
        <div
          className="mb-10 flex items-center gap-3 border-b pb-8 text-[13px]"
          style={{ borderColor: 'var(--border)', color: 'var(--text-dim)' }}
        >
          <div
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-black"
            style={{ background: 'var(--accent)' }}
          >
            {post.author[0]}
          </div>
          <span className="text-white">{post.author}</span>
          <span>·</span>
          <span>{formatDate(post.date)}</span>
        </div>

        {/* Hero image (add /public/blog/why-i-built-agentbreeder/hero.png to enable) */}
        {post.image && (
          <div
            className="mb-12 overflow-hidden rounded-2xl border"
            style={{ borderColor: 'var(--border)' }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={post.image} alt={post.title} className="block w-full" />
          </div>
        )}

        {/* Body */}
        <article className="prose-blog">
          <MDX components={{ ...defaultMdxComponents }} />
        </article>

        {/* CTA */}
        <div
          className="mt-16 rounded-2xl border p-8"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--accent-border)' }}
        >
          <h3 className="mb-2 text-[18px] font-bold text-white">Try AgentBreeder</h3>
          <p className="mb-5 text-[14px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            Open-source, Apache 2.0. Define once, deploy anywhere, govern automatically.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link
              href="/docs"
              className="rounded-lg px-4 py-2 text-sm font-bold no-underline transition-opacity hover:opacity-90"
              style={{ background: 'var(--accent)', color: '#000' }}
            >
              Get Started →
            </Link>
            <a
              href="https://github.com/rajitsaha/agentbreeder"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-lg border px-4 py-2 text-sm no-underline transition-colors hover:text-white"
              style={{ borderColor: 'var(--border-hover)', color: 'var(--text-muted)' }}
            >
              ★ GitHub
            </a>
          </div>
        </div>

        <div className="mt-10">
          <Link
            href="/blog"
            className="text-[13px] no-underline transition-colors hover:text-white"
            style={{ color: 'var(--text-muted)' }}
          >
            ← Back to all posts
          </Link>
        </div>
      </main>
      <Footer />
    </>
  );
}
