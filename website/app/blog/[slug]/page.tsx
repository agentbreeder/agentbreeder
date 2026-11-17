import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';
import { getBlogPosts, getBlogPost, getSlug } from '@/lib/blog';
import { mdxComponents } from '@/components/mdx';
import { ArrowLeftIcon, ArrowRightIcon, GitHubIcon, LinkedInIcon, TriangleAlertIcon } from '@/components/icons';

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

  const url = `https://www.agentbreeder.io/blog/${slug}`;
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
      images: [{ url: post.image ?? '/og.png' }],
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

// Posts dated before the focused rebuild describe 2.x and get a historical notice.
const REBUILD_DATE = '2026-09-15';

export default async function BlogPostPage({ params }: Props) {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) notFound();

  const MDX = post.body;

  const url = `https://www.agentbreeder.io/blog/${slug}`;
  const blogPostingJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: post.title,
    description: post.description,
    datePublished: post.date,
    image: post.image
      ? `https://www.agentbreeder.io${post.image}`
      : 'https://www.agentbreeder.io/og.png',
    author: {
      '@type': 'Person',
      name: 'Rajit Saha',
    },
    mainEntityOfPage: url,
    publisher: {
      '@type': 'Organization',
      name: 'AgentBreeder',
      url: 'https://www.agentbreeder.io',
      logo: 'https://www.agentbreeder.io/og.png',
    },
  };

  return (
    <>
      {/* BlogPosting structured data — static, server-controlled frontmatter. */}
      <script type="application/ld+json">{JSON.stringify(blogPostingJsonLd)}</script>
      <Nav />
      <main className="ab-container max-w-[760px] py-12 sm:py-16">
        <Link
          href="/blog"
          className="mb-10 inline-flex min-h-[44px] items-center gap-1.5 text-[13px] text-muted-foreground no-underline transition-colors hover:text-foreground"
        >
          <ArrowLeftIcon size={14} />
          All posts
        </Link>

        {post.tags && post.tags.length > 0 && (
          <div className="mb-5 flex flex-wrap gap-2">
            {post.tags.map((tag) => (
              <span key={tag} className="ab-badge">
                {tag}
              </span>
            ))}
          </div>
        )}

        <h1 className="ab-h1 mb-5">{post.title}</h1>

        <p className="mb-8 text-lg leading-relaxed text-muted-foreground">{post.description}</p>

        <div className="mb-10 flex items-center gap-3 border-b border-border pb-8 text-[13px] text-muted-foreground">
          <div className="relative h-8 w-8 shrink-0 overflow-hidden rounded-full">
            <Image src="/rajit-saha.jpg" alt="" fill className="object-cover" sizes="32px" />
          </div>
          <Link
            href="https://www.linkedin.com/in/rajsaha/"
            target="_blank"
            rel="noopener noreferrer"
            className="ab-link font-medium"
          >
            {post.author}
          </Link>
          <span aria-hidden="true">·</span>
          <time dateTime={post.date}>{formatDate(post.date)}</time>
        </div>

        {/* Hero image (add /public/blog/why-i-built-agentbreeder/hero.png to enable) */}
        {post.image && (
          <div className="mb-12 overflow-hidden rounded-xl border border-border">
            <Image
              src={post.image}
              alt={post.title}
              width={1280}
              height={640}
              className="block h-auto w-full dark:brightness-[0.88]"
              sizes="(max-width: 768px) 100vw, 768px"
            />
          </div>
        )}

        {post.date < REBUILD_DATE && (
          <div
            role="note"
            className="mb-10 flex gap-3 rounded-xl border border-border bg-warning-subtle px-5 py-4 text-sm leading-[1.65] text-foreground"
          >
            <span className="mt-0.5 flex-shrink-0 text-warning">
              <TriangleAlertIcon size={16} />
            </span>
            <p className="m-0">
              <strong className="font-semibold">Historical post.</strong> This was written about
              AgentBreeder 2.x. Many product claims in it did not hold up in a 2026-11 end-to-end
              test, and 2.x is now unmaintained. For what AgentBreeder does today, see{' '}
              <Link href="/docs" className="ab-link">
                the docs
              </Link>{' '}
              and{' '}
              <Link href="/docs/legacy-2x" className="ab-link">
                Legacy 2.x
              </Link>
              .
            </p>
          </div>
        )}

        <article className="prose-blog">
          <MDX components={mdxComponents} />
        </article>

        <aside aria-label="About the author" className="ab-card mt-16 flex gap-5 p-6">
          <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-xl border border-border">
            <Image src="/rajit-saha.jpg" alt="Rajit Saha" fill className="object-cover" sizes="64px" />
          </div>
          <div>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="font-semibold text-foreground">Rajit Saha</span>
              <span className="ab-badge">inventor &amp; author</span>
            </div>
            <p className="ab-label mb-2">Director of Data Intelligence Platform · Udemy</p>
            <p className="text-sm leading-relaxed text-muted-foreground">
              20+ years turning passive data warehouses into active, agent-driven systems. Built AgentBreeder to solve the deployment problem he kept hitting in production.
            </p>
            <Link
              href="https://www.linkedin.com/in/rajsaha/"
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex min-h-[44px] items-center gap-1.5 text-[13px] font-medium text-muted-foreground no-underline transition-colors hover:text-foreground"
            >
              <LinkedInIcon size={12} />
              Connect on LinkedIn
            </Link>
          </div>
        </aside>

        <div className="ab-card mt-8 p-6 sm:p-8">
          <h2 className="ab-h3 mb-2">Try AgentBreeder</h2>
          <p className="mb-5 text-sm leading-relaxed text-muted-foreground">
            Open source, Apache 2.0. An alpha preview that deploys an agent container you already
            built, after its identity checks out.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/docs" className="ab-btn ab-btn-primary">
              Get started
              <ArrowRightIcon size={14} />
            </Link>
            <a
              href="https://github.com/agentbreeder/agentbreeder"
              target="_blank"
              rel="noopener noreferrer"
              className="ab-btn ab-btn-secondary"
            >
              <GitHubIcon />
              GitHub
            </a>
          </div>
        </div>

        <div className="mt-10">
          <Link
            href="/blog"
            className="inline-flex min-h-[44px] items-center gap-1.5 text-[13px] text-muted-foreground no-underline transition-colors hover:text-foreground"
          >
            <ArrowLeftIcon size={14} />
            Back to all posts
          </Link>
        </div>
      </main>
      <Footer />
    </>
  );
}
