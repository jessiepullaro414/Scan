/** The OpenHandScan mark: the scanner's laser cross. */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <defs>
        <linearGradient id="ohs-logo-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6aa8ff" />
          <stop offset="1" stopColor="#2f6fe8" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="15" fill="#0f141b" />
      <rect x="2.5" y="2.5" width="59" height="59" rx="14.5" fill="none" stroke="#fff" strokeOpacity="0.14" />
      <path d="M32 13v38M13 32h38" stroke="url(#ohs-logo-g)" strokeWidth="5" strokeLinecap="round" />
      <circle cx="32" cy="32" r="9.5" fill="#0f141b" stroke="url(#ohs-logo-g)" strokeWidth="4" />
      <circle cx="32" cy="32" r="3" fill="#fff" />
    </svg>
  );
}
