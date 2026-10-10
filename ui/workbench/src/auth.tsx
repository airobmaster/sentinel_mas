// Sign-in. With Cognito: the hosted sign-in page, authorization code + PKCE (oidc-client-ts); the
// user's groups are their roles. In dev mode (no Cognito): pick a test user's role.
import { useQueryClient } from "@tanstack/react-query";
import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, onUnauthorized, setAccessToken } from "./api";
import type { Me } from "./types";

type Mode = "dev" | "cognito";

interface AuthState {
  mode: Mode | null;
  me: Me | null;
  demoRoleSwitch: boolean;
  switchRole: (role: string) => Promise<void>;
  loading: boolean;
  error: string | null;
  signIn: (role?: string) => Promise<void>;
  signOut: () => Promise<void>;
  completeSignIn: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);
const DEV_TOKEN_KEY = "sentinel.devToken"; // dev-mode and demo role-switch tokens

let manager: UserManager | null = null;
let cognitoDomain: string | undefined;
let cognitoClientId: string | undefined;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [demoRoleSwitch, setDemoRoleSwitch] = useState(false);

  const queryClient = useQueryClient();
  const applyToken = useCallback(async (token: string | null) => {
    setAccessToken(token);
    queryClient.clear(); // another user may see (and do) different things: never reuse their data
    setMe(token ? await api.me() : null);
  }, [queryClient]);

  useEffect(() => {
    (async () => {
      try {
        const config = await api.authConfig();
        setMode(config.mode);
        setDemoRoleSwitch(!!config.demo_role_switch);
        if (config.mode === "cognito") {
          cognitoDomain = config.domain;
          cognitoClientId = config.client_id;
          manager = new UserManager({
            authority: config.authority!,
            client_id: config.client_id!,
            redirect_uri: `${window.location.origin}/callback`,
            post_logout_redirect_uri: `${window.location.origin}/`,
            response_type: "code",
            scope: "openid email profile",
            userStore: new WebStorageStateStore({ store: window.sessionStorage }),
            automaticSilentRenew: true,
          });
          manager.events.addUserLoaded((user) => setAccessToken(user.access_token));
        }
        const demoToken = sessionStorage.getItem(DEV_TOKEN_KEY);
        if (demoToken) { // a role chosen from the demo dropdown (or dev mode) wins over the Cognito session
          try {
            await applyToken(demoToken);
            return;
          } catch {
            sessionStorage.removeItem(DEV_TOKEN_KEY);
          }
        }
        const user = manager ? await manager.getUser() : null;
        if (user && !user.expired) await applyToken(user.access_token);
      } catch (e) {
        setError(`Sentinel API not reachable: ${(e as Error).message}`);
      } finally {
        setLoading(false);
      }
    })();
  }, [applyToken]);

  const signIn = useCallback(async (role?: string) => {
    if (mode === "cognito") {
      await manager!.signinRedirect();
      return;
    }
    const { access_token } = await api.devToken(role ?? "l2");
    sessionStorage.setItem(DEV_TOKEN_KEY, access_token);
    await applyToken(access_token);
  }, [mode, applyToken]);

  const switchRole = useCallback(async (role: string) => {
    const { access_token } = await api.demoSignIn(role);
    sessionStorage.setItem(DEV_TOKEN_KEY, access_token);
    await applyToken(access_token);
  }, [applyToken]);

  const completeSignIn = useCallback(async () => {
    const user = await manager!.signinRedirectCallback();
    await applyToken(user.access_token);
  }, [applyToken]);

  const signOut = useCallback(async () => {
    const demo = !!sessionStorage.getItem(DEV_TOKEN_KEY);
    sessionStorage.removeItem(DEV_TOKEN_KEY);
    await applyToken(null);
    if (mode === "cognito" && !demo) {
      await manager!.removeUser();
      // Cognito's own logout endpoint ends the hosted session too, so "switch user" really switches
      window.location.href = `${cognitoDomain}/logout?client_id=${cognitoClientId}` +
        `&logout_uri=${encodeURIComponent(`${window.location.origin}/`)}`;
    }
  }, [mode, applyToken]);

  useEffect(() => onUnauthorized(() => { // an expired token: back to the sign-in page
    sessionStorage.removeItem(DEV_TOKEN_KEY);
    setAccessToken(null);
    setMe(null);
  }), []);

  const value = useMemo(() => ({ mode, me, loading, error, demoRoleSwitch, switchRole, signIn, signOut, completeSignIn }),
    [mode, me, loading, error, demoRoleSwitch, switchRole, signIn, signOut, completeSignIn]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

export function hasRole(me: Me | null, ...roles: string[]): boolean {
  return !!me && me.roles.some((r) => roles.includes(r));
}
