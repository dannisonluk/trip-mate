/** @type {import('next').NextConfig} */

// Security & Privacy Spec §4.1 / §5.1 — HTTP security headers.
const isProd = process.env.NODE_ENV === "production";

/**
 * `script-src` keeps `'unsafe-inline'`, and that is a deliberate, measured
 * decision rather than an oversight — see tech debt #4.
 *
 * A per-request nonce (the obvious fix) was implemented and tested: it works on
 * the two dynamically rendered routes, but this app prerenders 10 of its 12
 * routes at build time, and **prerendered HTML cannot carry a per-request
 * nonce**. In production that produced 18 CSP violations per static page —
 * blocking even the external chunk files, because `'strict-dynamic'` makes
 * `'self'` apply to nothing once a nonce is present. The app was unusable.
 *
 * So the choice is between a strict `script-src` and static prerendering; there
 * is no way to have both. Until that trade-off is decided deliberately, the
 * working configuration stands.
 */
const csp = [
  "default-src 'self'",
  "img-src 'self' data: blob: https:",
  // Next.js emits inline RSC hydration payloads; static prerendering rules out
  // the nonce that would otherwise replace this.
  `script-src 'self'${isProd ? "" : " 'unsafe-eval'"} 'unsafe-inline'`,
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  `connect-src 'self' ${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"} ws: wss:`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "geolocation=(), microphone=(), camera=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  {
    key: "Strict-Transport-Security",
    value: "max-age=31536000; includeSubDomains",
  },
];

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

module.exports = nextConfig;
