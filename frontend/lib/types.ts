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
  created_at: string;
  updated_at: string;
  last_message_preview?: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
  active_model?: string;
}

export interface ApiError {
  detail?: string;
  error?: string;
}
