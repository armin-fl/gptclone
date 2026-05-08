import { cookies } from "next/headers";

import { ChatApp } from "@/components/chat/chat-app";
import { ACCESS_COOKIE } from "@/lib/auth-cookies";
import type { AuthUser, ConversationDetail, ConversationPage, InitialChatData } from "@/lib/types";

export const dynamic = "force-dynamic";

const DJANGO_API_BASE_URL = process.env.DJANGO_API_BASE_URL ?? "http://127.0.0.1:8000";

async function djangoGet<T>(path: string, accessToken: string): Promise<T | null> {
  const response = await fetch(`${DJANGO_API_BASE_URL}${path}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
    next: {
      revalidate: 60,
      tags: ["chat:conversations"],
    },
  });

  if (!response.ok) {
    return null;
  }
  return (await response.json()) as T;
}

async function loadInitialChatData(conversationId?: string): Promise<InitialChatData> {
  const accessToken = (await cookies()).get(ACCESS_COOKIE)?.value;
  const emptyConversations: ConversationPage = { results: [], next_cursor: null };
  if (!accessToken) {
    return {
      user: null,
      conversations: emptyConversations,
      activeConversation: null,
      csrf_token: "",
    };
  }

  const [user, conversations] = await Promise.all([
    djangoGet<AuthUser>("/api/auth/me/", accessToken),
    djangoGet<ConversationPage>("/api/conversations/?limit=30", accessToken),
  ]);
  const safeConversations = conversations ?? emptyConversations;
  const targetId =
    conversationId && safeConversations.results.some((item) => item.id === conversationId)
      ? conversationId
      : null;
  const activeConversation = targetId
    ? await djangoGet<ConversationDetail>(`/api/conversations/${targetId}/?limit=50`, accessToken)
    : null;

  return {
    user,
    conversations: safeConversations,
    activeConversation,
    csrf_token: "",
  };
}

export default async function Home({
  searchParams,
}: {
  searchParams?: Promise<{ conversation?: string }>;
}) {
  const params = await searchParams;
  const initialData = await loadInitialChatData(params?.conversation);
  return <ChatApp initialData={initialData} />;
}
