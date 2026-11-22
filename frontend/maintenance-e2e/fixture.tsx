import {createRoot} from 'react-dom/client';
import {MaintenanceTools} from '../src/MaintenanceTools';
import '../src/styles.css';

createRoot(document.getElementById('root')!).render(<MaintenanceTools order={['a', 'b', 'carrier']} />);
