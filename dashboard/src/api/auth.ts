import { api } from "./client";

interface LoginResponse {
  success: boolean;
  message: string;
  user_id: string;
  name: string;
  token: string;
}

export async function login(userId: string, password: string): Promise<LoginResponse> {
  return api.post<LoginResponse>("/auth/token", { user_id: userId, password });
}
