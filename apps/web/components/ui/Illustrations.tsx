/** Small, original flat illustrations for empty states (decorative: aria-hidden; the text says what to do). */
export type ArtName = "meds" | "records" | "family" | "chat";

export function Illustration({ name, className = "h-24 w-32" }: { name: ArtName; className?: string }) {
  const soft = "var(--surface-2)", brand = "var(--border-input)", accent = "var(--accent)", surface = "var(--surface)";
  return (
    <svg aria-hidden viewBox="0 0 160 120" className={className}>
      <ellipse cx="80" cy="104" rx="56" ry="8" fill={soft} />
      {name === "meds" && (
        <>
          <rect x="50" y="34" width="40" height="64" rx="8" fill={brand} />
          <rect x="46" y="24" width="48" height="14" rx="4" fill={accent} />
          <rect x="56" y="52" width="28" height="22" rx="3" fill={surface} />
          <path d="M66 63h8M70 59v8" stroke={brand} strokeWidth="3" strokeLinecap="round" />
          <g transform="rotate(-30 112 82)"><rect x="98" y="74" width="30" height="14" rx="7" fill={soft} stroke={brand} strokeWidth="2" />
            <path d="M113 74v14" stroke={brand} strokeWidth="2" /></g>
        </>
      )}
      {name === "records" && (
        <>
          <rect x="48" y="22" width="64" height="78" rx="8" fill={surface} stroke={brand} strokeWidth="3" />
          <rect x="66" y="16" width="28" height="12" rx="4" fill={accent} />
          <path d="M58 62h12l6-12 8 22 6-10h12" fill="none" stroke={brand} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M60 82h40M60 90h28" stroke={soft} strokeWidth="4" strokeLinecap="round" />
        </>
      )}
      {name === "family" && (
        <>
          <circle cx="56" cy="44" r="12" fill={brand} /><rect x="42" y="60" width="28" height="38" rx="12" fill={brand} />
          <circle cx="104" cy="44" r="12" fill={accent} /><rect x="90" y="60" width="28" height="38" rx="12" fill={accent} />
          <circle cx="80" cy="62" r="9" fill={soft} stroke={brand} strokeWidth="2" /><rect x="70" y="74" width="20" height="24" rx="9" fill={soft} stroke={brand} strokeWidth="2" />
        </>
      )}
      {name === "chat" && (
        <>
          <rect x="34" y="26" width="70" height="42" rx="14" fill={brand} /><path d="M50 68l-6 14 18-14z" fill={brand} />
          <rect x="70" y="52" width="58" height="36" rx="12" fill={soft} stroke={brand} strokeWidth="2" />
          <circle cx="88" cy="70" r="3" fill={brand} /><circle cx="99" cy="70" r="3" fill={brand} /><circle cx="110" cy="70" r="3" fill={brand} />
        </>
      )}
    </svg>
  );
}
