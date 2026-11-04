import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import WireWorkbench from './wire/WireWorkbench';
import './style.css';
import './wire/wire.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <WireWorkbench />
  </StrictMode>,
);
