"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  LogOut,
  Menu,
  MessageSquare,
  Mic,
  Moon,
  MoreHorizontal,
  PanelLeftClose,
  Paperclip,
  Search,
  Send,
  Settings,
  Sparkles,
  SquarePen,
  Sun,
  X,
} from "lucide-react";

import {
  createConversation,
  getConversation,
  HttpError,
  listConversations,
  refreshAuthToken,
  requestOtp,
  sendMessage,
  verifyOtp,
} from "@/lib/api";
import type { Conversation, ConversationDetail } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const PHONE_STORAGE_KEY = "chat_phone_number";
const ACCESS_TOKEN_STORAGE_KEY = "chat_access_token";
const REFRESH_TOKEN_STORAGE_KEY = "chat_refresh_token";
const THEME_STORAGE_KEY = "chat_theme";
const DEFAULT_SYSTEM_INSTRUCTION =
  "You are a helpful AI assistant. Use concise and actionable answers unless the user asks for detail.";

type Theme = "dark" | "light";
type AuthMode = "login" | "register";

interface AuthSession {
  access: string;
  refresh: string;
}

const MODEL_OPTIONS = [
  {
    id: "gpt-oss-20b",
    label: "GPTClone 20B",
    description: "Default local vLLM model",
    enabled: true,
  },
  {
    id: "fast-placeholder",
    label: "Fast model",
    description: "Subscription placeholder",
    enabled: false,
  },
  {
    id: "reasoning-placeholder",
    label: "Reasoning model",
    description: "Subscription placeholder",
    enabled: false,
  },
];

function isToday(value: string): boolean {
  const date = new Date(value);
  const now = new Date();
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  );
}

