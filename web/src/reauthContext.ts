import { createContext, useContext } from "react";

/** Runs an action; if the server asks to confirm it's you first, shows the dialog and retries. */
export type ReauthRunner = <T>(action: () => Promise<T>) => Promise<T>;

export const ReauthContext = createContext<ReauthRunner>((action) => action());

export const useReauth = () => useContext(ReauthContext);
