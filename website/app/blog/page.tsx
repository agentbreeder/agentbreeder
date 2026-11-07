import type { Metadata } from 'next';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';
import { getBlogPosts, getSlug } from '@/lib/blog';

export const metadata: Metadata = {
  title: 'Blog',
  description:
    'Thoughts on enterprise AI agents, platform engineering, and open-source from the AgentBreeder team.',
  openGraph: {
    title: 'AgentBreeder Blog',
    description:
      'Thoughts on enterprise AI agents, platform engineering, and open-source from the AgentBreeder team.',
    url: 'https://www.agentbreeder.io/blog',
  },
};

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

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

export default function BlogIndexPage() {
  const posts = getBlogPosts();

  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[860px] px-6 py-20">
        <div className="mb-16">
          <p
            className="mb-3 text-[11px] font-semibold uppercase tracking-[1.2px]"
            style={{ color: 'var(--accent)' }}
          >
            Blog
          </p>
          <h1 className="mb-4 text-[40px] font-bold leading-[1.15] tracking-tight text-white">
            Engineering & Ideas
          </h1>
          <p
            className="max-w-[520px] text-[16px] leading-relaxed"
            style={{ color: 'var(--text-muted)' }}
          >
            Thoughts on enterprise AI agents, platform engineering, open-source
            infrastructure, and the problems AgentBreeder was built to solve.
          </p>
        </div>

        {posts.length === 0 && (
          <p style={{ color: 'var(--text-muted)' }}>No posts yet. Check back soon.</p>
        )}

        <div className="flex flex-col gap-8">
          {posts.map((post) => {
            const slug = getSlug(post);
            return (
              <Link
                key={slug}
                href={`/blog/${slug}`}
                className="group block rounded-2xl border p-8 no-underline transition-colors"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                {/* Tags */}
                {post.tags && post.tags.length > 0 && (
                  <div className="mb-4 flex flex-wrap gap-2">
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
                <h2 className="mb-3 text-[22px] font-bold leading-[1.3] tracking-tight text-white transition-colors group-hover:text-[#22c55e]">
                  {post.title}
                </h2>

                {/* Description */}
                <p
                  className="mb-6 text-[15px] leading-relaxed"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {post.description}
                </p>

                {/* Meta */}
                <div
                  className="flex items-center gap-4 text-[13px]"
                  style={{ color: 'var(--text-dim)' }}
                >
                  <span>{post.author}</span>
                  <span>·</span>
                  <span>{formatDate(post.date)}</span>
                  <span
                    className="ml-auto text-[13px] font-medium"
                    style={{ color: 'var(--accent)' }}
                  >
                    Read →
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      </main>
      <Footer />
    </>
  );
}
