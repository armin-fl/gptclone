export type MessageRole = "system" | "user" | "assistant";

export interface ChatMessage {
  id: number;
  role: MessageRole;
  content: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  user_phone_number?: string;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
  last_message_preview?: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
  active_model?: string;
}

export interface AuthUser {
  id: number;
  phone_number: string;
  first_name: string;
  last_name: string;
  profile_image_url: string;
}

export interface OTPRequestResponse {
  detail: string;
  otp_code?: string;
}

export interface AuthTokenResponse {
  access: string;
  refresh: string;
  token_type: "Bearer";
  access_expires_at: string;
  refresh_expires_at: string;
  user: AuthUser;
}

export interface TokenRefreshResponse {
  access: string;
  refresh?: string;
}

export interface ApiError {
  detail?: string;
  error?: string;
  [key: string]: unknown;
}
