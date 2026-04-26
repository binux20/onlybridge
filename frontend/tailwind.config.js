/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        'bg-1': 'var(--bg-elev-1)',
        'bg-2': 'var(--bg-elev-2)',
        'bg-hover': 'var(--bg-hover)',
        border: 'var(--border)',
        'border-soft': 'var(--border-soft)',
        text: 'var(--text)',
        'text-dim': 'var(--text-dim)',
        'text-muted': 'var(--text-muted)',
        accent: 'var(--accent)',
        'accent-hi': 'var(--accent-hi)',
        success: 'var(--success)',
        danger: 'var(--danger)',
        warn: 'var(--warn)',
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"IBM Plex Mono"', 'ui-monospace', 'Menlo', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', 'sans-serif'],
      },
      letterSpacing: {
        label: '0.08em',
        btn: '0.05em',
      },
      borderRadius: {
        sq: '2px',
      },
    },
  },
  plugins: [],
}
