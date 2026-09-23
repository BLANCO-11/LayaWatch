/* Nav entry icons: geometric line icons, 16 px box, stroke 1.5, currentColor
 * (design-language 1.5). One per fixed entry in NAV_GROUPS; the collapsed rail
 * renders these alone, the expanded item pairs them with the label. */

const PATHS: Record<string, React.ReactNode> = {
  "/": (
    /* gauge: overview */
    <>
      <path d="M2.5 11.5a5.5 5.5 0 0 1 11 0" strokeLinecap="round" />
      <path d="M8 11.5l2.8-2.8" strokeLinecap="round" />
    </>
  ),
  "/traces": (
    /* branch: request routed through the engine */
    <>
      <circle cx="4" cy="4" r="1.9" />
      <circle cx="12" cy="12" r="1.9" />
      <path d="M5.9 4.6h3.2a2 2 0 0 1 2 2v3.5" strokeLinecap="round" />
    </>
  ),
  "/metrics": (
    /* bars: counters and rates */
    <>
      <path d="M3 13V8.5" strokeLinecap="round" />
      <path d="M8 13V3.5" strokeLinecap="round" />
      <path d="M13 13v-6" strokeLinecap="round" />
    </>
  ),
  "/logs": (
    /* terminal: log stream */
    <>
      <path d="M3 5l3 3-3 3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M9 11h4" strokeLinecap="round" />
    </>
  ),
  "/playground": (
    /* flask: run a one-off prediction */
    <>
      <path d="M6.5 2v4.2L3.2 12.4a1 1 0 0 0 .9 1.6h7.8a1 1 0 0 0 .9-1.6L9.5 6.2V2" strokeLinejoin="round" />
      <path d="M5.6 2h4.8" strokeLinecap="round" />
    </>
  ),
  "/checkpoints": (
    /* stack: checkpoint sets */
    <>
      <path d="M8 2l5.5 3L8 8 2.5 5z" strokeLinejoin="round" />
      <path d="M2.5 8.5L8 11.5l5.5-3" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  "/keys": (
    /* key: API credentials */
    <>
      <circle cx="5.5" cy="10.5" r="3" />
      <path d="M7.7 8.3L13.5 2.5" strokeLinecap="round" />
      <path d="M11 5l2 2" strokeLinecap="round" />
    </>
  ),
  "/users": (
    /* person: users and sessions */
    <>
      <circle cx="6" cy="5.5" r="2.5" />
      <path d="M1.8 13.5a4.2 4.2 0 0 1 8.4 0" strokeLinecap="round" />
      <path d="M11.4 13.5a4.2 4.2 0 0 0-1.1-2.8" strokeLinecap="round" />
    </>
  ),
  "/settings": (
    /* sliders: server preferences */
    <>
      <path d="M2.5 5.5h11" strokeLinecap="round" />
      <path d="M2.5 10.5h11" strokeLinecap="round" />
      <circle cx="6" cy="5.5" r="1.6" />
      <circle cx="10.5" cy="10.5" r="1.6" />
    </>
  ),
};

export default function NavIcon({ href }: { href: string }) {
  const path = PATHS[href];
  if (!path) return null;
  return (
    <svg
      className="lw-nav-icon"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      {path}
    </svg>
  );
}
