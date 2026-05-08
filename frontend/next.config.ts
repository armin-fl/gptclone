import type { NextConfig } from "next";

const publicDevHost = process.env.PUBLIC_DEV_HOST ?? "46.100.12.235";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["localhost", "127.0.0.1", "::1", publicDevHost],
};

export default nextConfig;
