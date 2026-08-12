/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Neutral greys and exactly one accent, per the brief. Values are the
        // validated chart palette so the sparkline and the UI agree.
        surface: '#ffffff',
        page: '#f9f9f7',
        hairline: '#e1e0d9',
        ink: {
          DEFAULT: '#0b0b0b',
          secondary: '#52514e',
          muted: '#898781',
        },
        accent: {
          DEFAULT: '#2a78d6',
          hover: '#256abf',
          soft: '#eaf2fd',
          ring: '#b7d3f6',
        },
        // Reserved status roles. Never reused for anything else, and never the
        // only carrier of meaning -- every use ships a dot and a text label.
        status: {
          good: '#0ca30c',
          goodInk: '#0a6b0a',
          goodSoft: '#e8f6e8',
          warn: '#fab219',
          warnInk: '#8a5a00',
          warnSoft: '#fdf3e0',
          bad: '#d03b3b',
          badInk: '#a32b2b',
          badSoft: '#fbeaea',
        },
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};
