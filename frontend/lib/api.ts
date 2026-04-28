import type {
  ApiError,
  AuthTokenResponse,
  AuthUser,
  Conversation,
  ConversationDetail,
  OTPRequestResponse,
  TokenRefreshResponse,
} from "@/lib/types";

const API_BASE_URL = "http://127.0.0.1:8000";

export class HttpError extends Error {
  public status: number;
  public payload: ApiError | null;

  constructor(status: number, payload: ApiError | null) {
    super(payload?.detail || payload?.error || `Request failed with status ${status}`);
    this.status = status;
    this.payload = payload;
  }
}

async function request<T>(path: string, init?: RequestInit, accessToken?: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let payload: ApiError | null = null;
    try {
      payload = (await response.json()) as ApiError;
    } catch {
      payload = null;
    }
    throw new HttpError(response.status, payload);
  }

  return (await response.json()) as T;
}

export function requestOtp(phoneNumber: string) {
  return request<OTPRequestResponse>("/api/auth/request-otp/", {
    method: "POST",
    body: JSON.stringify({ phone_number: phoneNumber }),
  });
}

export function verifyOtp(payload: { phone_number: string; otp: string }) {
  return request<AuthTokenResponse>("/api/auth/verify-otp/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function refreshAuthToken(refresh: string) {
  return request<TokenRefreshResponse>("/api/auth/refresh/", {
    method: "POST",
    body: JSON.stringify({ refresh }),
  });
}

export function getMe(accessToken: string) {
  return request<AuthUser>("/api/auth/me/", undefined, accessToken);
}

export function listConversations(accessToken: string) {
  return request<Conversation[]>("/api/conversations/", undefined, accessToken);
}

export function getConversation(conversationId: string, accessToken: string) {
  return request<ConversationDetail>(`/api/conversations/${conversationId}/`, undefined, accessToken);
}

export function createConversation(accessToken: string, payload: { title?: string } = {}) {
  return request<ConversationDetail>("/api/conversations/", {
    method: "POST",
    body: JSON.stringify(payload),
  }, accessToken);
}

export function sendMessage(
  conversationId: string,
  accessToken: string,
  payload: {
    content: string;
    model?: string;
    system_instruction?: string;
  },
) {
  return request<ConversationDetail>(`/api/conversations/${conversationId}/messages/`, {
    method: "POST",
    body: JSON.stringify(payload),
  }, accessToken);
}
