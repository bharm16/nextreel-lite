/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/**/*.js"
  ],
  darkMode: 'media',
  theme: {
    extend: {
      colors: {
        primary: '#02243f', // deep navy
        accent: '#2db830',  // brand green
      },
      fontFamily: {
        'brand-serif': ['Merriweather', 'Georgia', 'Cambria', 'Times New Roman', 'Times', 'serif'],
        'sans': ['Poppins', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      boxShadow: {
        'soft': '0 10px 25px rgba(2, 36, 63, 0.08)'
      }
    }
  },
  plugins: [],
}