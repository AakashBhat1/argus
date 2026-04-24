/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
      colors: {
        slate: {
          925: "#0d1117",
          950: "#090d14",
        },
        accent: {
          blue:    "#3b82f6",
          indigo:  "#6366f1",
          purple:  "#8b5cf6",
          green:   "#10b981",
          emerald: "#34d399",
          teal:    "#14b8a6",
          red:     "#ef4444",
          rose:    "#f43f5e",
          amber:   "#f59e0b",
          yellow:  "#eab308",
          cyan:    "#06b6d4",
        },
      },
      animation: {
        "pulse-slow":     "pulse 3s cubic-bezier(0.4,0,0.6,1) infinite",
        "fade-in":        "fadeIn 0.5s ease-out",
        "slide-up":       "slideUp 0.5s cubic-bezier(0.16,1,0.3,1)",
        "slide-in-right": "slideInRight 0.3s ease-out",
        "glow-pulse":     "glowPulse 2s ease-in-out infinite",
        "scan":           "scan 3s ease-in-out infinite",
        "float":          "float 6s ease-in-out infinite",
        "gradient":       "gradient 6s ease infinite",
        "gradient-x":     "gradientX 3s ease infinite",
        "border-glow":    "borderGlow 4s ease-in-out infinite",
        "shimmer-fast":   "shimmerFast 2s ease-in-out infinite",
        "counter":        "counter 1.5s cubic-bezier(0.16,1,0.3,1)",
        "spotlight":      "spotlight 4s ease-in-out infinite",
      },
      keyframes: {
        fadeIn:  {
          "0%":   { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        slideUp: {
          "0%":   { opacity: "0", transform: "translateY(16px) scale(0.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        slideInRight: {
          "0%":   { opacity: "0", transform: "translateX(-8px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        glowPulse: {
          "0%, 100%": { opacity: "0.4" },
          "50%":      { opacity: "1" },
        },
        scan: {
          "0%, 100%": { transform: "translateY(-100%)" },
          "50%":      { transform: "translateY(100%)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%":      { transform: "translateY(-12px)" },
        },
        gradient: {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%":      { backgroundPosition: "100% 50%" },
        },
        gradientX: {
          "0%":   { backgroundPosition: "0% 50%" },
          "50%":  { backgroundPosition: "100% 50%" },
          "100%": { backgroundPosition: "0% 50%" },
        },
        borderGlow: {
          "0%, 100%": { opacity: "0.5", transform: "rotate(0deg)" },
          "50%":      { opacity: "1", transform: "rotate(180deg)" },
        },
        shimmerFast: {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        counter: {
          "0%":   { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        spotlight: {
          "0%":   { opacity: "0", transform: "translateX(-100%)" },
          "50%":  { opacity: "1" },
          "100%": { opacity: "0", transform: "translateX(100%)" },
        },
      },
      boxShadow: {
        "glow-blue":   "0 0 20px rgba(59,130,246,0.3)",
        "glow-green":  "0 0 20px rgba(16,185,129,0.3)",
        "glow-red":    "0 0 20px rgba(239,68,68,0.25)",
        "glow-violet": "0 0 20px rgba(139,92,246,0.3)",
        "card":        "0 4px 24px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.04)",
        "card-hover":  "0 8px 40px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.06)",
        "panel":       "0 20px 60px rgba(0,0,0,0.5)",
        "glow-blue-lg":"0 0 40px rgba(59,130,246,0.15), 0 0 80px rgba(59,130,246,0.05)",
        "inner-glow":  "inset 0 1px 1px rgba(255,255,255,0.06), 0 4px 24px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};
