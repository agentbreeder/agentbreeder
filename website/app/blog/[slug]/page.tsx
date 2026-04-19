import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
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
          <div className="relative h-8 w-8 shrink-0 overflow-hidden rounded-full">
            <Image src="/rajit-saha.jpg" alt={post.author} fill className="object-cover" sizes="32px" />
          </div>
          <Link
            href="https://www.linkedin.com/in/rajsaha/"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium no-underline hover:text-white"
            style={{ color: 'var(--accent)' }}
          >
            {post.author}
          </Link>
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

        {/* Author card */}
        <div
          className="mt-16 flex gap-5 rounded-2xl border p-6"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
        >
          <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-xl">
            <Image src="/rajit-saha.jpg" alt="Rajit Saha" fill className="object-cover" sizes="64px" />
          </div>
          <div>
            <div className="mb-0.5 flex items-center gap-2">
              <span className="font-bold text-white">Rajit Saha</span>
              <span
                className="rounded-full border px-2 py-0.5 text-[10px] font-semibold"
                style={{ background: 'var(--accent-dim)', borderColor: 'var(--accent-border)', color: 'var(--accent)' }}
              >
                Inventor &amp; Author
              </span>
            </div>
            <p className="mb-2 text-[12px]" style={{ color: 'var(--accent)' }}>
              Head of Data Intelligence Platform · Udemy
            </p>
            <p className="text-[13px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              20+ years turning passive data warehouses into active, agent-driven systems. Built AgentBreeder to solve the deployment problem he kept hitting in production.
            </p>
            <Link
              href="https://www.linkedin.com/in/rajsaha/"
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-flex items-center gap-1.5 text-[12px] font-medium no-underline transition-colors hover:text-white"
              style={{ color: 'var(--text-muted)' }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
              </svg>
              Connect on LinkedIn ↗
            </Link>
          </div>
        </div>

        {/* CTA */}
        <div
          className="mt-8 rounded-2xl border p-8"
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
