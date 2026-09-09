/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep production builds separate from the local dev cache. This also avoids
  // OneDrive mixing generated chunks when CI-style builds run beside dev.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // This project lives in a OneDrive-synced folder. OneDrive intercepts file
  // renames, which breaks webpack's on-disk cache (ENOENT rename -> hung compiles).
  // Use an in-memory cache in dev to avoid the conflict.
  webpack: (config) => {
    // Do not let production builds write webpack's rename-heavy disk cache
    // either. OneDrive can leave a half-written build that serves HTML while
    // returning 404 for its CSS and runtime chunks.
    config.cache = false;
    return config;
  },
};

export default nextConfig;
