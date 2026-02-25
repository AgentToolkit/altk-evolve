import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import Namespaces from './components/Namespaces';
import EntityExplorer from './components/EntityExplorer';
import { LayoutDashboard, FolderTree } from 'lucide-react';

function Navigation() {
  const location = useLocation();

  return (
    <nav className="sidebar">
      <Link to="/" style={{ textDecoration: 'none', color: 'inherit' }}>
        <h1 className="logo">Kaizen</h1>
      </Link>
      <ul className="nav-links">
        <li>
          <Link to="/" className={`nav-link ${location.pathname === '/' ? 'active' : ''}`}>
            <LayoutDashboard size={20} /> Dashboard
          </Link>
        </li>
        <li>
          <Link to="/namespaces" className={`nav-link ${location.pathname.startsWith('/namespaces') ? 'active' : ''}`}>
            <FolderTree size={20} /> Namespaces
          </Link>
        </li>
      </ul>
    </nav>
  );
}

function App() {
  return (
    <Router>
      <div className="app-shell">
        <Navigation />
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/namespaces" element={<Namespaces />} />
            <Route path="/namespaces/:id/entities" element={<EntityExplorer />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
