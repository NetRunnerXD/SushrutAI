import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async redirects() {
    // old flat routes -> new information architecture
    return [
      { source: "/triage", destination: "/ask/check", permanent: false },
      { source: "/prescriptions", destination: "/medicines/rx", permanent: false },
      { source: "/reports", destination: "/records", permanent: false },
      { source: "/profile", destination: "/me", permanent: false },
    ];
  },
  async headers() {
    return [{
      source: "/(.*)",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Permissions-Policy", value: "microphone=(self), camera=(self), geolocation=(self)" },
      ],
    }];
  },
};

export default nextConfig;
