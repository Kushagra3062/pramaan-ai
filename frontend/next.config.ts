import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // The frontend is an independent package below a repository that also has a lockfile.
  // Keep build tracing local to this package instead of allowing Next to infer the root.
  outputFileTracingRoot: __dirname,
};

export default nextConfig;
