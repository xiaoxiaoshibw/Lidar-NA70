import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import LocalCaptureWorkbench from './localcapture/LocalCaptureWorkbench';
import './style.css';
import './localcapture/local-capture.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LocalCaptureWorkbench />
  </StrictMode>,
);
