import type { NextConfig } from "next";

const apiProxyTarget =
  process.env.API_PROXY_TARGET ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    const target = apiProxyTarget.replace(/\/$/, "");

    return [
      {
        source: "/api/v1/:path*",
        destination: `${target}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
