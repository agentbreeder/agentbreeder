import Link from 'next/link';

interface LogoProps {
  size?: number;
  showWordmark?: boolean;
  href?: string;
}

export function LogoIcon({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="36" height="36" rx="8" fill="#0a0f1e"/>
      <polygon points="18,4 29,10.5 29,23.5 18,30 7,23.5 7,10.5"
        fill="none" stroke="#22c55e" strokeWidth="1.5" opacity="0.55"/>
      <circle cx="18" cy="17" r="3.2" fill="#22c55e"/>
      <circle cx="12" cy="12.5" r="2" fill="#4ade80" opacity="0.7"/>
      <circle cx="24" cy="12.5" r="2" fill="#4ade80" opacity="0.7"/>
      <circle cx="12" cy="21.5" r="2" fill="#4ade80" opacity="0.5"/>
      <circle cx="24" cy="21.5" r="2" fill="#4ade80" opacity="0.5"/>
      <line x1="18" y1="17" x2="12" y2="12.5" stroke="#22c55e" strokeWidth="1" opacity="0.35"/>
      <line x1="18" y1="17" x2="24" y2="12.5" stroke="#22c55e" strokeWidth="1" opacity="0.35"/>
      <line x1="18" y1="17" x2="12" y2="21.5" stroke="#22c55e" strokeWidth="1" opacity="0.25"/>
      <line x1="18" y1="17" x2="24" y2="21.5" stroke="#22c55e" strokeWidth="1" opacity="0.25"/>
    </svg>
  );
}

export function Logo({ size = 28, showWordmark = true, href = '/' }: LogoProps) {
  return (
    <Link href={href} className="flex items-center gap-2 no-underline">
      <LogoIcon size={size} />
      {showWordmark && (
        <span
          className="font-extrabold text-[15px] text-white"
          style={{ letterSpacing: '-0.3px' }}
        >
          agent<span style={{ color: 'var(--accent)' }}>breeder</span>
        </span>
      )}
    </Link>
  );
}
