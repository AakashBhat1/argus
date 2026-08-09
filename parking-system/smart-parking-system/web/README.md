# 🅿️ ParkSmart — SvelteKit Dashboard Rebuild

This directory contains the new, premium SvelteKit + TypeScript web frontend for the Smart Parking System. It is designed to act as a real-time, event-driven operations console.

---

## 🏗️ Architecture

- **`src/lib/api.ts`**: Contains full TypeScript definitions for the entire backend API contract, the API client, and a reactive global Svelte store (`sseStore`). It runs a single shared `EventSource` connection to `/api/events` and updates local state using fast, in-memory delta parsing.
- **`src/app.css`**: Defines design tokens (colors, typography, spacing, border-radii) and establishes a dense, responsive, slate-and-neon control-room console style system.
- **`src/routes/+layout.svelte`**: Scaffolds the layout shell (sidebar, navigation, live status, and connection banner). It initializes the shared EventSource connection and handles online/offline status indicators.
- **`src/routes/+page.svelte`**: Implements the live Operations Dashboard, consisting of real-time telemetry stats cards, a responsive SVG donut occupancy chart, and a live activity feed.

---

## 🚀 Running Locally

### Prerequisites

1. Ensure the Flask backend is configured and running:
   ```bash
   # In the root smart-parking-system directory
   venv\Scripts\activate
   python app.py
   ```
   The Flask server runs on `http://localhost:5000`.

### Dev Server

1. Navigate to the `web` directory:
   ```bash
   cd web
   ```
2. Install SvelteKit Node dependencies:
   ```bash
   npm install
   ```
3. Launch the SvelteKit development server:
   ```bash
   npm run dev
   ```
   Open **`http://localhost:5173`** in your browser.

---

## 🔌 Integration & Dev Proxy

Vite is configured to act as a dev proxy inside `vite.config.ts`. 

Any relative fetch request starting with `/api` or `/feed` is automatically proxied from the SvelteKit dev server (`localhost:5173`) to the Flask server (`localhost:5000`).

This setup:
- Eliminates CORS issues during development.
- Allows the frontend code to use relative URLs (e.g. `fetch('/api/stats')` and `new EventSource('/api/events')`) directly, matching same-origin production behavior.
- Means no configuration changes are required on the Flask backend.
