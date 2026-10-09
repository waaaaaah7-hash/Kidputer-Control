import { useEffect } from "react";
import { api } from "./api";

/** While a page showing live data is open, keep the PC checking in every few seconds. */
export function useLiveDevice(deviceId: string): void {
  useEffect(() => {
    let stopped = false;
    const ping = () => {
      if (!stopped && document.visibilityState === "visible") void api.keepLive(deviceId).catch(() => {});
    };
    ping();
    const timer = window.setInterval(ping, 20000);
    document.addEventListener("visibilitychange", ping);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", ping);
    };
  }, [deviceId]);
}
