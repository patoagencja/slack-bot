import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  basePath: "/slack-bot/site",
  images: { unoptimized: true },
};

export default nextConfig;
