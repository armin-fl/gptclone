import type {
  ApiError,
  AuthTokenResponse,
  AuthUser,
  ChatStreamEvent,
  ConversationPage,
  ConversationDetail,
  ImageGenerationResponse,
  ImageModelsResponse,
  InitialChatData,
  KnowledgeDocument,
  KnowledgeDocumentsResponse,
  KnowledgeSearchResponse,
  LlmModelsResponse,
  OTPRequestResponse,
  ThinkingEffort,
  TokenRefreshResponse,
} from "@/lib/types";

const CSRF_COOKIE = "chat_csrf_token";

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

async function readErrorPayload(response: Response): Promise<ApiError | null> {
  try {
    return (await response.json()) as ApiError;
  } catch {
    return null;
  }
}

function getCookie(name: string): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const match = document.cookie
    .split("; ")
    .find((part) => part.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

async function ensureCsrfToken(): Promise<string> {
  const existing = getCookie(CSRF_COOKIE);
  if (existing) {
    return existing;
  }
  await request<InitialChatData>("/api/auth/session", { method: "GET" }, { skipCsrf: true });
  const refreshed = getCookie(CSRF_COOKIE);
  if (!refreshed) {
    throw new Error("Could not initialize CSRF protection.");
  }
  return refreshed;
}

interface RequestOptions {
  skipCsrf?: boolean;
}

async function request<T>(path: string, init?: RequestInit, options: RequestOptions = {}): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const method = init?.method?.toUpperCase() ?? "GET";
  const isUnsafe = method !== "GET" && method !== "HEAD";
  const csrfToken = isUnsafe && !options.skipCsrf ? await ensureCsrfToken() : null;
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
    credentials: "same-origin",
  });

  if (!response.ok) {
    const payload = await readErrorPayload(response);
    throw new HttpError(response.status, payload);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function getSession() {
  return request<InitialChatData>("/api/auth/session");
}

export function signOut() {
  return request<{ detail: string }>("/api/auth/session", {
    method: "DELETE",
  });
}

export function requestOtp(phoneNumber: string, authMode: "login" | "register") {
  return request<OTPRequestResponse>("/api/auth/request-otp", {
    method: "POST",
    body: JSON.stringify({ phone_number: phoneNumber, auth_mode: authMode }),
  });
}

export function verifyOtp(payload: { phone_number: string; otp: string; auth_mode: "login" | "register" }) {
  return request<AuthTokenResponse>("/api/auth/verify-otp", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function refreshAuthToken() {
  return request<TokenRefreshResponse>("/api/auth/refresh", {
    method: "POST",
  });
}

export function getMe() {
  return request<AuthUser>("/api/auth/me");
}

export function updateMe(payload: FormData) {
  return request<AuthUser>("/api/auth/me", {
    method: "PATCH",
    body: payload,
  });
}

export function requestPhoneChangeOtp(phoneNumber: string) {
  return request<OTPRequestResponse>("/api/auth/change-phone/request-otp", {
    method: "POST",
    body: JSON.stringify({ phone_number: phoneNumber }),
  });
}

export function verifyPhoneChangeOtp(payload: { phone_number: string; otp: string }) {
  return request<AuthUser>("/api/auth/change-phone/verify-otp", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listConversations(
  params: { cursor?: string | null; limit?: number; q?: string } = {},
) {
  const search = new URLSearchParams();
  if (params.cursor) {
    search.set("cursor", params.cursor);
  }
  if (params.limit) {
    search.set("limit", String(params.limit));
  }
  if (params.q?.trim()) {
    search.set("q", params.q.trim());
  }
  const suffix = search.toString();
  return request<ConversationPage>(`/api/conversations${suffix ? `?${suffix}` : ""}`);
}

export function listModels() {
  return request<LlmModelsResponse>("/api/models");
}

export function listImageModels() {
  return request<ImageModelsResponse>("/api/image-models");
}

export function generateImage(payload: {
  prompt: string;
  model?: string;
  n?: number;
  size?: string;
  negative_prompt?: string;
  num_inference_steps?: number;
  guidance_scale?: number;
  true_cfg_scale?: number;
  seed?: number;
}) {
  return request<ImageGenerationResponse>("/api/image-generations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listKnowledgeDocuments() {
  return request<KnowledgeDocumentsResponse>("/api/knowledge/documents");
}

export function createKnowledgeDocument(payload: {
  title?: string;
  source_name?: string;
  content: string;
}) {
  return request<KnowledgeDocument>("/api/knowledge/documents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteKnowledgeDocument(documentId: string) {
  return request<void>(`/api/knowledge/documents/${documentId}`, {
    method: "DELETE",
  });
}

export function searchKnowledge(payload: { query: string; top_k?: number }) {
  return request<KnowledgeSearchResponse>("/api/knowledge/search", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getConversation(
  conversationId: string,
  params: { before?: string | null; limit?: number } = {},
) {
  const search = new URLSearchParams();
  if (params.before) {
    search.set("before", params.before);
  }
  if (params.limit) {
    search.set("limit", String(params.limit));
  }
  const suffix = search.toString();
  return request<ConversationDetail>(`/api/conversations/${conversationId}${suffix ? `?${suffix}` : ""}`);
}

export function updateConversation(
  conversationId: string,
  payload: { title?: string; is_pinned?: boolean },
) {
  return request<ConversationDetail>(`/api/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteConversation(conversationId: string) {
  return request<void>(`/api/conversations/${conversationId}`, {
    method: "DELETE",
  });
}

export function createConversation(payload: { title?: string } = {}) {
  return request<ConversationDetail>("/api/conversations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function forkConversationFromMessage(
  conversationId: string,
  messageId: number,
) {
  return request<ConversationDetail>(`/api/conversations/${conversationId}/messages/${messageId}/fork`, {
    method: "POST",
  });
}

async function streamChatResponse(
  path: string,
  payload: Record<string, unknown>,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": await ensureCsrfToken(),
    },
    body: JSON.stringify({ ...payload, stream: true }),
    cache: "no-store",
    credentials: "same-origin",
    signal,
  });

  if (!response.ok) {
    const payload = await readErrorPayload(response);
    throw new HttpError(response.status, payload);
  }

  if (!response.body) {
    throw new Error("Streaming response is not available in this browser.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  async function emitLine(line: string) {
    const clean = line.trim();
    if (!clean) {
      return;
    }

    let event: ChatStreamEvent;
    try {
      event = JSON.parse(clean) as ChatStreamEvent;
    } catch {
      throw new Error("Received malformed streaming response.");
    }

    onEvent(event);
    if (event.type === "error") {
      throw new Error(event.error || event.detail);
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      await emitLine(line);
    }
  }

  buffer += decoder.decode();
  await emitLine(buffer);
}

export async function streamMessage(
  conversationId: string,
  payload: {
    content: string;
    model?: string;
    system_instruction?: string;
    rag_enabled?: boolean;
    thinking_enabled?: boolean;
    thinking_effort?: ThinkingEffort;
  },
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
) {
  await streamChatResponse(
    `/api/conversations/${conversationId}/messages`,
    payload,
    onEvent,
    signal,
  );
}

export async function streamRegenerateMessage(
  conversationId: string,
  messageId: number,
  payload: {
    model?: string;
    system_instruction?: string;
    rag_enabled?: boolean;
    thinking_enabled?: boolean;
    thinking_effort?: ThinkingEffort;
  },
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
) {
  await streamChatResponse(
    `/api/conversations/${conversationId}/messages/${messageId}/regenerate`,
    payload,
    onEvent,
    signal,
  );
}

export async function streamEditMessage(
  conversationId: string,
  messageId: number,
  payload: {
    content: string;
    model?: string;
    system_instruction?: string;
    rag_enabled?: boolean;
    thinking_enabled?: boolean;
    thinking_effort?: ThinkingEffort;
  },
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
) {
  await streamChatResponse(
    `/api/conversations/${conversationId}/messages/${messageId}/edit`,
    payload,
    onEvent,
    signal,
  );
}
