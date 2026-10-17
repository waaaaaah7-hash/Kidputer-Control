import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "./api";
import { useAuth } from "./auth";
import { deviceStatus } from "./format";
import { Icon, type IconName } from "./icons";
import { useLoader } from "./ui";

const MAIN_LINKS: { to: string; label: string; short: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Home", short: "Home", icon: "home", end: true },
  { to: "/activity", label: "Activity", short: "Activity", icon: "activity" },
  { to: "/account/notifications", label: "Notifications", short: "Alerts", icon: "bell" },
  { to: "/account", label: "Parents and account", short: "Account", icon: "person", end: true },
];

/**
 * The frame around every signed-in page. On a computer: a sidebar with the main links and the PCs. On a phone
 * (under 900 px): a slim top bar and a menu bar along the bottom, like a phone app.
 */
export default function Layout() {
  const { admin, logout } = useAuth();
  const { pathname } = useLocation();
  // Reloaded on every page change too, so a PC that was just added or renamed shows up.
  const devices = useLoader(() => api.devices(), [pathname], 30000);

  return (
    <div className="shell">
      <aside className="sidebar">
        <NavLink to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Kidputer Control
        </NavLink>
        <nav aria-label="Main" className="side-nav">
          {MAIN_LINKS.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end}>
              <Icon name={l.icon} /> {l.label}
            </NavLink>
          ))}
        </nav>
        <nav aria-label="Help" className="side-nav">
          <NavLink to="/help">
            <Icon name="help" /> Help and videos
          </NavLink>
        </nav>
        {(devices.data?.length ?? 0) > 0 && (
          <nav aria-label="PCs" className="side-nav">
            <p className="side-heading">PCs</p>
            {devices.data!.map((d) => {
              const status = deviceStatus(d);
              return (
                <NavLink key={d.id} to={`/devices/${d.id}`}>
                  <span className="grow">{d.name}</span>
                  <span className={`status-dot tone-${status.tone}`} title={status.label} />
                  <span className="sr-only">{status.label}</span>
                </NavLink>
              );
            })}
          </nav>
        )}
        <div className="user-box">
          <span className="grow small" title={admin?.email}>
            {admin?.name}
          </span>
          <button type="button" className="btn btn-ghost" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </aside>

      <header className="phone-bar">
        <NavLink to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Kidputer Control
        </NavLink>
        <div className="row">
          <NavLink to="/help" className="btn btn-ghost">
            <Icon name="help" size={18} /> Help
          </NavLink>
          <button type="button" className="btn btn-ghost" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </header>

      <main className="content">
        <Outlet />
      </main>

      <nav aria-label="Main" className="tabbar">
        {MAIN_LINKS.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end}>
            <Icon name={l.icon} size={22} />
            <span>{l.short}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
