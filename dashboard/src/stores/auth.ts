import { create } from "zustand";
import { setToken, getToken } from "@/api/client";

interface AuthState {
  token: string | null;
  userId: string | null;
  userName: string | null;
  isAuthenticated: boolean;
  login: (token: string, userId: string, userName: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: getToken(),
  userId: localStorage.getItem("gbot_user_id"),
  userName: localStorage.getItem("gbot_user_name"),
  isAuthenticated: !!getToken(),
  login: (token, userId, userName) => {
    setToken(token);
    localStorage.setItem("gbot_user_id", userId);
    localStorage.setItem("gbot_user_name", userName);
    set({ token, userId, userName, isAuthenticated: true });
  },
  logout: () => {
    setToken(null);
    localStorage.removeItem("gbot_user_id");
    localStorage.removeItem("gbot_user_name");
    set({ token: null, userId: null, userName: null, isAuthenticated: false });
  },
}));
