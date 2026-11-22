import {createRoot} from 'react-dom/client';
import {HashRouter} from 'react-router-dom';
import {App} from './App';

const root = document.getElementById('root');
if (!root) throw new Error('Missing workbench root');

// Keep the static fallback intact until React replaces it in the first commit.
createRoot(root).render(<HashRouter><App /></HashRouter>);
