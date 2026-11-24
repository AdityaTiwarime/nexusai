/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#dbe7ff",
          500: "#3b6cf6",
          600: "#2a55e0",
          700: "#2245b5",
          900: "#172554",
        },
      },
    },
  },
  plugins: [],
};
