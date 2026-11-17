'use client';

import { useEffect, useRef, useState, type ComponentProps } from 'react';

// Wide tables scroll inside their wrapper. Only a wrapper that overflows joins the tab order, so
// keyboard users can scroll it (axe: scrollable-region-focusable) without an extra stop on every
// table that fits.
export function ScrollTable(props: ComponentProps<'table'>) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setScrolls(el.scrollWidth > el.clientWidth + 1);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={ref} className="relative my-6 overflow-auto prose-no-margin" tabIndex={scrolls ? 0 : undefined}>
      <table {...props} />
    </div>
  );
}
