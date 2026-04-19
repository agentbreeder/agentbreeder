import Image from 'next/image';
import Link from 'next/link';

const CAREER_HIGHLIGHTS = [
  { label: '23+ years', desc: 'architecting enterprise data platforms' },
  { label: '8 companies', desc: 'Oracle · IBM · Yahoo · Teradata · VMware · LendingClub · Experian · Udemy' },
];

export function BuiltBy() {
  return (
    <section className="mx-auto max-w-[1200px] px-20 py-20">
      <p
        className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
        style={{ color: 'var(--accent)' }}
      >
        Built by
      </p>
      <h2
        className="mb-12 text-[36px] font-extrabold text-white"
        style={{ letterSpacing: '-1px' }}
      >
        The inventor
      </h2>

      <div
        className="rounded-[18px] border p-10"
        style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
      >
        <div className="flex gap-10 items-start">
          {/* Headshot */}
          <div className="flex-shrink-0">
            <div className="relative overflow-hidden rounded-2xl" style={{ width: 120, height: 120 }}>
              <Image
                src="/rajit-saha.jpg"
                alt="Rajit Saha"
                fill
                className="object-cover"
                sizes="120px"
              />
            </div>
            <div className="mt-3 text-center">
              <Link
                href="https://www.linkedin.com/in/rajsaha/"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-medium no-underline transition-all hover:border-[var(--accent)] hover:text-[var(--accent)]"
                style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
              >
                <LinkedInIcon />
                LinkedIn
              </Link>
            </div>
          </div>

          {/* Bio */}
          <div className="flex-1 min-w-0">
            <div className="mb-1 flex items-center gap-3">
              <h3 className="text-[22px] font-bold text-white">Rajit Saha</h3>
              <span
                className="rounded-full border px-2.5 py-0.5 text-[11px] font-semibold"
                style={{
                  background: 'var(--accent-dim)',
                  borderColor: 'var(--accent-border)',
                  color: 'var(--accent)',
                }}
              >
                Inventor &amp; Author
              </span>
            </div>
            <p className="mb-4 text-[13px]" style={{ color: 'var(--accent)' }}>
              Head of Data Intelligence Platform · Udemy
            </p>
            <p className="mb-4 text-[14px] leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
              Spent 20 years making data platforms bigger and faster. Then decided smarter was more interesting.
              At Udemy, shipped AI agents that actually do things in production.
              AgentBreeder is the tool I kept wishing existed while building them.
            </p>
            <p className="text-[14px] leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
              The pattern across 8 companies and 23 years: architect it, build the MVP personally, hand it to a
              great team to scale. Passive data warehouses had a good run. Active, agent-driven systems are what
              comes next — and that's what I'm building now.
            </p>

            {/* Stats row */}
            <div className="mt-8 grid grid-cols-2 gap-4">
              {CAREER_HIGHLIGHTS.map(({ label, desc }) => (
                <div
                  key={label}
                  className="rounded-[10px] border p-4"
                  style={{ background: 'var(--bg-base)', borderColor: 'var(--border)' }}
                >
                  <div className="mb-1 text-[18px] font-extrabold text-white" style={{ letterSpacing: '-0.5px' }}>
                    {label}
                  </div>
                  <div className="text-[11px] leading-[1.5]" style={{ color: 'var(--text-dim)' }}>
                    {desc}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function LinkedInIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
      <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
    </svg>
  );
}
