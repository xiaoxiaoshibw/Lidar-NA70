import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import AnnotateApp from './AnnotateApp';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AnnotateApp />
  </StrictMode>,
);
