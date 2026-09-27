import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth";
import Layout from "./Layout";
import Account from "./pages/Account";
import Activity from "./pages/Activity";
import DeviceDetail from "./pages/DeviceDetail";
import Devices from "./pages/Devices";
import Login from "./pages/Login";
import TotpSetup from "./pages/TotpSetup";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/setup-2fa" element={<TotpSetup />} />
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
        <Route path="account" element={<Account />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
