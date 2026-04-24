/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js"],
  darkMode: 'media',
  theme: {
    extend: {
      colors: {
        primary: '#181818',
        accent: '#c67a5c',
      },
      fontFamily: {
        'brand-serif': ['Merriweather', 'Georgia', 'Cambria', 'Times New Roman', 'Times', 'serif'],
        'sans': ['DM Sans', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      boxShadow: {
        'soft': 'none'
      }
    }
  },
  plugins: [],
}
