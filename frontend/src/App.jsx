/**
 * ORBIS App root.
 *
 * This is intentionally minimal — all state, gateway logic, and visual identity
 * are owned by OrbisCore. App.jsx exists only to mount OrbisCore inside the
 * page-level shell and apply the outermost layout class.
 */
import { OrbisCore } from './components/OrbisCore';


export function App() {
  return (
    <div className="orbis-app">
      <OrbisCore />
    </div>
  );
}

export default App;
