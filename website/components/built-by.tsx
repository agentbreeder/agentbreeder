import Image from 'next/image';
import Link from 'next/link';
import { LinkedInIcon } from './icons';

const CAREER_HIGHLIGHTS = [
  { label: '23+ years', desc: 'industry experience with distributed systems & data infrastructure' },
  { label: '8 companies', desc: 'Oracle · IBM · Yahoo · Teradata · VMware · LendingClub · Experian · Udemy + Coursera' },
];

export function BuiltBy() {
  return (
    <section className="ab-section" aria-labelledby="built-by">
      <div className="ab-container">
        <p className="ab-label mb-3">built by</p>
        <h2 id="built-by" className="ab-h2 mb-12">
          The inventor
        </h2>

        <div className="ab-card p-6 sm:p-10">
          <div className="flex flex-col items-start gap-8 sm:flex-row sm:gap-10">
            <div className="flex flex-shrink-0 items-center gap-4 sm:flex-col sm:gap-0">
              <div className="relative h-[100px] w-[100px] overflow-hidden rounded-2xl border border-border">
                <Image src="/rajit-saha.jpg" alt="Rajit Saha" fill className="object-cover" sizes="100px" />
              </div>
              <div className="sm:mt-3 sm:text-center">
                <Link
                  href="https://www.linkedin.com/in/rajsaha/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ab-btn ab-btn-secondary min-h-[36px] px-3 text-xs"
                >
                  <LinkedInIcon />
                  LinkedIn
                </Link>
              </div>
            </div>

            <div className="min-w-0 flex-1">
              <div className="mb-1 flex flex-wrap items-center gap-3">
                <h3 className="ab-h3">Rajit Saha</h3>
                <span className="ab-badge">inventor &amp; author</span>
              </div>
              <p className="ab-label mb-4">Director of Data Platform · Udemy + Coursera</p>
              <p className="mb-4 text-[15px] leading-[1.75] text-muted-foreground">
                Spent 20 years making data platforms bigger and faster. Then decided smarter was more interesting.
                At Udemy + Coursera, shipped AI agents that actually do things in production.
                AgentBreeder is the tool I kept wishing existed while building them.
              </p>
              <p className="text-[15px] leading-[1.75] text-muted-foreground">
                The pattern across 8 companies and 23 years: architect it, build the MVP personally, hand it to a
                great team to scale. Passive data warehouses had a good run. Active, agent-driven systems are what
                comes next — and that&apos;s what I&apos;m building now.
              </p>

              <dl className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
                {CAREER_HIGHLIGHTS.map(({ label, desc }) => (
                  <div key={label} className="rounded-xl border border-border bg-background p-4">
                    <dt className="mb-1 text-lg font-semibold tracking-[-0.02em] text-foreground">{label}</dt>
                    <dd className="m-0 text-[13px] leading-[1.5] text-muted-foreground">{desc}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
