import { ImageResponse } from 'next/og';

export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 32,
          height: 32,
          borderRadius: 7,
          background: '#0d1527',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <svg width="22" height="22" viewBox="0 0 100 100" fill="none">
          <polygon
            points="86,50 68,81 32,81 14,50 32,19 68,19"
            fill="none"
            stroke="#22c55e"
            strokeWidth="4"
            strokeLinejoin="round"
          />
          <line x1="50" y1="50" x2="33" y2="34" stroke="#22c55e" strokeWidth="3" />
          <line x1="50" y1="50" x2="67" y2="34" stroke="#22c55e" strokeWidth="3" />
          <line x1="50" y1="50" x2="33" y2="66" stroke="#22c55e" strokeWidth="3" />
          <line x1="50" y1="50" x2="67" y2="66" stroke="#22c55e" strokeWidth="3" />
          <circle cx="50" cy="50" r="8" fill="#22c55e" />
          <circle cx="33" cy="34" r="6" fill="#22c55e" />
          <circle cx="67" cy="34" r="6" fill="#22c55e" />
          <circle cx="33" cy="66" r="6" fill="#22c55e" />
          <circle cx="67" cy="66" r="6" fill="#22c55e" />
        </svg>
      </div>
    ),
    { ...size }
  );
}
