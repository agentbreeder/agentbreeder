import type { Metadata } from 'next';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';
import { getBlogPosts, getSlug } from '@/lib/blog';
import { ArrowRightIcon } from '@/components/icons';

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
      <main className="ab-container max-w-[860px] py-16 sm:py-20">
        <div className="mb-14">
          <p className="ab-label mb-3">blog</p>
          <h1 className="ab-h1 mb-4">Engineering &amp; ideas</h1>
          <p className="ab-lede max-w-[520px]">
            Thoughts on enterprise AI agents, platform engineering, open-source
            infrastructure, and the problems AgentBreeder was built to solve.
          </p>
        </div>

        {posts.length === 0 && <p className="text-muted-foreground">No posts yet. Check back soon.</p>}

        <ul className="m-0 flex list-none flex-col gap-6 p-0">
          {posts.map((post) => {
            const slug = getSlug(post);
            return (
              <li key={slug}>
                <Link href={`/blog/${slug}`} className="ab-card ab-card-link group block p-6 no-underline sm:p-8">
                  {post.tags && post.tags.length > 0 && (
                    <div className="mb-4 flex flex-wrap gap-2">
                      {post.tags.map((tag) => (
                        <span key={tag} className="ab-badge">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  <h2 className="ab-h3 mb-3 group-hover:underline group-hover:decoration-control-border group-hover:underline-offset-4">
                    {post.title}
                  </h2>
                  <p className="mb-6 text-[15px] leading-relaxed text-muted-foreground">{post.description}</p>
                  <div className="flex items-center gap-3 text-[13px] text-muted-foreground">
                    <span>{post.author}</span>
                    <span aria-hidden="true">·</span>
                    <time dateTime={post.date}>{formatDate(post.date)}</time>
                    <span className="ml-auto inline-flex items-center gap-1 font-medium text-foreground">
                      Read
                      <ArrowRightIcon size={14} />
                    </span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      </main>
      <Footer />
    </>
  );
}
