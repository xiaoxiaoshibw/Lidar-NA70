import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  build: {
    rollupOptions: {
      input: { main: 'index.html', demo: 'demo.html', annotate: 'annotate.html', wire: 'wire.html', capture: 'capture.html' },
    },
  },
});
