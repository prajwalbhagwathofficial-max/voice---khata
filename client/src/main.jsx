/**
 * Voice Khata - Client Entry Point
 * 
 * Initializes the Preact app, registers service worker for PWA,
 * and sets up the root render.
 */

import { render } from 'preact';
import { App } from './app.jsx';

// Mount the application
render(<App />, document.getElementById('app'));

// Register service worker for PWA support only in production
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) => {
      console.warn('Service worker registration failed:', err);
    });
  });
} else if ('serviceWorker' in navigator) {
  // In development, unregister any existing service worker to ensure fresh code
  navigator.serviceWorker.getRegistrations().then((registrations) => {
    for (const reg of registrations) reg.unregister();
  });
}
