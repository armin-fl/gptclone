export type MessageRole = "system" | "user" | "assistant";
export type ThinkingControl = "none" | "toggle" | "effort";
export type ThinkingEffort = "none" | "short" | "medium" | "long";
export type SubscriptionPlan = "free" | "pro" | "ultra_pro";

export interface ChatMessage {
  id: number;
  role: MessageRole;
  content: string;
  thinking_duration_ms: number | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
  last_message_preview?: string;
  message_count: number;
  last_message_at: string | null;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
  next_before: string | null;
  active_model?: string;
}

export interface CursorPage<T> {
  results: T[];
  next_cursor: string | null;
}

export type ConversationPage = CursorPage<Conversation>;

export interface LlmModelStatus {
  id: string;
  label: string;
  provider: string;
  supports_thinking_toggle: boolean;
  thinking_control?: ThinkingControl;
  thinking_efforts?: ThinkingEffort[];
  managed?: boolean;
  running?: boolean;
  sleeping?: boolean;
  available: boolean;
  reason?: string;
}

export interface LlmModelsResponse {
  models: LlmModelStatus[];
}

export interface ImageModelStatus {
  id: string;
  label: string;
  provider: string;
  available: boolean;
  reason?: string;
}

export interface ImageModelsResponse {
  models: ImageModelStatus[];
}

export interface ImageGenerationResponse {
  created?: number;
  data: Array<{
    b64_json?: string;
    url?: string | null;
    revised_prompt?: string | null;
  }>;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source_name: string;
  content_hash: string;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocumentsResponse {
  documents: KnowledgeDocument[];
}

export interface KnowledgeSearchResult {
  rank: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  source_name: string;
  chunk_index: number;
  score: number;
  rerank_score: number | null;
  content: string;
}

export interface KnowledgeSearchResponse {
  results: KnowledgeSearchResult[];
}

export interface InitialChatData {
  user: AuthUser | null;
  conversations: ConversationPage;
  activeConversation: ConversationDetail | null;
  csrf_token: string;
}

export type ChatStreamEvent =
  | {
      type: "message";
      message: ChatMessage;
      conversation: Conversation;
    }
  | {
      type: "sync";
      conversation: ConversationDetail;
    }
  | {
      type: "delta";
      delta: string;
    }
  | {
      type: "done";
      message: ChatMessage;
      conversation: Conversation;
      active_model?: string;
    }
  | {
      type: "error";
      detail: string;
      error?: string;
    };

export interface AuthUser {
  id: number;
  phone_number: string;
  first_name: string;
  last_name: string;
  profile_image_url: string;
  subscription_plan: SubscriptionPlan;
  subscription_plan_label: string;
}

export interface OTPRequestResponse {
  detail: string;
  otp_code?: string;
}

export interface AuthTokenResponse {
  access?: string;
  refresh?: string;
  token_type: "Bearer";
  access_expires_at: string;
  refresh_expires_at: string;
  user: AuthUser;
}

export interface TokenRefreshResponse {
  access?: string;
  refresh?: string;
}

export interface ApiError {
  detail?: string;
  error?: string;
  [key: string]: unknown;
}
