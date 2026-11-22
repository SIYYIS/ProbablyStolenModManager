import { createRoot } from 'react-dom/client';
import { SourceTools } from './src/SourceTools';
import './src/styles.css';

createRoot(document.getElementById('root')!).render(<SourceTools requireLogin={false} notify={() => {}}
  onInstalled={() => { document.body.dataset.refreshed = 'true'; }} />);
