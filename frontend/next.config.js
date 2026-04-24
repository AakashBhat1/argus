/** @type {import('next').NextConfig} */
const backendOrigin =
  process.env.NEXT_PRIVATE_BACKEND_URL || "http://localhost:8000";
const chatbotOrigin =
  process.env.NEXT_PRIVATE_CHATBOT_URL || "http://localhost:8001";

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
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
      {
        source: "/chat",
        destination: `${chatbotOrigin}/chat`,
      },
      {
        source: "/health/chatbot",
        destination: `${chatbotOrigin}/health`,
      },
    ];
  },
};

module.exports = nextConfig;
