import type { NextConfig } from "next";

const exporting = process.env.NEXT_OUTPUT === "export";

const nextConfig: NextConfig = {
  output: exporting ? "export" : undefined,
  outputFileTracingRoot: __dirname,
  trailingSlash: true,
  images: { unoptimized: true },
  ...(exporting
    ? {}
    : {
        async rewrites() {
          return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
        },
      }),
};

export default nextConfig;
