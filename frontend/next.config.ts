import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {},
  output: "standalone",
  deploymentId: process.env.DEPLOYMENT_VERSION,
};

export default nextConfig;
