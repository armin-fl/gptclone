"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { createConversation, getConversation, listConversations, sendMessage } from "@/lib/api";
import type { Conversation, ConversationDetail } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const PHONE_STORAGE_KEY = "chat_phone_number";
const DEFAULT_SYSTEM_INSTRUCTION =
  "You are a helpful AI assistant. Use concise and actionable answers unless the user asks for detail.";

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function ChatApp() {
  const [phoneNumber, setPhoneNumber] = useState(() => {
    if (typeof window === "undefined") {
      return "";
    }
    return window.localStorage.getItem(PHONE_STORAGE_KEY) ?? "";
  });
  const [phoneInput, setPhoneInput] = useState(phoneNumber);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [systemInstruction, setSystemInstruction] = useState(DEFAULT_SYSTEM_INSTRUCTION);
  const [modelName, setModelName] = useState("");
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);

  const activeConversationId = activeConversation?.id;

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activeConversation?.messages]);

  useEffect(() => {
    let isCancelled = false;
    const timeoutId = window.setTimeout(async () => {
      setIsLoadingConversations(true);
      setError(null);
      try {
        const items = await listConversations(phoneNumber || undefined);
        if (isCancelled) {
          return;
        }

        setConversations(items);

        if (!items.length) {
          setActiveConversation(null);
          return;
        }

        const targetId =
          items.find((item) => item.id === activeConversationId)?.id ?? items[0].id;
        const detail = await getConversation(targetId, phoneNumber || undefined);
        if (!isCancelled) {
          setActiveConversation(detail);
        }
      } catch (err) {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : "Failed to load conversations.");
        }
      } finally {
        if (!isCancelled) {
          setIsLoadingConversations(false);
        }
      }
    }, 0);

    return () => {
      isCancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [activeConversationId, phoneNumber]);

  const activeTitle = useMemo(() => {
    if (activeConversation?.title) {
      return activeConversation.title;
    }
    return "New Chat";
  }, [activeConversation?.title]);

  async function handleCreateConversation() {
    setError(null);
    try {
      const created = await createConversation({
        phone_number: phoneNumber || undefined,
      });
      setActiveConversation(created);
      setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create conversation.");
    }
  }

  async function handleSelectConversation(conversationId: string) {
    setError(null);
    try {
      const detail = await getConversation(conversationId, phoneNumber || undefined);
      setActiveConversation(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open conversation.");
    }
  }

  async function handleSendMessage(event: React.FormEvent) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || isSending) {
      return;
    }

    setIsSending(true);
    setError(null);

    try {
      let conversation = activeConversation;
      if (!conversation) {
        conversation = await createConversation({
          phone_number: phoneNumber || undefined,
        });
      }

      const updated = await sendMessage(conversation.id, {
        content: message,
        phone_number: phoneNumber || undefined,
        model: modelName.trim() || undefined,
        system_instruction: systemInstruction.trim() || undefined,
      });

      setDraft("");
      setActiveConversation(updated);
      setConversations((prev) => {
        const rest = prev.filter((item) => item.id !== updated.id);
        return [updated, ...rest];
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message.");
    } finally {
      setIsSending(false);
    }
  }

  function handlePhoneApply(event: React.FormEvent) {
    event.preventDefault();
    const clean = phoneInput.trim();
    setPhoneNumber(clean);
    setPhoneInput(clean);
    if (clean) {
      window.localStorage.setItem(PHONE_STORAGE_KEY, clean);
    } else {
      window.localStorage.removeItem(PHONE_STORAGE_KEY);
    }
  }

  return (
    <div className="h-screen bg-[radial-gradient(circle_at_top,_#f8fafc,_#dbeafe_55%,_#cbd5e1)] text-slate-900">
      <div className="mx-auto flex h-full w-full max-w-[1700px] flex-col md:flex-row">
        <aside className="flex w-full shrink-0 flex-col border-b border-slate-200 bg-slate-950 text-slate-100 md:w-[340px] md:border-b-0 md:border-r md:border-slate-800">
          <div className="space-y-3 border-b border-slate-800 p-4">
            <div>
              <p className="text-xs uppercase tracking-[0.2em] text-slate-400">gptclone dev</p>
              <h1 className="text-lg font-semibold">Chats</h1>
            </div>

            <form className="flex gap-2" onSubmit={handlePhoneApply}>
              <Input
                value={phoneInput}
                onChange={(event) => setPhoneInput(event.target.value)}
                placeholder="Phone identity (+989...)"
                className="border-slate-700 bg-slate-900 text-slate-100 placeholder:text-slate-500"
              />
              <Button variant="secondary" className="shrink-0 bg-slate-100" type="submit">
                Apply
              </Button>
            </form>

            <Button className="w-full" onClick={handleCreateConversation}>
              New chat
            </Button>
          </div>

          <ScrollArea className="max-h-60 p-2 md:max-h-none md:flex-1">
            <div className="space-y-1">
              {conversations.map((conversation) => (
                <button
                  key={conversation.id}
                  onClick={() => void handleSelectConversation(conversation.id)}
                  className={cn(
                    "w-full rounded-xl p-3 text-left transition",
                    activeConversationId === conversation.id
                      ? "bg-slate-800"
                      : "hover:bg-slate-900",
                  )}
                >
                  <p className="truncate text-sm font-medium">{conversation.title}</p>
                  <p className="mt-1 truncate text-xs text-slate-400">
                    {conversation.last_message_preview || "No messages yet"}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-500">
                    {formatTimestamp(conversation.updated_at)}
                  </p>
                </button>
              ))}

              {!isLoadingConversations && conversations.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-700 p-4 text-xs text-slate-400">
                  No chats yet. Start one with &quot;New chat&quot;.
                </div>
              ) : null}
            </div>
          </ScrollArea>
        </aside>

        <main className="flex min-h-0 flex-1 flex-col">
          <header className="border-b border-slate-200 bg-white/70 px-4 py-3 backdrop-blur md:px-6">
            <p className="text-xs uppercase tracking-[0.2em] text-slate-500">Current conversation</p>
            <h2 className="truncate text-lg font-semibold text-slate-900">{activeTitle}</h2>
          </header>

          <ScrollArea className="flex-1 px-4 py-6 md:px-8">
            <div className="mx-auto w-full max-w-4xl space-y-4">
              {activeConversation?.messages.map((message) => (
                <div
                  key={message.id}
                  className={cn(
                    "max-w-[85%] rounded-2xl px-4 py-3 text-sm shadow-sm",
                    message.role === "user"
                      ? "ml-auto bg-slate-900 text-white"
                      : "mr-auto bg-white text-slate-900",
                  )}
                >
                  <p className="mb-2 text-[11px] uppercase tracking-[0.12em] opacity-60">
                    {message.role}
                  </p>
                  <p className="whitespace-pre-wrap leading-7">{message.content}</p>
                  <p className="mt-2 text-[11px] opacity-60">{formatTimestamp(message.created_at)}</p>
                </div>
              ))}

              {!activeConversation?.messages.length ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white/70 p-6 text-sm text-slate-600">
                  Start chatting. The backend will store the full history and send conversation context to vLLM.
                </div>
              ) : null}

              <div ref={scrollAnchorRef} />
            </div>
          </ScrollArea>

          <section className="border-t border-slate-200 bg-white p-4 md:p-6">
            <div className="mx-auto w-full max-w-4xl space-y-3">
              <div className="grid gap-3 md:grid-cols-2">
                <Input
                  value={modelName}
                  onChange={(event) => setModelName(event.target.value)}
                  placeholder="vLLM model (gpt-oss-20b)"
                />
                <Input
                  value={systemInstruction}
                  onChange={(event) => setSystemInstruction(event.target.value)}
                  placeholder="System instruction"
                />
              </div>

              <form className="space-y-3" onSubmit={handleSendMessage}>
                <Textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="Message your model..."
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                />
                <div className="flex items-center justify-between">
                  {error ? <p className="text-sm text-red-600">{error}</p> : <span />}
                  <Button type="submit" disabled={isSending}>
                    {isSending ? "Sending..." : "Send"}
                  </Button>
                </div>
              </form>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
