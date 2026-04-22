import type { ApiError, Conversation, ConversationDetail } from "@/lib/types";

const API_BASE_URL = "http://127.0.0.1:8000";

class HttpError extends Error {
  public status: number;
  public payload: ApiError | null;

  constructor(status: number, payload: ApiError | null) {
    super(payload?.detail || payload?.error || `Request failed with status ${status}`);
    this.status = status;
    this.payload = payload;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
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

function withPhoneNumber(path: string, phoneNumber?: string): string {
  if (!phoneNumber) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}phone_number=${encodeURIComponent(phoneNumber)}`;
}

export function listConversations(phoneNumber?: string) {
  return request<Conversation[]>(withPhoneNumber("/api/conversations/", phoneNumber));
}

export function getConversation(conversationId: string, phoneNumber?: string) {
  return request<ConversationDetail>(
    withPhoneNumber(`/api/conversations/${conversationId}/`, phoneNumber),
  );
}

export function createConversation(payload: { title?: string; phone_number?: string }) {
  return request<ConversationDetail>("/api/conversations/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function sendMessage(
  conversationId: string,
  payload: {
    content: string;
    phone_number?: string;
    model?: string;
    system_instruction?: string;
  },
) {
  return request<ConversationDetail>(`/api/conversations/${conversationId}/messages/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
