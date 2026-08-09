# Frontend Dockerfile for Smart Parking System (SvelteKit + adapter-node)
# Build context is the project root (context: .) — all paths are prefixed with web/.
# Multi-stage build for smaller final image.

# ---- Builder stage ----
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files first for better layer caching.
# Paths are relative to the root build context, so web/ prefix is required.
COPY web/package.json web/package-lock.json ./

# Install all dependencies (including devDeps needed for the build)
RUN npm ci

# Copy SvelteKit source (node_modules/ and build/ are excluded via root .dockerignore)
COPY web/ .

# Build the SvelteKit app (adapter-node)
RUN npm run build

# ---- Runner stage ----
FROM node:20-alpine AS runner

WORKDIR /app

# Copy the compiled server bundle and package manifest from builder
COPY --from=builder /app/build ./build
COPY --from=builder /app/package.json /app/package-lock.json ./

# Install only production runtime dependencies
RUN npm ci --omit=dev

# Environment for the Node server (adapter-node).
# ORIGIN must match the public-facing URL for SvelteKit CSRF protection.
# Override via docker-compose environment or -e ORIGIN=https://your-domain.
ENV NODE_ENV=production
ENV PORT=3000
ENV HOST=0.0.0.0
ENV ORIGIN=http://localhost

EXPOSE 3000

CMD ["node", "build/index.js"]
