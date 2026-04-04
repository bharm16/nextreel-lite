/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.html"],
  darkMode: 'media',
  theme: {
    extend: {
      colors: {
        primary: '#1a1e27', // dark charcoal
        accent: '#e85d50',  // coral-red
      },
      fontFamily: {
        'brand-serif': ['Merriweather', 'Georgia', 'Cambria', 'Times New Roman', 'Times', 'serif'],
        'sans': ['Poppins', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      boxShadow: {
        'soft': '0 10px 25px rgba(0, 0, 0, 0.25)'
      }
    }
  },
  plugins: [],
}
