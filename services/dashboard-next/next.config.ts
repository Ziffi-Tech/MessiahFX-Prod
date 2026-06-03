import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Allow the dashboard to make requests to the gateway during build
  experimental: {
    // typedRoutes: true,  // enable once all routes are typed
  },
};

export default nextConfig;
