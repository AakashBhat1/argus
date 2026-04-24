# ── Stage 1: Install dependencies ───────────────────────────────────────────
FROM node:20-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json* ./
# Install all deps (including dev) so Tailwind/PostCSS are available at build time
RUN npm ci

# ── Stage 2: Build ───────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Accept build-time API URLs so next.config.js can bake them in
ARG NEXT_PRIVATE_BACKEND_URL=http://backend:8000
ARG NEXT_PRIVATE_CHATBOT_URL=http://chatbot:8001
ARG NEXT_PUBLIC_API_URL=/api/v1
ARG NEXT_PUBLIC_CHATBOT_URL=/chat

ENV NEXT_PRIVATE_BACKEND_URL=${NEXT_PRIVATE_BACKEND_URL}
ENV NEXT_PRIVATE_CHATBOT_URL=${NEXT_PRIVATE_CHATBOT_URL}
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_CHATBOT_URL=${NEXT_PUBLIC_CHATBOT_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# ── Stage 3: Production runner ───────────────────────────────────────────────
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3001

# Non-root user
RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD wget -qO- http://$HOSTNAME:3001/ || exit 1

CMD ["node", "server.js"]
