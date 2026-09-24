/** @type {import('next').NextConfig} */
const backendOrigin =
  process.env.NEXT_PRIVATE_BACKEND_URL || "http://localhost:8000";
// The parking service runs separately (possibly on another host). Behind the
// production gateway nginx routes /api/v1/parking/* itself; this rewrite
// covers development and the all-in-one compose stack.
const parkingOrigin =
  process.env.NEXT_PRIVATE_PARKING_URL || "http://localhost:8100";

const path = require("path");

const nextConfig = {
  output: "standalone",
  // Pin Turbopack root to frontend so it doesn't use repo root (avoids scanning .venv, backend; fixes "node size increased" and startup issues)
  turbopack: {
    root: path.resolve(__dirname),
  },
  // Explicit distDir avoids Windows normalizePathOnWindows bug in Next 16
  distDir: ".next",
  async rewrites() {
    return [
      {
        source: "/api/v1/parking/:path*",
        destination: `${parkingOrigin}/api/v1/parking/:path*`,
      },
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
