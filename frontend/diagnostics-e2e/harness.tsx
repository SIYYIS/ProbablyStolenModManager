import {createRoot} from 'react-dom/client';
import '../src/styles.css';
import {DevPanel, DiagnosticsTools} from '../src/DiagnosticsTools';

createRoot(document.getElementById('root')!).render(<><DiagnosticsTools/><div className="fixture-settings"><DevPanel/></div></>);