function daysAgo(value: string): number {
  const date = new Date(value);
  const now = new Date();
  const startOfDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.floor((startOfToday - startOfDate) / 86_400_000);
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function getInitials(phoneNumber: string): string {
  const digits = phoneNumber.replace(/\D/g, "");
  if (digits.length >= 2) {
    return digits.slice(-2);
  }
  return "U";
}

function groupConversations(conversations: Conversation[], query: string) {
  const cleanQuery = query.trim().toLowerCase();
  const filtered = conversations.filter((conversation) => {
    const haystack = `${conversation.title} ${conversation.last_message_preview ?? ""}`.toLowerCase();
    return !cleanQuery || haystack.includes(cleanQuery);
  });

  return {
    today: filtered.filter((conversation) => isToday(conversation.updated_at)),
    lastWeek: filtered.filter((conversation) => {
      const age = daysAgo(conversation.updated_at);
      return age > 0 && age <= 30;
    }),
    olderThan30: filtered.filter((conversation) => daysAgo(conversation.updated_at) > 30),
  };
}

export function ChatApp() {
  const [phoneNumber, setPhoneNumber] = useState(() => {
    if (typeof window === "undefined") {
      return "";
    }
    return window.localStorage.getItem(PHONE_STORAGE_KEY) ?? "";
  });
  const [phoneInput, setPhoneInput] = useState(phoneNumber);
  const [authSession, setAuthSession] = useState<AuthSession | null>(() => {
    if (typeof window === "undefined") {
      return null;
    }
    const access = window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
    const refresh = window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
    return access && refresh ? { access, refresh } : null;
  });
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === "undefined") {
      return "dark";
    }
    return (window.localStorage.getItem(THEME_STORAGE_KEY) as Theme | null) ?? "dark";
  });
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [otpInput, setOtpInput] = useState("");
  const [devOtp, setDevOtp] = useState("");
  const [isOtpRequested, setIsOtpRequested] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [systemInstruction, setSystemInstruction] = useState(DEFAULT_SYSTEM_INSTRUCTION);
  const [selectedModel, setSelectedModel] = useState(MODEL_OPTIONS[0].id);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);

  const isDark = theme === "dark";
  const activeConversationId = activeConversation?.id;
  const accessToken = authSession?.access ?? "";
  const activeModel = MODEL_OPTIONS.find((model) => model.id === selectedModel) ?? MODEL_OPTIONS[0];
  const groupedConversations = useMemo(
    () => groupConversations(conversations, searchQuery),
    [conversations, searchQuery],
  );

  const persistAuthSession = useCallback((nextSession: AuthSession) => {
    setAuthSession(nextSession);
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, nextSession.access);
    window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, nextSession.refresh);
  }, []);

  const clearAuthSession = useCallback(() => {
    setAuthSession(null);
    setConversations([]);
    setActiveConversation(null);
    setDraft("");
    window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
  }, []);

  const performAuthenticated = useCallback(
    async <T,>(operation: (access: string) => Promise<T>): Promise<T> => {
      if (!authSession) {
        throw new Error("Sign in first.");
      }

      try {
        return await operation(authSession.access);
      } catch (err) {
        if (!(err instanceof HttpError) || err.status !== 401) {
          throw err;
        }

        try {
          const refreshed = await refreshAuthToken(authSession.refresh);
          const nextSession = {
            access: refreshed.access,
            refresh: refreshed.refresh ?? authSession.refresh,
          };
          persistAuthSession(nextSession);
          return await operation(nextSession.access);
        } catch (refreshErr) {
          clearAuthSession();
          throw refreshErr;
        }
      }
    },
    [authSession, clearAuthSession, persistAuthSession],
  );

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activeConversation?.messages]);

  useEffect(() => {
    if (!authSession) {
      return;
    }

    let isCancelled = false;
    const timeoutId = window.setTimeout(async () => {
      setIsLoadingConversations(true);
      setError(null);
      try {
        const items = await performAuthenticated((access) => listConversations(access));
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
        const detail = await performAuthenticated((access) => getConversation(targetId, access));
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
  }, [authSession, activeConversationId, performAuthenticated]);

  function toggleTheme() {
    const nextTheme = isDark ? "light" : "dark";
    setTheme(nextTheme);
    window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  }

  async function handleCreateConversation() {
    if (!authSession) {
      setError("Sign in first.");
      return;
    }

    setError(null);
    try {
      const created = await performAuthenticated((access) => createConversation(access));
      setActiveConversation(created);
      setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setIsSidebarOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create conversation.");
    }
  }

  async function handleSelectConversation(conversationId: string) {
    if (!authSession) {
      setError("Sign in first.");
      return;
    }

    setError(null);
    try {
      const detail = await performAuthenticated((access) => getConversation(conversationId, access));
      setActiveConversation(detail);
      setIsSidebarOpen(false);
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

    if (!authSession) {
      setError("Sign in first.");
      return;
    }

    setIsSending(true);
    setError(null);

    try {
      let conversation = activeConversation;
      if (!conversation) {
        conversation = await performAuthenticated((access) => createConversation(access));
      }

      const updated = await performAuthenticated((access) =>
        sendMessage(conversation.id, access, {
          content: message,
          model: selectedModel,
          system_instruction: systemInstruction.trim() || undefined,
        }),
      );

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

  async function handleRequestOtp(event: React.FormEvent) {
    event.preventDefault();
    const clean = phoneInput.trim();
    if (!clean || isAuthenticating) {
      return;
    }

    setIsAuthenticating(true);
    setError(null);

    try {
      const result = await requestOtp(clean);
      setPhoneNumber(clean);
      setPhoneInput(clean);
      setIsOtpRequested(true);
      setDevOtp(result.otp_code ?? "");
      setOtpInput(result.otp_code ?? "");
      window.localStorage.setItem(PHONE_STORAGE_KEY, clean);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send OTP.");
    } finally {
      setIsAuthenticating(false);
    }
  }

  async function handleVerifyOtp(event: React.FormEvent) {
    event.preventDefault();
    const clean = phoneInput.trim();
    const code = otpInput.trim();
    if (!clean || !code || isAuthenticating) {
      return;
    }

    setIsAuthenticating(true);
    setError(null);

    try {
      const session = await verifyOtp({ phone_number: clean, otp: code });
      persistAuthSession({
        access: session.access,
        refresh: session.refresh,
      });
      setPhoneNumber(session.user.phone_number);
      setPhoneInput(session.user.phone_number);
      setOtpInput("");
      setDevOtp("");
      setIsOtpRequested(false);
      window.localStorage.setItem(PHONE_STORAGE_KEY, session.user.phone_number);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to verify OTP.");
    } finally {
      setIsAuthenticating(false);
    }
  }

  function handleSignOut() {
    clearAuthSession();
    setIsProfileMenuOpen(false);
  }

  function renderConversationGroup(title: string, items: Conversation[]) {
    if (!items.length) {
      return null;
    }

    return (
      <section className="space-y-1">
        <h3
          className={cn(
            "px-3 pt-3 text-xs font-medium",
            isDark ? "text-[#9b9b9b]" : "text-[#6f6f6f]",
          )}
        >
          {title}
        </h3>
        {items.map((conversation) => (
          <button
            key={conversation.id}
            onClick={() => void handleSelectConversation(conversation.id)}
            className={cn(
              "group flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-sm transition",
              activeConversationId === conversation.id
                ? isDark
                  ? "bg-[#303030] text-[#ececec]"
                  : "bg-[#ececec] text-[#171717]"
                : isDark
                  ? "text-[#ececec] hover:bg-[#2a2a2a]"
                  : "text-[#171717] hover:bg-[#ececec]",
            )}
          >
            <MessageSquare className="h-4 w-4 shrink-0 opacity-70" />
            <span className="min-w-0 flex-1 truncate">{conversation.title}</span>
            <MoreHorizontal className="h-4 w-4 shrink-0 opacity-0 transition group-hover:opacity-70" />
          </button>
        ))}
      </section>
    );
  }

  return (
    <div
      className={cn(
        "h-screen overflow-hidden font-sans",
        isDark ? "bg-[#212121] text-[#ececec]" : "bg-white text-[#171717]",
      )}
    >
      <div className="flex h-full">
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-[304px] flex-col border-r transition-transform duration-200 md:relative md:translate-x-0",
            isSidebarOpen ? "translate-x-0" : "-translate-x-full",
            isDark ? "border-[#2f2f2f] bg-[#171717]" : "border-[#e5e5e5] bg-[#f9f9f9]",
          )}
        >
          <div className="flex h-14 items-center justify-between px-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full">
              <Bot className="h-6 w-6" />
            </div>
            <button
              type="button"
              className={cn(
                "grid h-9 w-9 place-items-center rounded-lg transition",
                isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
              )}
              onClick={() => setIsSidebarOpen(false)}
              aria-label="Close sidebar"
            >
              <PanelLeftClose className="h-5 w-5" />
            </button>
          </div>

          <div className="space-y-2 px-3 pb-3">
            <button
              type="button"
              onClick={() => void handleCreateConversation()}
              disabled={!accessToken}
              className={cn(
                "flex h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-50",
                isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
              )}
            >
              <SquarePen className="h-5 w-5" />
              <span>New chat</span>
            </button>

            <div
              className={cn(
                "flex h-10 items-center gap-3 rounded-lg px-3",
                isDark ? "bg-[#212121]" : "bg-white",
              )}
            >
              <Search className="h-5 w-5 opacity-70" />
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search chats"
                className={cn(
                  "min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-current placeholder:opacity-60",
                  isDark ? "text-[#ececec]" : "text-[#171717]",
                )}
              />
              {searchQuery ? (
                <button type="button" onClick={() => setSearchQuery("")} aria-label="Clear search">
                  <X className="h-4 w-4 opacity-70" />
                </button>
              ) : null}
            </div>
          </div>

          <ScrollArea className="min-h-0 flex-1 px-2 pb-3">
            {!accessToken ? (
              <div
                className={cn(
                  "mx-1 rounded-lg border border-dashed px-3 py-4 text-sm",
                  isDark ? "border-[#3a3a3a] text-[#9b9b9b]" : "border-[#dedede] text-[#6f6f6f]",
                )}
              >
                Sign in to load your chats.
              </div>
            ) : null}

            {accessToken && isLoadingConversations ? (
              <div className={cn("px-3 py-2 text-sm", isDark ? "text-[#9b9b9b]" : "text-[#6f6f6f]")}>
                Loading chats...
              </div>
            ) : null}

            {renderConversationGroup("Today", groupedConversations.today)}
            {renderConversationGroup("Last week", groupedConversations.lastWeek)}
            {renderConversationGroup("More than 30 days", groupedConversations.olderThan30)}

            {accessToken &&
            !isLoadingConversations &&
            conversations.length > 0 &&
            !groupedConversations.today.length &&
            !groupedConversations.lastWeek.length &&
            !groupedConversations.olderThan30.length ? (
              <div className={cn("px-3 py-2 text-sm", isDark ? "text-[#9b9b9b]" : "text-[#6f6f6f]")}>
                No chats found.
              </div>
            ) : null}
          </ScrollArea>

          <div className={cn("border-t p-3", isDark ? "border-[#242424]" : "border-[#e9e9e9]")}>
            <button
              type="button"
              onClick={() => setIsProfileMenuOpen((value) => !value)}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg p-2 text-left transition",
                isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
              )}
            >
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#0d8bd9] text-xs font-semibold text-white">
                {getInitials(phoneNumber)}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{phoneNumber || "Guest profile"}</p>
                <p className={cn("truncate text-xs", isDark ? "text-[#9b9b9b]" : "text-[#6f6f6f]")}>
                  Free plan placeholder
                </p>
              </div>
              <MoreHorizontal className="h-5 w-5 opacity-70" />
            </button>
          </div>
        </aside>

        {isSidebarOpen ? (
          <button
            type="button"
            aria-label="Close sidebar overlay"
            className="fixed inset-0 z-30 bg-black/40 md:hidden"
            onClick={() => setIsSidebarOpen(false)}
          />
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col">
          <header
            className={cn(
              "relative flex h-14 shrink-0 items-center justify-between border-b px-3 md:px-4",
              isDark ? "border-[#2f2f2f] bg-[#212121]" : "border-[#eeeeee] bg-white",
            )}
          >
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setIsSidebarOpen(true)}
                className={cn(
                  "grid h-9 w-9 place-items-center rounded-lg transition",
                  isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
                )}
                aria-label="Open sidebar"
              >
                <Menu className="h-5 w-5" />
              </button>

              <div className="relative">
                <button
                  type="button"
                  onClick={() => setIsModelMenuOpen((value) => !value)}
                  className={cn(
                    "flex h-10 max-w-[260px] items-center gap-2 rounded-lg px-3 text-lg font-medium transition",
                    isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#f2f2f2]",
                  )}
                >
                  <span className="truncate">{activeModel.label}</span>
                  <ChevronDown className="h-4 w-4 shrink-0 opacity-70" />
                </button>

                {isModelMenuOpen ? (
                  <div
                    className={cn(
                      "absolute left-0 top-12 z-50 w-[300px] rounded-xl border p-2 shadow-2xl",
                      isDark
                        ? "border-[#3a3a3a] bg-[#2f2f2f] text-[#ececec]"
                        : "border-[#dedede] bg-white text-[#171717]",
                    )}
                  >
                    {MODEL_OPTIONS.map((model) => (
                      <button
                        key={model.id}
                        type="button"
                        disabled={!model.enabled}
                        onClick={() => {
                          if (!model.enabled) {
                            return;
                          }
                          setSelectedModel(model.id);
                          setIsModelMenuOpen(false);
                        }}
                        className={cn(
                          "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-50",
                          isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                        )}
                      >
                        <Sparkles className="h-4 w-4 shrink-0" />
                        <span className="min-w-0 flex-1">
                          <span className="block font-medium">{model.label}</span>
                          <span className={cn("block text-xs", isDark ? "text-[#b4b4b4]" : "text-[#6f6f6f]")}>
                            {model.description}
                          </span>
                        </span>
                        {model.id === selectedModel ? <Check className="h-4 w-4" /> : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                className={cn(
                  "hidden h-9 items-center rounded-lg px-3 text-sm font-medium transition sm:flex",
                  isDark ? "bg-[#303030] hover:bg-[#3a3a3a]" : "bg-[#f2f2f2] hover:bg-[#e7e7e7]",
                )}
              >
                Share
              </button>
              <button
                type="button"
                onClick={() => setIsSettingsOpen((value) => !value)}
                className={cn(
                  "grid h-9 w-9 place-items-center rounded-lg transition",
                  isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
                )}
                aria-label="Settings"
              >
                <Settings className="h-5 w-5" />
              </button>
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setIsProfileMenuOpen((value) => !value)}
                  className="grid h-9 w-9 place-items-center rounded-full bg-[#0d8bd9] text-xs font-semibold text-white"
                  aria-label="Profile"
                >
                  {getInitials(phoneNumber)}
                </button>

                {isProfileMenuOpen ? (
                  <div
                    className={cn(
                      "absolute right-0 top-12 z-50 w-[280px] rounded-xl border p-2 shadow-2xl",
                      isDark
                        ? "border-[#3a3a3a] bg-[#2f2f2f] text-[#ececec]"
                        : "border-[#dedede] bg-white text-[#171717]",
                    )}
                  >
                    <div className="flex items-center gap-3 px-3 py-3">
                      <div className="grid h-10 w-10 place-items-center rounded-full bg-[#0d8bd9] text-xs font-semibold text-white">
                        {getInitials(phoneNumber)}
                      </div>
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{phoneNumber || "Guest profile"}</p>
                        <p className={cn("truncate text-xs", isDark ? "text-[#b4b4b4]" : "text-[#6f6f6f]")}>
                          Free plan placeholder
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={toggleTheme}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
                        isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                      )}
                    >
                      {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                      <span>{isDark ? "Light mode" : "Dark mode"}</span>
                    </button>
                    {accessToken ? (
                      <button
                        type="button"
                        onClick={handleSignOut}
                        className={cn(
                          "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
                          isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                        )}
                      >
                        <LogOut className="h-4 w-4" />
                        <span>Log out</span>
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>

            {isSettingsOpen ? (
              <div
                className={cn(
                  "absolute right-16 top-12 z-50 w-[280px] rounded-xl border p-2 shadow-2xl",
                  isDark
                    ? "border-[#3a3a3a] bg-[#2f2f2f] text-[#ececec]"
                    : "border-[#dedede] bg-white text-[#171717]",
                )}
              >
                <label className="block px-3 py-2 text-xs font-medium uppercase opacity-60">
                  System instruction
                </label>
                <Textarea
                  value={systemInstruction}
                  onChange={(event) => setSystemInstruction(event.target.value)}
                  className={cn(
                    "min-h-28 rounded-lg border px-3 py-2 text-sm",
                    isDark
                      ? "border-[#4a4a4a] bg-[#212121] text-[#ececec]"
                      : "border-[#dedede] bg-white text-[#171717]",
                  )}
                />
              </div>
            ) : null}
          </header>

          <ScrollArea className="min-h-0 flex-1">
            <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-8 md:px-6">
              {!accessToken ? (
                <div className="flex flex-1 items-center justify-center">
                  <form
                    onSubmit={isOtpRequested ? handleVerifyOtp : handleRequestOtp}
                    className={cn(
                      "w-full max-w-sm rounded-2xl border p-5 shadow-sm",
                      isDark ? "border-[#343434] bg-[#262626]" : "border-[#e7e7e7] bg-white",
                    )}
                  >
                    <div className="mb-4 flex rounded-lg p-1 text-sm">
                      {(["login", "register"] as AuthMode[]).map((mode) => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => {
                            setAuthMode(mode);
                            setIsOtpRequested(false);
                            setOtpInput("");
                            setDevOtp("");
                          }}
                          className={cn(
                            "flex-1 rounded-md px-3 py-2 capitalize transition",
                            authMode === mode
                              ? isDark
                                ? "bg-[#3a3a3a]"
                                : "bg-[#f0f0f0]"
                              : isDark
                                ? "text-[#b4b4b4]"
                                : "text-[#6f6f6f]",
                          )}
                        >
                          {mode}
                        </button>
                      ))}
                    </div>

                    <div className="mb-5 space-y-1">
                      <h1 className="text-2xl font-semibold">
                        {authMode === "login" ? "Welcome back" : "Create account"}
                      </h1>
                      <p className={cn("text-sm", isDark ? "text-[#b4b4b4]" : "text-[#6f6f6f]")}>
                        Continue with phone number.
                      </p>
                    </div>

                    <div className="space-y-3">
                      <Input
                        value={phoneInput}
                        onChange={(event) => setPhoneInput(event.target.value)}
                        placeholder="Phone number"
                        className={cn(
                          "h-11 rounded-lg",
                          isDark
                            ? "border-[#4a4a4a] bg-[#212121] text-[#ececec] placeholder:text-[#8b8b8b]"
                            : "border-[#dedede] bg-white text-[#171717]",
                        )}
                      />

                      {isOtpRequested ? (
                        <Input
                          value={otpInput}
                          onChange={(event) => setOtpInput(event.target.value)}
                          inputMode="numeric"
                          placeholder="6-digit OTP"
                          className={cn(
                            "h-11 rounded-lg",
                            isDark
                              ? "border-[#4a4a4a] bg-[#212121] text-[#ececec] placeholder:text-[#8b8b8b]"
                              : "border-[#dedede] bg-white text-[#171717]",
                          )}
                        />
                      ) : null}

                      {devOtp ? (
                        <div
                          className={cn(
                            "rounded-lg px-3 py-2 text-sm",
                            isDark ? "bg-[#1f1f1f] text-[#b4b4b4]" : "bg-[#f6f6f6] text-[#6f6f6f]",
                          )}
                        >
                          Dev OTP: <span className="font-mono">{devOtp}</span>
                        </div>
                      ) : null}

                      <Button
                        type="submit"
                        disabled={isAuthenticating}
                        className="h-11 w-full rounded-full bg-[#10a37f] text-white hover:bg-[#0d8f6f]"
                      >
                        {isAuthenticating ? "Working..." : isOtpRequested ? "Verify OTP" : "Continue"}
                      </Button>
                    </div>
                  </form>
                </div>
              ) : (
                <div className="flex flex-1 flex-col">
                  {!activeConversation?.messages.length ? (
                    <div className="flex flex-1 flex-col items-center justify-center pb-28 text-center">
                      <div
                        className={cn(
                          "mb-5 grid h-12 w-12 place-items-center rounded-full",
                          isDark ? "bg-[#303030]" : "bg-[#f1f1f1]",
                        )}
                      >
                        <Sparkles className="h-6 w-6" />
                      </div>
                      <h1 className="text-3xl font-semibold md:text-4xl">What can I help with?</h1>
                    </div>
                  ) : (
                    <div className="space-y-8 pb-36">
                      {activeConversation.messages.map((message) => (
                        <div
                          key={message.id}
                          className={cn(
                            "flex gap-4",
                            message.role === "user" ? "justify-end" : "justify-start",
                          )}
                        >
                          {message.role !== "user" ? (
                            <div
                              className={cn(
                                "mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full",
                                isDark ? "bg-[#303030]" : "bg-[#f1f1f1]",
                              )}
                            >
                              <Bot className="h-4 w-4" />
                            </div>
                          ) : null}

                          <div
                            className={cn(
                              "max-w-[82%] whitespace-pre-wrap text-[15px] leading-7",
                              message.role === "user"
                                ? isDark
                                  ? "rounded-3xl bg-[#303030] px-5 py-3"
                                  : "rounded-3xl bg-[#f4f4f4] px-5 py-3"
                                : "",
                            )}
                          >
                            {message.content}
                            <div className={cn("mt-1 text-xs", isDark ? "text-[#8f8f8f]" : "text-[#8a8a8a]")}>
                              {formatTime(message.created_at)}
                            </div>
                          </div>
                        </div>
                      ))}
                      <div ref={scrollAnchorRef} />
                    </div>
                  )}
                </div>
              )}
            </div>
          </ScrollArea>

          {accessToken ? (
            <div className="shrink-0 px-3 pb-3 md:px-4">
              <form
                onSubmit={handleSendMessage}
                className={cn(
                  "mx-auto flex w-full max-w-3xl items-end gap-2 rounded-[28px] border p-2 shadow-lg",
                  isDark ? "border-[#3a3a3a] bg-[#303030]" : "border-[#dedede] bg-white",
                )}
              >
                <button
                  type="button"
                  className={cn(
                    "grid h-10 w-10 shrink-0 place-items-center rounded-full transition",
                    isDark ? "hover:bg-[#424242]" : "hover:bg-[#f2f2f2]",
                  )}
                  aria-label="Attach file"
                >
                  <Paperclip className="h-5 w-5" />
                </button>
                <Textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="Ask anything"
                  className={cn(
                    "max-h-44 min-h-10 resize-none border-0 bg-transparent px-0 py-2 text-[15px] shadow-none outline-none focus-visible:ring-0",
                    isDark ? "text-[#ececec] placeholder:text-[#b4b4b4]" : "text-[#171717] placeholder:text-[#8a8a8a]",
                  )}
                  rows={1}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                />
                <button
                  type="button"
                  className={cn(
                    "grid h-10 w-10 shrink-0 place-items-center rounded-full transition",
                    isDark ? "hover:bg-[#424242]" : "hover:bg-[#f2f2f2]",
                  )}
                  aria-label="Voice input"
                >
                  <Mic className="h-5 w-5" />
                </button>
                <button
                  type="submit"
                  disabled={!draft.trim() || isSending}
                  className={cn(
                    "grid h-10 w-10 shrink-0 place-items-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-40",
                    draft.trim()
                      ? "bg-[#ececec] text-[#171717] hover:bg-white"
                      : isDark
                        ? "bg-[#424242] text-[#8f8f8f]"
                        : "bg-[#ececec] text-[#8a8a8a]",
                  )}
                  aria-label="Send message"
                >
                  {isSending ? <X className="h-5 w-5" /> : <Send className="h-5 w-5" />}
                </button>
              </form>
              <div className={cn("mx-auto mt-2 max-w-3xl text-center text-xs", isDark ? "text-[#b4b4b4]" : "text-[#6f6f6f]")}>
                GPTClone can make mistakes. Check important info.
              </div>
            </div>
          ) : null}

          {error ? (
            <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-full bg-[#ef4444] px-4 py-2 text-sm text-white shadow-lg">
              {error}
            </div>
          ) : null}
        </main>
      </div>
    </div>
  );
}
