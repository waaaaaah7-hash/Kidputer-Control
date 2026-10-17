import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth";
import Layout from "./Layout";
import Account from "./pages/Account";
import Activity from "./pages/Activity";
import DeviceDetail from "./pages/DeviceDetail";
import Devices from "./pages/Devices";
import Invite from "./pages/Invite";
import Login from "./pages/Login";
import Help from "./pages/Help";
import Notifications from "./pages/Notifications";
import TotpSetup from "./pages/TotpSetup";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/setup-2fa" element={<TotpSetup />} />
      <Route path="/invite" element={<Invite />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Devices />} />
        <Route path="devices/:id" element={<DeviceDetail />} />
        <Route path="activity" element={<Activity />} />
        <Route path="help" element={<Help />} />
        <Route path="account" element={<Account />} />
        <Route path="account/notifications" element={<Notifications />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
