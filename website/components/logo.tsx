import Link from 'next/link';

interface LogoProps {
  size?: number;
  showWordmark?: boolean;
  href?: string;
}

// The mark (design v2 §3): its single stroke is the gate line, --brand; the nodes and the wordmark
// are --foreground. Colours come from tokens, so the mark follows the theme.
export function LogoIcon({ size = 28 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="text-foreground"
    >
      <polygon
        points="86,50 68,81 32,81 14,50 32,19 68,19"
        fill="none"
        className="stroke-brand forced-colors:stroke-[CanvasText]"
        strokeWidth="6"
        strokeLinejoin="round"
      />
      <g stroke="currentColor" strokeWidth="4" strokeLinecap="round">
        <line x1="50" y1="50" x2="33" y2="34" />
        <line x1="50" y1="50" x2="67" y2="34" />
        <line x1="50" y1="50" x2="33" y2="66" />
        <line x1="50" y1="50" x2="67" y2="66" />
      </g>
      <g fill="currentColor">
        <circle cx="50" cy="50" r="8" />
        <circle cx="33" cy="34" r="5.5" />
        <circle cx="67" cy="34" r="5.5" />
        <circle cx="33" cy="66" r="5.5" />
        <circle cx="67" cy="66" r="5.5" />
      </g>
    </svg>
  );
}

export function Logo({ size = 28, showWordmark = true, href = '/' }: LogoProps) {
  return (
    <Link href={href} className="flex min-h-[44px] items-center gap-2 text-foreground no-underline">
      <LogoIcon size={size} />
      {showWordmark && <span className="text-[15px] font-semibold tracking-[-0.02em]">agentbreeder</span>}
      {!showWordmark && <span className="sr-only">AgentBreeder home</span>}
    </Link>
  );
}
