import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  basePath: "/slack-bot",
  images: { unoptimized: true },
};

export default nextConfig;
