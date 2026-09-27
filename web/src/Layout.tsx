import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "./auth";

export default function Layout() {
  const { admin, logout } = useAuth();
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Session Monitor
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Devices
          </NavLink>
          <NavLink to="/activity">Activity</NavLink>
          <NavLink to="/account">Account</NavLink>
        </nav>
        <div className="topbar-user">
          <span className="muted small">{admin?.email}</span>
          <button type="button" className="btn btn-ghost" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
