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
    super(formatApiError(status, payload));
    this.status = status;
    this.payload = payload;
  }
}

function formatApiError(status: number, payload: ApiError | null): string {
  if (!payload) {
    return `Request failed with status ${status}`;
  }

  if (payload.detail || payload.error) {
    return String(payload.detail || payload.error);
  }

  const firstFieldError = Object.entries(payload).find(([, value]) => {
    if (Array.isArray(value)) {
      return value.length > 0;
    }
    return typeof value === "string";
  });

  if (firstFieldError) {
    const [field, value] = firstFieldError;
    const message = Array.isArray(value) ? value[0] : value;
    return `${field}: ${message}`;
  }

  return `Request failed with status ${status}`;
}

async function request<T>(path: string, init?: RequestInit, accessToken?: string): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
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

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function requestOtp(phoneNumber: string, authMode: "login" | "register") {
  return request<OTPRequestResponse>("/api/auth/request-otp/", {
    method: "POST",
    body: JSON.stringify({ phone_number: phoneNumber, auth_mode: authMode }),
  });
}

export function verifyOtp(payload: { phone_number: string; otp: string; auth_mode: "login" | "register" }) {
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

export function updateMe(accessToken: string, payload: FormData) {
  return request<AuthUser>("/api/auth/me/", {
    method: "PATCH",
    body: payload,
  }, accessToken);
}

export function requestPhoneChangeOtp(accessToken: string, phoneNumber: string) {
  return request<OTPRequestResponse>("/api/auth/change-phone/request-otp/", {
    method: "POST",
    body: JSON.stringify({ phone_number: phoneNumber }),
  }, accessToken);
}

export function verifyPhoneChangeOtp(
  accessToken: string,
  payload: { phone_number: string; otp: string },
) {
  return request<AuthUser>("/api/auth/change-phone/verify-otp/", {
    method: "POST",
    body: JSON.stringify(payload),
  }, accessToken);
}

export function listConversations(accessToken: string) {
  return request<Conversation[]>("/api/conversations/", undefined, accessToken);
}

export function getConversation(conversationId: string, accessToken: string) {
  return request<ConversationDetail>(`/api/conversations/${conversationId}/`, undefined, accessToken);
}

export function updateConversation(
  conversationId: string,
  accessToken: string,
  payload: { title?: string; is_pinned?: boolean },
) {
  return request<ConversationDetail>(`/api/conversations/${conversationId}/`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  }, accessToken);
}

export function deleteConversation(conversationId: string, accessToken: string) {
  return request<void>(`/api/conversations/${conversationId}/`, {
    method: "DELETE",
  }, accessToken);
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
