"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  Bot,
  Brain,
  Camera,
  Check,
  ChevronDown,
  Copy,
  GitFork,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  MoreHorizontal,
  PanelLeftClose,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Palette,
  Phone,
  RotateCcw,
  Search,
  Settings,
  Sparkles,
  SquarePen,
  Sun,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  UserRound,
  X,
} from "lucide-react";

import {
  createConversation,
  deleteConversation,
  forkConversationFromMessage,
  getConversation,
  getMe,
  getSession,
  HttpError,
  listConversations,
  listModels,
  refreshAuthToken,
  requestPhoneChangeOtp,
  requestOtp,
  signOut,
  streamEditMessage,
  streamRegenerateMessage,
  streamMessage,
  updateConversation,
  updateMe,
  verifyPhoneChangeOtp,
  verifyOtp,
} from "@/lib/api";
import type {
  AuthUser,
  ChatMessage,
  Conversation,
  ConversationDetail,
  ConversationPage,
  InitialChatData,
  LlmModelStatus,
} from "@/lib/types";
import { ChatMessageRenderer } from "@/components/chat/chat-message-renderer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Textarea } from "@/components/ui/textarea";
import { stripThinkingBlocks } from "@/lib/thinking";
import { cn } from "@/lib/utils";

const PHONE_STORAGE_KEY = "chat_phone_number";
const LEGACY_ACCESS_TOKEN_STORAGE_KEY = "chat_access_token";
const LEGACY_REFRESH_TOKEN_STORAGE_KEY = "chat_refresh_token";
const THEME_STORAGE_KEY = "chat_theme";
const SYSTEM_INSTRUCTION_STORAGE_KEY = "chat_system_instruction";
const EDIT_WARNING_DISABLED_STORAGE_KEY = "chat_edit_warning_disabled";
const PHONE_PREFIX = "09";
const PHONE_REST_LENGTH = 9;
const PHONE_LENGTH = PHONE_PREFIX.length + PHONE_REST_LENGTH;
const PHONE_NUMBER_PATTERN = /^09[0-9]{9}$/;
const CHAT_BOTTOM_THRESHOLD = 56;
const MODEL_STATUS_REFRESH_MS = 15000;
const DEFAULT_SYSTEM_INSTRUCTION =
  "You are a helpful AI assistant. Use concise and actionable answers unless the user asks for detail.";

type Theme = "dark" | "light";
type AuthMode = "login" | "register";
type AccountTab = "profile" | "personalization";

interface AuthSession {
  authenticated: true;
}

interface ModelOption {
  id: string;
  label: string;
  description: string;
  enabled: boolean;
  supportsThinkingToggle?: boolean;
}

const MODEL_OPTIONS: ModelOption[] = [
  {
    id: "gpt-oss-20b",
    label: "GPTClone 20B",
    description: "Default local vLLM model",
    enabled: true,
  },
  {
    id: "qwen3:14b",
    label: "Qwen3 14B",
    description: "Local Ollama model",
    enabled: true,
    supportsThinkingToggle: true,
  },
  {
    id: "qwen3-32b-awq",
    label: "Qwen3 32B AWQ",
    description: "Local vLLM AWQ model",
    enabled: true,
    supportsThinkingToggle: true,
  },
  {
    id: "qwq-32b-awq",
    label: "QwQ 32B AWQ",
    description: "Local reasoning vLLM AWQ model",
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

const DIGIT_RANGE_STARTS = [
  0x0660,
  0x06f0,
  0x0966,
  0x09e6,
  0x0a66,
  0x0ae6,
  0x0b66,
  0x0be6,
  0x0c66,
  0x0ce6,
  0x0d66,
  0x0e50,
  0x0ed0,
  0x0f20,
  0x1040,
  0x17e0,
  0xff10,
];

function toEnglishDigits(value: string): string {
  return Array.from(value, (char) => {
    const code = char.codePointAt(0);
    if (code === undefined) {
      return char;
    }

    for (const start of DIGIT_RANGE_STARTS) {
      if (code >= start && code <= start + 9) {
        return String(code - start);
      }
    }

    return char;
  }).join("");
}

function getDigits(value: string): string {
  return toEnglishDigits(value).replace(/\D/g, "");
}

function normalizePhoneDraft(value: string): string {
  const digits = getDigits(value);

  if (!digits) {
    return PHONE_PREFIX;
  }

  if (digits.startsWith(PHONE_PREFIX)) {
    return digits.slice(0, PHONE_LENGTH);
  }

  if (digits.startsWith("9") && digits.length >= 10) {
    return `0${digits.slice(0, PHONE_LENGTH - 1)}`;
  }

  return `${PHONE_PREFIX}${digits.slice(0, PHONE_REST_LENGTH)}`;
}

function getStoredPhoneNumber(value: string | null): string {
  if (!value) {
    return "";
  }

  const normalized = normalizePhoneDraft(value);
  return PHONE_NUMBER_PATTERN.test(normalized) ? normalized : "";
}

function getSubmitPhoneNumber(value: string): string | null {
  const normalized = normalizePhoneDraft(value);
  return PHONE_NUMBER_PATTERN.test(normalized) ? normalized : null;
}

function normalizeOtpDraft(value: string): string {
  return getDigits(value).slice(0, 6);
}

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

function getDisplayName(user: AuthUser | null, fallbackPhoneNumber: string): string {
  const fullName = [user?.first_name, user?.last_name].filter(Boolean).join(" ").trim();
  return fullName || user?.phone_number || fallbackPhoneNumber || "Guest profile";
}

function getInitials(phoneNumber: string, user?: AuthUser | null): string {
  const fullName = [user?.first_name, user?.last_name].filter(Boolean).join(" ").trim();
  if (fullName) {
    const parts = fullName.split(/\s+/).slice(0, 2);
    return parts.map((part) => part[0]).join("").toUpperCase();
  }

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
  const unpinned = filtered.filter((conversation) => !conversation.is_pinned);

  return {
    pinned: filtered.filter((conversation) => conversation.is_pinned),
    today: unpinned.filter((conversation) => isToday(conversation.updated_at)),
    lastWeek: unpinned.filter((conversation) => {
      const age = daysAgo(conversation.updated_at);
      return age > 0 && age <= 30;
    }),
    olderThan30: unpinned.filter((conversation) => daysAgo(conversation.updated_at) > 30),
  };
}

function getMessagePreview(content: string): string {
  const previewContent = stripThinkingBlocks(content);
  return previewContent.length > 120 ? `${previewContent.slice(0, 120)}...` : previewContent;
}

function toConversationSummary(conversation: ConversationDetail): Conversation {
  let lastMessage: ChatMessage | undefined;
  for (let index = conversation.messages.length - 1; index >= 0; index -= 1) {
    if (conversation.messages[index].content.trim()) {
      lastMessage = conversation.messages[index];
      break;
    }
  }

  return {
    id: conversation.id,
    title: conversation.title,
    is_pinned: conversation.is_pinned,
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
    message_count: conversation.message_count,
    last_message_at: conversation.last_message_at,
    last_message_preview: lastMessage
      ? getMessagePreview(lastMessage.content)
      : conversation.last_message_preview ?? "",
  };
}

function mergeConversationSummary(
  detail: ConversationDetail,
  summary: Conversation,
): ConversationDetail {
  return {
    ...detail,
    ...summary,
    messages: detail.messages,
    next_before: detail.next_before,
  };
}

function replaceMessageById(messages: ChatMessage[], messageId: number, nextMessage: ChatMessage) {
  let didReplace = false;
  const nextMessages = messages.map((message) => {
    if (message.id !== messageId) {
      return message;
    }
    didReplace = true;
    return nextMessage;
  });
  return didReplace ? nextMessages : [...nextMessages, nextMessage];
}

function isAbortError(error: unknown): boolean {
  return typeof DOMException !== "undefined" && error instanceof DOMException && error.name === "AbortError";
}

function getLinkedConversationId(): string | null {
  const conversationId = new URLSearchParams(window.location.search).get("conversation");
  return conversationId?.trim() || null;
}

function buildConversationUrl(conversationId: string): string {
  const url = new URL(window.location.href);
  url.pathname = "/";
  url.searchParams.set("conversation", conversationId);
  url.hash = "";
  return url.toString();
}

function getConversationHref(conversationId: string): string {
  const search = new URLSearchParams({ conversation: conversationId });
  return `/?${search.toString()}`;
}

function setCurrentConversationUrl(conversationId: string | null) {
  const url = new URL(window.location.href);
  if (conversationId) {
    url.searchParams.set("conversation", conversationId);
  } else {
    url.searchParams.delete("conversation");
  }
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.select();

  try {
    const didCopy = document.execCommand("copy");
    if (!didCopy) {
      throw new Error("Copy command failed.");
    }
  } finally {
    document.body.removeChild(textarea);
  }
}

interface ChatAppProps {
  initialData?: InitialChatData;
}

export function ChatApp({ initialData }: ChatAppProps = {}) {
  const queryClient = useQueryClient();
  const [phoneNumber, setPhoneNumber] = useState(initialData?.user?.phone_number ?? "");
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(initialData?.user ?? null);
  const [phoneInput, setPhoneInput] = useState(initialData?.user?.phone_number ?? PHONE_PREFIX);
  const [authSession, setAuthSession] = useState<AuthSession | null>(
    initialData?.user ? { authenticated: true } : null,
  );
  const [theme, setTheme] = useState<Theme>("dark");
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [otpInput, setOtpInput] = useState("");
  const [devOtp, setDevOtp] = useState("");
  const [isOtpRequested, setIsOtpRequested] = useState(false);
  const [isPhoneInputFocused, setIsPhoneInputFocused] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>(initialData?.conversations.results ?? []);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(
    initialData?.activeConversation ?? null,
  );
  const [isComposingNewChat, setIsComposingNewChat] = useState(
    Boolean(initialData?.user && !initialData.activeConversation && !initialData.conversations.results.length),
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [systemInstruction, setSystemInstruction] = useState(DEFAULT_SYSTEM_INSTRUCTION);
  const [selectedModel, setSelectedModel] = useState(MODEL_OPTIONS[0].id);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [modelStatuses, setModelStatuses] = useState<Record<string, LlmModelStatus>>({});
  const [isRefreshingModelStatuses, setIsRefreshingModelStatuses] = useState(false);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const [openConversationMenuId, setOpenConversationMenuId] = useState<string | null>(null);
  const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [isSidebarProfileMenuOpen, setIsSidebarProfileMenuOpen] = useState(false);
  const [isAccountModalOpen, setIsAccountModalOpen] = useState(false);
  const [accountTab, setAccountTab] = useState<AccountTab>("profile");
  const [profileFirstName, setProfileFirstName] = useState(initialData?.user?.first_name ?? "");
  const [profileLastName, setProfileLastName] = useState(initialData?.user?.last_name ?? "");
  const [profileImageFile, setProfileImageFile] = useState<File | null>(null);
  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [profileMessage, setProfileMessage] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [changePhoneInput, setChangePhoneInput] = useState(initialData?.user?.phone_number ?? PHONE_PREFIX);
  const [changePhoneOtpInput, setChangePhoneOtpInput] = useState("");
  const [changePhoneDevOtp, setChangePhoneDevOtp] = useState("");
  const [isPhoneChangeOtpRequested, setIsPhoneChangeOtpRequested] = useState(false);
  const [isRequestingPhoneOtp, setIsRequestingPhoneOtp] = useState(false);
  const [isVerifyingPhoneOtp, setIsVerifyingPhoneOtp] = useState(false);
  const [phoneChangeMessage, setPhoneChangeMessage] = useState<string | null>(null);
  const [phoneChangeError, setPhoneChangeError] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<number | null>(null);
  const [feedbackByMessageId, setFeedbackByMessageId] = useState<Record<number, "up" | "down">>({});
  const [regeneratingMessageId, setRegeneratingMessageId] = useState<number | null>(null);
  const [forkingMessageId, setForkingMessageId] = useState<number | null>(null);
  const [openMessageMenuId, setOpenMessageMenuId] = useState<number | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [pendingEditMessage, setPendingEditMessage] = useState<ChatMessage | null>(null);
  const [isEditWarningOpen, setIsEditWarningOpen] = useState(false);
  const [suppressEditWarning, setSuppressEditWarning] = useState(false);
  const [shouldRememberEditWarningChoice, setShouldRememberEditWarningChoice] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);
  const chatScrollAreaRef = useRef<HTMLDivElement | null>(null);
  const bottomSentinelRef = useRef<HTMLDivElement | null>(null);
  const draftTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const copiedMessageTimeoutRef = useRef<number | null>(null);
  const bottomStateFrameRef = useRef<number | null>(null);
  const contentNoticeFrameRef = useRef<number | null>(null);
  const hideJumpFrameRef = useRef<number | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const nextPendingMessageIdRef = useRef(-1);
  const isAtBottomRef = useRef(true);
  const previousMessagesKeyRef = useRef("");
  const skipNextContentNoticeRef = useRef(0);
  const hasScrolledForCurrentResponseRef = useRef(false);
  const streamAbortControllerRef = useRef<AbortController | null>(null);
  const isSendingRef = useRef(false);
  const didUseInitialDataRef = useRef(Boolean(initialData?.user));

  const isDark = theme === "dark";
  const activeConversationId = activeConversation?.id;
  const isAuthenticated = Boolean(authSession);
  const modelOptions = useMemo(
    () =>
      MODEL_OPTIONS.map((model) => {
        const status = modelStatuses[model.id];
        if (!model.enabled || !status) {
          return model;
        }
        return {
          ...model,
          description: status.available ? model.description : "Model container is stopped",
          enabled: status.available,
        };
      }),
    [modelStatuses],
  );
  const activeModel = modelOptions.find((model) => model.id === selectedModel) ?? modelOptions[0];
  const activeModelStatus = modelStatuses[selectedModel];
  const activeModelSupportsThinkingToggle =
    activeModelStatus?.supports_thinking_toggle ?? activeModel.supportsThinkingToggle ?? false;
  const requestedThinkingEnabled = activeModelSupportsThinkingToggle && thinkingEnabled;
  const isActiveModelAvailable = activeModel.enabled;
  const canSubmitDraft = Boolean(draft.trim()) && isActiveModelAvailable;
  const profileDisplayName = getDisplayName(currentUser, phoneNumber);
  const profileImageUrl = currentUser?.profile_image_url || "";
  const phoneRestInput = phoneInput.startsWith(PHONE_PREFIX) ? phoneInput.slice(PHONE_PREFIX.length) : "";
  const changePhoneClean = getSubmitPhoneNumber(changePhoneInput);
  const isChangePhoneValid = Boolean(changePhoneClean);
  const isChangePhoneSame =
    Boolean(changePhoneClean) && changePhoneClean === (currentUser?.phone_number || phoneNumber);
  const phoneDigitSlots = Array.from(
    { length: PHONE_REST_LENGTH },
    (_, index) => phoneRestInput[index] ?? "",
  );
  const isPhoneNumberValid = PHONE_NUMBER_PATTERN.test(phoneInput);
  const isOtpValid = otpInput.length === 6;
  const groupedConversations = useMemo(
    () => groupConversations(conversations, searchQuery),
    [conversations, searchQuery],
  );
  const messageContentKey = useMemo(
    () =>
      activeConversation?.messages
        .map((message) => `${message.id}:${message.role}:${message.content.length}:${message.content}`)
        .join("\n") ?? "",
    [activeConversation?.messages],
  );
  const lastPersistedMessage = useMemo(() => {
    const messages = activeConversation?.messages ?? [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.id > 0 && message.content.trim()) {
        return message;
      }
    }
    return null;
  }, [activeConversation?.messages]);
  const latestAssistantMessageId =
    lastPersistedMessage?.role === "assistant" ? lastPersistedMessage.id : null;

  const hideJumpToLatest = useCallback(() => {
    if (hideJumpFrameRef.current !== null) {
      return;
    }

    hideJumpFrameRef.current = window.requestAnimationFrame(() => {
      hideJumpFrameRef.current = null;
      setShowJumpToLatest(false);
    });
  }, []);

  function getNextPendingMessageId() {
    const id = nextPendingMessageIdRef.current;
    nextPendingMessageIdRef.current -= 1;
    return id;
  }

  const updateBottomState = useCallback(() => {
    const scrollArea = chatScrollAreaRef.current;
    if (!scrollArea) {
      return;
    }

    const distanceToBottom =
      scrollArea.scrollHeight - scrollArea.scrollTop - scrollArea.clientHeight;
    const isAtBottom = distanceToBottom <= CHAT_BOTTOM_THRESHOLD;
    isAtBottomRef.current = isAtBottom;
    if (isAtBottom) {
      hideJumpToLatest();
    }
  }, [hideJumpToLatest]);

  const scheduleBottomStateUpdate = useCallback(() => {
    if (bottomStateFrameRef.current !== null) {
      return;
    }

    bottomStateFrameRef.current = window.requestAnimationFrame(() => {
      bottomStateFrameRef.current = null;
      updateBottomState();
    });
  }, [updateBottomState]);

  const scrollToLatest = useCallback(
    (behavior: ScrollBehavior = "smooth") => {
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
      }

      hideJumpToLatest();
      scrollFrameRef.current = window.requestAnimationFrame(() => {
        scrollFrameRef.current = null;
        const bottomSentinel = bottomSentinelRef.current;
        if (bottomSentinel) {
          bottomSentinel.scrollIntoView({ behavior, block: "end" });
        } else {
          const scrollArea = chatScrollAreaRef.current;
          scrollArea?.scrollTo({ behavior, top: scrollArea.scrollHeight });
        }
        window.requestAnimationFrame(updateBottomState);
      });
    },
    [hideJumpToLatest, updateBottomState],
  );

  const skipNextJumpNotice = useCallback(() => {
    skipNextContentNoticeRef.current += 1;
  }, []);

  const scrollToLatestForNewContent = useCallback(
    (behavior: ScrollBehavior = "smooth") => {
      skipNextJumpNotice();
      scrollToLatest(behavior);
    },
    [scrollToLatest, skipNextJumpNotice],
  );

  const handleChatScroll = useCallback(() => {
    scheduleBottomStateUpdate();
  }, [scheduleBottomStateUpdate]);

  const handleJumpToLatest = useCallback(() => {
    scrollToLatest("smooth");
  }, [scrollToLatest]);

  const persistAuthSession = useCallback((nextSession: AuthSession) => {
    setAuthSession(nextSession);
  }, []);

  const clearAuthSession = useCallback(() => {
    void signOut().catch(() => undefined);
    setAuthSession(null);
    setCurrentUser(null);
    setConversations([]);
    setActiveConversation(null);
    setIsComposingNewChat(false);
    setDraft("");
    setModelStatuses({});
    setCurrentConversationUrl(null);
    window.localStorage.removeItem(LEGACY_ACCESS_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(LEGACY_REFRESH_TOKEN_STORAGE_KEY);
  }, []);

  const applyConversationUpdate = useCallback((updated: ConversationDetail) => {
    queryClient.setQueryData(["conversation", updated.id], updated);
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === updated.id
          ? {
              ...conversation,
              title: updated.title,
              is_pinned: updated.is_pinned,
              updated_at: updated.updated_at,
            }
          : conversation,
      ),
    );
    setActiveConversation((prev) =>
      prev?.id === updated.id
        ? {
            ...prev,
            title: updated.title,
            is_pinned: updated.is_pinned,
            updated_at: updated.updated_at,
          }
        : prev,
    );
  }, [queryClient]);

  useEffect(() => {
    let isCancelled = false;
    const timeoutId = window.setTimeout(async () => {
      const storedPhoneNumber = getStoredPhoneNumber(window.localStorage.getItem(PHONE_STORAGE_KEY));
      const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
      const storedSystemInstruction = window.localStorage.getItem(SYSTEM_INSTRUCTION_STORAGE_KEY);
      const storedEditWarningDisabled = window.localStorage.getItem(EDIT_WARNING_DISABLED_STORAGE_KEY);

      if (storedPhoneNumber) {
        setPhoneNumber(storedPhoneNumber);
        setPhoneInput(storedPhoneNumber);
      }

      if (storedTheme === "dark" || storedTheme === "light") {
        setTheme(storedTheme);
      }

      if (storedSystemInstruction !== null) {
        setSystemInstruction(storedSystemInstruction);
      }

      if (storedEditWarningDisabled === "true") {
        setSuppressEditWarning(true);
      }

      try {
        const session = await getSession();
        if (isCancelled || !session.user) {
          return;
        }
        persistAuthSession({ authenticated: true });
        setCurrentUser(session.user);
        setPhoneNumber(session.user.phone_number);
        setPhoneInput(session.user.phone_number);
        setChangePhoneInput(session.user.phone_number);
        setProfileFirstName(session.user.first_name);
        setProfileLastName(session.user.last_name);
        window.localStorage.setItem(PHONE_STORAGE_KEY, session.user.phone_number);
      } catch {
        if (!isCancelled) {
          setAuthSession(null);
        }
      }
    }, 0);

    return () => {
      isCancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [persistAuthSession]);

  useEffect(() => {
    if (!initialData?.user) {
      return;
    }
    queryClient.setQueryData<ConversationPage>(["conversations", ""], initialData.conversations);
    if (initialData.activeConversation) {
      queryClient.setQueryData(
        ["conversation", initialData.activeConversation.id],
        initialData.activeConversation,
      );
    }
  }, [initialData, queryClient]);

  const performAuthenticated = useCallback(
    async <T,>(operation: () => Promise<T>): Promise<T> => {
      if (!authSession) {
        throw new Error("Sign in first.");
      }

      try {
        return await operation();
      } catch (err) {
        if (!(err instanceof HttpError) || err.status !== 401) {
          throw err;
        }

        try {
          await refreshAuthToken();
          persistAuthSession({ authenticated: true });
          return await operation();
        } catch (refreshErr) {
          clearAuthSession();
          throw refreshErr;
        }
      }
    },
    [authSession, clearAuthSession, persistAuthSession],
  );

  const applyModelStatuses = useCallback((models: LlmModelStatus[]) => {
    const statusesById = Object.fromEntries(models.map((model) => [model.id, model]));
    setModelStatuses(statusesById);
  }, []);

  const refreshModelStatuses = useCallback(async () => {
    if (!authSession) {
      setModelStatuses({});
      return;
    }

    setIsRefreshingModelStatuses(true);
    try {
      const response = await performAuthenticated(() => listModels());
      applyModelStatuses(response.models);
    } catch {
      // Keep the last known statuses; chat requests still show their normal backend errors.
    } finally {
      setIsRefreshingModelStatuses(false);
    }
  }, [applyModelStatuses, authSession, performAuthenticated]);

  useEffect(() => {
    if (!authSession) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      void refreshModelStatuses();
    }, 0);
    const intervalId = window.setInterval(() => {
      void refreshModelStatuses();
    }, MODEL_STATUS_REFRESH_MS);

    return () => {
      window.clearTimeout(timeoutId);
      window.clearInterval(intervalId);
    };
  }, [authSession, refreshModelStatuses]);

  useEffect(() => {
    if (!authSession) {
      return;
    }

    let isCancelled = false;
    const timeoutId = window.setTimeout(async () => {
      try {
        const user = await performAuthenticated(() => getMe());
        if (isCancelled) {
          return;
        }

        setCurrentUser(user);
        setPhoneNumber(user.phone_number);
        setPhoneInput(user.phone_number);
        setChangePhoneInput(user.phone_number);
        setProfileFirstName(user.first_name);
        setProfileLastName(user.last_name);
        window.localStorage.setItem(PHONE_STORAGE_KEY, user.phone_number);
      } catch (err) {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : "Failed to load profile.");
        }
      }
    }, 0);

    return () => {
      isCancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [authSession, performAuthenticated]);

  useEffect(() => {
    previousMessagesKeyRef.current = "";
    hideJumpToLatest();
    scheduleBottomStateUpdate();
  }, [activeConversationId, hideJumpToLatest, scheduleBottomStateUpdate]);

  useEffect(() => {
    const root = chatScrollAreaRef.current;
    const sentinel = bottomSentinelRef.current;
    if (!root || !sentinel) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        const isAtBottom = Boolean(entry?.isIntersecting);
        isAtBottomRef.current = isAtBottom;
        if (isAtBottom) {
          hideJumpToLatest();
        }
      },
      {
        root,
        rootMargin: `0px 0px ${CHAT_BOTTOM_THRESHOLD}px 0px`,
        threshold: 0,
      },
    );

    observer.observe(sentinel);
    updateBottomState();

    return () => observer.disconnect();
  }, [activeConversationId, activeConversation?.messages.length, hideJumpToLatest, updateBottomState]);

  useEffect(() => {
    if (!messageContentKey) {
      previousMessagesKeyRef.current = "";
      hideJumpToLatest();
      return;
    }

    const previousKey = previousMessagesKeyRef.current;
    if (!previousKey) {
      previousMessagesKeyRef.current = messageContentKey;
      if (skipNextContentNoticeRef.current > 0) {
        skipNextContentNoticeRef.current -= 1;
      }
      scheduleBottomStateUpdate();
      return;
    }

    if (previousKey === messageContentKey) {
      return;
    }

    previousMessagesKeyRef.current = messageContentKey;
    if (contentNoticeFrameRef.current !== null) {
      return;
    }

    contentNoticeFrameRef.current = window.requestAnimationFrame(() => {
      contentNoticeFrameRef.current = null;
      updateBottomState();

      if (skipNextContentNoticeRef.current > 0) {
        skipNextContentNoticeRef.current -= 1;
        return;
      }

      if (!isAtBottomRef.current) {
        setShowJumpToLatest(true);
      }
    });
  }, [hideJumpToLatest, messageContentKey, scheduleBottomStateUpdate, updateBottomState]);

  useEffect(() => {
    return () => {
      if (bottomStateFrameRef.current !== null) {
        window.cancelAnimationFrame(bottomStateFrameRef.current);
      }
      if (contentNoticeFrameRef.current !== null) {
        window.cancelAnimationFrame(contentNoticeFrameRef.current);
      }
      if (hideJumpFrameRef.current !== null) {
        window.cancelAnimationFrame(hideJumpFrameRef.current);
      }
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
      }
      if (copiedMessageTimeoutRef.current !== null) {
        window.clearTimeout(copiedMessageTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    isSendingRef.current = isSending;
  }, [isSending]);

  useEffect(() => {
    if (!isModelMenuOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      if (!modelMenuRef.current?.contains(event.target as Node)) {
        setIsModelMenuOpen(false);
      }
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [isModelMenuOpen]);

  useEffect(() => {
    if (!openConversationMenuId) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Element) || !target.closest("[data-conversation-menu]")) {
        setOpenConversationMenuId(null);
      }
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [openConversationMenuId]);

  useEffect(() => {
    if (openMessageMenuId === null) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Element) || !target.closest("[data-message-actions]")) {
        setOpenMessageMenuId(null);
      }
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [openMessageMenuId]);

  useEffect(() => {
    if (!isProfileMenuOpen && !isSidebarProfileMenuOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Element) || !target.closest("[data-profile-menu]")) {
        setIsProfileMenuOpen(false);
        setIsSidebarProfileMenuOpen(false);
      }
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [isProfileMenuOpen, isSidebarProfileMenuOpen]);

  useEffect(() => {
    if (!authSession || isSendingRef.current) {
      return;
    }
    if (didUseInitialDataRef.current) {
      didUseInitialDataRef.current = false;
      return;
    }

    let isCancelled = false;
    const timeoutId = window.setTimeout(async () => {
      setIsLoadingConversations(true);
      setError(null);
      try {
        const page = await performAuthenticated(() => listConversations({ q: searchQuery }));
        const items = page.results;
        if (isCancelled) {
          return;
        }

        queryClient.setQueryData<ConversationPage>(["conversations", searchQuery.trim()], page);
        setConversations(items);

        if (!items.length) {
          setActiveConversation(null);
          setIsComposingNewChat(true);
          return;
        }

        if (isComposingNewChat) {
          setActiveConversation(null);
          return;
        }

        const linkedConversationId = getLinkedConversationId();
        const targetId =
          (linkedConversationId && items.find((item) => item.id === linkedConversationId)?.id) ||
          items.find((item) => item.id === activeConversationId)?.id ||
          items[0].id;
        const detail = await performAuthenticated(() => getConversation(targetId));
        if (!isCancelled) {
          queryClient.setQueryData(["conversation", targetId], detail);
          setActiveConversation(detail);
          setCurrentConversationUrl(detail.id);
          scrollToLatest("auto");
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
  }, [
    authSession,
    activeConversationId,
    isComposingNewChat,
    performAuthenticated,
    queryClient,
    searchQuery,
    scrollToLatest,
  ]);

  function toggleTheme() {
    const nextTheme = isDark ? "light" : "dark";
    setTheme(nextTheme);
    window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  }

  function closeProfileMenus() {
    setIsProfileMenuOpen(false);
    setIsSidebarProfileMenuOpen(false);
  }

  function openAccountModal(tab: AccountTab) {
    if (tab === "profile") {
      setProfileFirstName(currentUser?.first_name ?? "");
      setProfileLastName(currentUser?.last_name ?? "");
      setProfileImageFile(null);
      setProfileMessage(null);
      setProfileError(null);
      setPhoneChangeMessage(null);
      setPhoneChangeError(null);
      setChangePhoneInput(currentUser?.phone_number || phoneNumber || PHONE_PREFIX);
      setChangePhoneOtpInput("");
      setChangePhoneDevOtp("");
      setIsPhoneChangeOtpRequested(false);
    }

    setAccountTab(tab);
    setIsAccountModalOpen(true);
    closeProfileMenus();
  }

  function handleSystemInstructionChange(value: string) {
    setSystemInstruction(value);
    window.localStorage.setItem(SYSTEM_INSTRUCTION_STORAGE_KEY, value);
  }

  function renderAvatar(className: string) {
    if (profileImageUrl) {
      return (
        <span
          className={cn("block shrink-0 rounded-full bg-cover bg-center", className)}
          style={{ backgroundImage: `url("${profileImageUrl}")` }}
          aria-hidden="true"
        />
      );
    }

    return (
      <span
        className={cn(
          "grid shrink-0 place-items-center rounded-full bg-[#0d8bd9] text-xs font-semibold text-white",
          className,
        )}
      >
        {getInitials(phoneNumber, currentUser)}
      </span>
    );
  }

  async function handleSaveProfile(event: React.FormEvent) {
    event.preventDefault();
    if (!isAuthenticated || isSavingProfile) {
      return;
    }

    const payload = new FormData();
    payload.append("first_name", profileFirstName.trim());
    payload.append("last_name", profileLastName.trim());
    if (profileImageFile) {
      payload.append("profile_image", profileImageFile);
    }

    setIsSavingProfile(true);
    setProfileError(null);
    setProfileMessage(null);

    try {
      const user = await performAuthenticated(() => updateMe(payload));
      setCurrentUser(user);
      setPhoneNumber(user.phone_number);
      setProfileFirstName(user.first_name);
      setProfileLastName(user.last_name);
      window.localStorage.setItem(PHONE_STORAGE_KEY, user.phone_number);
      setProfileImageFile(null);
      setProfileMessage("Profile saved.");
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : "Failed to save profile.");
    } finally {
      setIsSavingProfile(false);
    }
  }

  async function handleRequestPhoneChangeOtp(event: React.FormEvent) {
    event.preventDefault();
    const clean = getSubmitPhoneNumber(changePhoneInput);
    if (!isAuthenticated || isRequestingPhoneOtp) {
      return;
    }

    if (!clean) {
      setPhoneChangeError("Phone number must be 11 English digits and start with 09.");
      return;
    }

    if (clean === (currentUser?.phone_number || phoneNumber)) {
      setPhoneChangeError("Enter a new phone number.");
      return;
    }

    setIsRequestingPhoneOtp(true);
    setPhoneChangeError(null);
    setPhoneChangeMessage(null);
    setChangePhoneDevOtp("");
    setChangePhoneOtpInput("");

    try {
      const result = await performAuthenticated(() => requestPhoneChangeOtp(clean));
      setChangePhoneInput(clean);
      setChangePhoneDevOtp(result.otp_code ?? "");
      setChangePhoneOtpInput(result.otp_code ? normalizeOtpDraft(result.otp_code) : "");
      setIsPhoneChangeOtpRequested(true);
      setPhoneChangeMessage("OTP sent.");
    } catch (err) {
      setPhoneChangeError(err instanceof Error ? err.message : "Failed to send OTP.");
    } finally {
      setIsRequestingPhoneOtp(false);
    }
  }

  async function handleVerifyPhoneChangeOtp(event: React.FormEvent) {
    event.preventDefault();
    const clean = getSubmitPhoneNumber(changePhoneInput);
    const code = normalizeOtpDraft(changePhoneOtpInput);
    if (!isAuthenticated || isVerifyingPhoneOtp) {
      return;
    }

    if (!clean) {
      setPhoneChangeError("Phone number must be 11 English digits and start with 09.");
      return;
    }

    if (code.length !== 6) {
      setPhoneChangeError("OTP must be a 6-digit code.");
      return;
    }

    setIsVerifyingPhoneOtp(true);
    setPhoneChangeError(null);
    setPhoneChangeMessage(null);

    try {
      const user = await performAuthenticated(() => verifyPhoneChangeOtp({ phone_number: clean, otp: code }));
      setCurrentUser(user);
      setPhoneNumber(user.phone_number);
      setPhoneInput(user.phone_number);
      setChangePhoneInput(user.phone_number);
      setChangePhoneOtpInput("");
      setChangePhoneDevOtp("");
      setIsPhoneChangeOtpRequested(false);
      window.localStorage.setItem(PHONE_STORAGE_KEY, user.phone_number);
      setPhoneChangeMessage("Phone number changed.");
    } catch (err) {
      setPhoneChangeError(err instanceof Error ? err.message : "Failed to verify OTP.");
    } finally {
      setIsVerifyingPhoneOtp(false);
    }
  }

  function handleCreateConversation() {
    if (!authSession) {
      setError("Sign in first.");
      return;
    }

    setError(null);
    setDraft("");
    setActiveConversation(null);
    setIsComposingNewChat(true);
    setOpenConversationMenuId(null);
    setCurrentConversationUrl(null);
    setRenamingConversationId(null);
    setRenameDraft("");
  }

  async function handleSelectConversation(conversationId: string) {
    if (!authSession) {
      setError("Sign in first.");
      return;
    }

    setError(null);
    try {
      const detail = await performAuthenticated(() => getConversation(conversationId));
      queryClient.setQueryData(["conversation", conversationId], detail);
      setActiveConversation(detail);
      setIsComposingNewChat(false);
      setOpenConversationMenuId(null);
      setCurrentConversationUrl(detail.id);
      scrollToLatest("auto");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open conversation.");
    }
  }

  function startRenameConversation(conversation: Conversation) {
    setRenamingConversationId(conversation.id);
    setRenameDraft(conversation.title);
    setOpenConversationMenuId(null);
  }

  function cancelRenameConversation() {
    setRenamingConversationId(null);
    setRenameDraft("");
  }

  async function handleRenameConversation(event: React.FormEvent, conversationId: string) {
    event.preventDefault();
    const title = renameDraft.trim();
    if (!authSession || !title) {
      return;
    }

    setError(null);
    try {
      const updated = await performAuthenticated(() => updateConversation(conversationId, { title }));
      applyConversationUpdate(updated);
      cancelRenameConversation();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rename chat.");
    }
  }

  async function handleTogglePinConversation(conversation: Conversation) {
    if (!authSession) {
      setError("Sign in first.");
      return;
    }

    setOpenConversationMenuId(null);
    setError(null);
    try {
      const updated = await performAuthenticated(() =>
        updateConversation(conversation.id, { is_pinned: !conversation.is_pinned }),
      );
      applyConversationUpdate(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update pinned chat.");
    }
  }

  async function handleDeleteConversation(conversation: Conversation) {
    if (!authSession) {
      setError("Sign in first.");
      return;
    }

    const shouldDelete = window.confirm(`Delete "${conversation.title}"?`);
    if (!shouldDelete) {
      return;
    }

    setOpenConversationMenuId(null);
    setError(null);
    try {
      await performAuthenticated(() => deleteConversation(conversation.id));
      setConversations((prev) => prev.filter((item) => item.id !== conversation.id));
      setActiveConversation((prev) => (prev?.id === conversation.id ? null : prev));
      if (renamingConversationId === conversation.id) {
        cancelRenameConversation();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete chat.");
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
    if (!isActiveModelAvailable) {
      setError(`${activeModel.label} is not available.`);
      void refreshModelStatuses();
      return;
    }

    setIsSending(true);
    setError(null);
    const abortController = new AbortController();
    streamAbortControllerRef.current = abortController;
    let streamingConversationId = "";
    let pendingAssistantMessageId = 0;

    try {
      let conversation = activeConversation;
      if (!conversation) {
        conversation = await performAuthenticated(() => createConversation());
      }

      streamingConversationId = conversation.id;
      const createdAt = new Date().toISOString();
      const pendingUserMessageId = getNextPendingMessageId();
      const pendingAssistantDraftId = getNextPendingMessageId();
      const pendingUserMessage: ChatMessage = {
        id: pendingUserMessageId,
        role: "user",
        content: message,
        thinking_duration_ms: null,
        created_at: createdAt,
      };
      const pendingAssistantMessage: ChatMessage = {
        id: pendingAssistantDraftId,
        role: "assistant",
        content: "",
        thinking_duration_ms: null,
        created_at: createdAt,
      };
      pendingAssistantMessageId = pendingAssistantMessage.id;
      const optimisticConversation: ConversationDetail = {
        ...conversation,
        messages: [...conversation.messages, pendingUserMessage, pendingAssistantMessage],
      };

      setDraft("");
      setIsComposingNewChat(false);
      setCurrentConversationUrl(conversation.id);
      hasScrolledForCurrentResponseRef.current = false;
      setActiveConversation(optimisticConversation);
      setConversations((prev) => {
        const rest = prev.filter((item) => item.id !== optimisticConversation.id);
        return [toConversationSummary(optimisticConversation), ...rest];
      });
      scrollToLatestForNewContent("smooth");

      await performAuthenticated(() =>
        streamMessage(
          conversation.id,
          {
            content: message,
            model: selectedModel,
            system_instruction: systemInstruction.trim() || undefined,
            thinking_enabled: requestedThinkingEnabled,
          },
          (streamEvent) => {
            if (streamEvent.type === "message") {
              setActiveConversation((prev) => {
                if (prev?.id !== conversation.id) {
                  return prev;
                }

                return {
                  ...mergeConversationSummary(prev, streamEvent.conversation),
                  messages: replaceMessageById(prev.messages, pendingUserMessageId, streamEvent.message),
                };
              });
              setConversations((prev) => {
                const rest = prev.filter((item) => item.id !== streamEvent.conversation.id);
                return [streamEvent.conversation, ...rest];
              });
              return;
            }

            if (streamEvent.type === "delta") {
              if (!hasScrolledForCurrentResponseRef.current) {
                hasScrolledForCurrentResponseRef.current = true;
                scrollToLatestForNewContent("smooth");
              }

              setActiveConversation((prev) =>
                prev?.id === conversation.id
                  ? {
                      ...prev,
                      messages: prev.messages.map((item) =>
                        item.id === pendingAssistantMessageId
                          ? { ...item, content: item.content + streamEvent.delta }
                          : item,
                      ),
                    }
                  : prev,
              );
              return;
            }

            if (streamEvent.type === "done") {
              setActiveConversation((prev) =>
                prev?.id === conversation.id
                  ? {
                      ...mergeConversationSummary(prev, streamEvent.conversation),
                      messages: replaceMessageById(
                        prev.messages,
                        pendingAssistantMessageId,
                        streamEvent.message,
                      ),
                    }
                  : prev,
              );
              setConversations((prev) => {
                const rest = prev.filter((item) => item.id !== streamEvent.conversation.id);
                return [streamEvent.conversation, ...rest];
              });
            }
          },
          abortController.signal,
        ),
      );
    } catch (err) {
      setActiveConversation((prev) => {
        if (!streamingConversationId || prev?.id !== streamingConversationId) {
          return prev;
        }

        return {
          ...prev,
          messages: prev.messages.filter(
            (item) => item.id !== pendingAssistantMessageId || item.content.trim(),
          ),
        };
      });

      if (!isAbortError(err)) {
        setError(err instanceof Error ? err.message : "Failed to send message.");
      }
    } finally {
      setIsSending(false);
      if (streamAbortControllerRef.current === abortController) {
        streamAbortControllerRef.current = null;
      }
    }
  }

  async function handleCopyMessage(message: ChatMessage) {
    if (!message.content.trim()) {
      return;
    }

    try {
      await copyTextToClipboard(message.content);
      setOpenMessageMenuId(null);
      setCopiedMessageId(message.id);
      if (copiedMessageTimeoutRef.current !== null) {
        window.clearTimeout(copiedMessageTimeoutRef.current);
      }
      copiedMessageTimeoutRef.current = window.setTimeout(() => {
        setCopiedMessageId((currentId) => (currentId === message.id ? null : currentId));
        copiedMessageTimeoutRef.current = null;
      }, 1500);
    } catch {
      setError("Could not copy message.");
    }
  }

  function handleToggleFeedback(messageId: number, value: "up" | "down") {
    setFeedbackByMessageId((prev) => {
      const next = { ...prev };
      if (next[messageId] === value) {
        delete next[messageId];
      } else {
        next[messageId] = value;
      }
      return next;
    });
  }

  async function handleRegenerateMessage(message: ChatMessage) {
    if (
      !authSession ||
      !activeConversation ||
      isSending ||
      message.role !== "assistant" ||
      message.id < 0 ||
      message.id !== latestAssistantMessageId
    ) {
      return;
    }
    if (!isActiveModelAvailable) {
      setError(`${activeModel.label} is not available.`);
      void refreshModelStatuses();
      return;
    }

    const targetIndex = activeConversation.messages.findIndex((item) => item.id === message.id);
    if (targetIndex < 0) {
      return;
    }

    const originalConversation = activeConversation;
    const abortController = new AbortController();
    const pendingAssistantMessage: ChatMessage = {
      id: getNextPendingMessageId(),
      role: "assistant",
      content: "",
      thinking_duration_ms: null,
      created_at: new Date().toISOString(),
    };
    const pendingAssistantMessageId = pendingAssistantMessage.id;
    let didReceiveStreamEvent = false;

    setIsSending(true);
    setRegeneratingMessageId(message.id);
    setOpenMessageMenuId(null);
    setError(null);
    streamAbortControllerRef.current = abortController;
    hasScrolledForCurrentResponseRef.current = false;

    const optimisticConversation: ConversationDetail = {
      ...activeConversation,
      messages: [...activeConversation.messages.slice(0, targetIndex), pendingAssistantMessage],
    };

    setActiveConversation(optimisticConversation);
    setConversations((prev) => {
      const rest = prev.filter((item) => item.id !== optimisticConversation.id);
      return [toConversationSummary(optimisticConversation), ...rest];
    });
    scrollToLatestForNewContent("smooth");

    try {
      await performAuthenticated(() =>
        streamRegenerateMessage(
          originalConversation.id,
          message.id,
          {
            model: selectedModel,
            system_instruction: systemInstruction.trim() || undefined,
            thinking_enabled: requestedThinkingEnabled,
          },
          (streamEvent) => {
            didReceiveStreamEvent = true;

            if (streamEvent.type === "sync") {
              setActiveConversation((prev) => {
                if (prev?.id !== originalConversation.id) {
                  return prev;
                }

                const pendingAssistant = prev.messages.find(
                  (item) => item.id === pendingAssistantMessageId,
                );
                return {
                  ...streamEvent.conversation,
                  messages: pendingAssistant
                    ? [...streamEvent.conversation.messages, pendingAssistant]
                    : streamEvent.conversation.messages,
                };
              });
              setConversations((prev) => {
                const rest = prev.filter((item) => item.id !== streamEvent.conversation.id);
                return [toConversationSummary(streamEvent.conversation), ...rest];
              });
              return;
            }

            if (streamEvent.type === "delta") {
              if (!hasScrolledForCurrentResponseRef.current) {
                hasScrolledForCurrentResponseRef.current = true;
                scrollToLatestForNewContent("smooth");
              }

              setActiveConversation((prev) =>
                prev?.id === originalConversation.id
                  ? {
                      ...prev,
                      messages: prev.messages.map((item) =>
                        item.id === pendingAssistantMessageId
                          ? { ...item, content: item.content + streamEvent.delta }
                          : item,
                      ),
                    }
                  : prev,
              );
              return;
            }

            if (streamEvent.type === "done") {
              setActiveConversation((prev) =>
                prev?.id === originalConversation.id
                  ? {
                      ...mergeConversationSummary(prev, streamEvent.conversation),
                      messages: replaceMessageById(
                        prev.messages,
                        pendingAssistantMessageId,
                        streamEvent.message,
                      ),
                    }
                  : prev,
              );
              setConversations((prev) => {
                const rest = prev.filter((item) => item.id !== streamEvent.conversation.id);
                return [streamEvent.conversation, ...rest];
              });
            }
          },
          abortController.signal,
        ),
      );
    } catch (err) {
      setActiveConversation((prev) => {
        if (prev?.id !== originalConversation.id) {
          return prev;
        }

        if (!didReceiveStreamEvent) {
          return originalConversation;
        }

        return {
          ...prev,
          messages: prev.messages.filter(
            (item) => item.id !== pendingAssistantMessageId || item.content.trim(),
          ),
        };
      });

      if (!isAbortError(err)) {
        setError(err instanceof Error ? err.message : "Failed to regenerate response.");
      }
    } finally {
      setIsSending(false);
      setRegeneratingMessageId(null);
      if (streamAbortControllerRef.current === abortController) {
        streamAbortControllerRef.current = null;
      }
    }
  }

  function startEditMessage(message: ChatMessage) {
    if (message.role !== "user" || message.id < 0 || isSending) {
      return;
    }

    setEditingMessageId(message.id);
    setEditDraft(message.content);
    setOpenMessageMenuId(null);
    setError(null);
  }

  function cancelEditMessage() {
    setEditingMessageId(null);
    setEditDraft("");
    setPendingEditMessage(null);
    setIsEditWarningOpen(false);
    setShouldRememberEditWarningChoice(false);
  }

  function messageHasLaterConversation(message: ChatMessage) {
    const messages = activeConversation?.messages ?? [];
    const index = messages.findIndex((item) => item.id === message.id);
    return index >= 0 && index < messages.length - 1;
  }

  function handleEditSubmit(message: ChatMessage) {
    const content = editDraft.trim();
    if (!content || message.role !== "user" || message.id < 0 || isSending) {
      return;
    }

    if (messageHasLaterConversation(message) && !suppressEditWarning) {
      setPendingEditMessage(message);
      setIsEditWarningOpen(true);
      setShouldRememberEditWarningChoice(false);
      return;
    }

    void submitEditedMessage(message, content);
  }

  function handleConfirmEditWarning() {
    const message = pendingEditMessage;
    const content = editDraft.trim();
    if (!message || !content) {
      cancelEditMessage();
      return;
    }

    if (shouldRememberEditWarningChoice) {
      setSuppressEditWarning(true);
      window.localStorage.setItem(EDIT_WARNING_DISABLED_STORAGE_KEY, "true");
    }

    setPendingEditMessage(null);
    setIsEditWarningOpen(false);
    setShouldRememberEditWarningChoice(false);
    void submitEditedMessage(message, content);
  }

  async function submitEditedMessage(message: ChatMessage, content: string) {
    if (!authSession || !activeConversation || message.role !== "user" || message.id < 0 || isSending) {
      return;
    }
    if (!isActiveModelAvailable) {
      setError(`${activeModel.label} is not available.`);
      void refreshModelStatuses();
      return;
    }

    const targetIndex = activeConversation.messages.findIndex((item) => item.id === message.id);
    if (targetIndex < 0) {
      return;
    }

    const originalConversation = activeConversation;
    const abortController = new AbortController();
    const editedUserMessage: ChatMessage = {
      ...message,
      content,
    };
    const pendingAssistantMessage: ChatMessage = {
      id: getNextPendingMessageId(),
      role: "assistant",
      content: "",
      thinking_duration_ms: null,
      created_at: new Date().toISOString(),
    };
    const pendingAssistantMessageId = pendingAssistantMessage.id;
    let didReceiveStreamEvent = false;

    setIsSending(true);
    setError(null);
    setEditingMessageId(null);
    setEditDraft("");
    streamAbortControllerRef.current = abortController;
    hasScrolledForCurrentResponseRef.current = false;

    const optimisticConversation: ConversationDetail = {
      ...activeConversation,
      messages: [
        ...activeConversation.messages.slice(0, targetIndex),
        editedUserMessage,
        pendingAssistantMessage,
      ],
    };

    setActiveConversation(optimisticConversation);
    setConversations((prev) => {
      const rest = prev.filter((item) => item.id !== optimisticConversation.id);
      return [toConversationSummary(optimisticConversation), ...rest];
    });
    scrollToLatestForNewContent("smooth");

    try {
      await performAuthenticated(() =>
        streamEditMessage(
          originalConversation.id,
          message.id,
          {
            content,
            model: selectedModel,
            system_instruction: systemInstruction.trim() || undefined,
            thinking_enabled: requestedThinkingEnabled,
          },
          (streamEvent) => {
            didReceiveStreamEvent = true;

            if (streamEvent.type === "sync") {
              setActiveConversation((prev) => {
                if (prev?.id !== originalConversation.id) {
                  return prev;
                }

                const pendingAssistant = prev.messages.find(
                  (item) => item.id === pendingAssistantMessageId,
                );
                return {
                  ...streamEvent.conversation,
                  messages: pendingAssistant
                    ? [...streamEvent.conversation.messages, pendingAssistant]
                    : streamEvent.conversation.messages,
                };
              });
              setConversations((prev) => {
                const rest = prev.filter((item) => item.id !== streamEvent.conversation.id);
                return [toConversationSummary(streamEvent.conversation), ...rest];
              });
              return;
            }

            if (streamEvent.type === "delta") {
              if (!hasScrolledForCurrentResponseRef.current) {
                hasScrolledForCurrentResponseRef.current = true;
                scrollToLatestForNewContent("smooth");
              }

              setActiveConversation((prev) =>
                prev?.id === originalConversation.id
                  ? {
                      ...prev,
                      messages: prev.messages.map((item) =>
                        item.id === pendingAssistantMessageId
                          ? { ...item, content: item.content + streamEvent.delta }
                          : item,
                      ),
                    }
                  : prev,
              );
              return;
            }

            if (streamEvent.type === "done") {
              setActiveConversation((prev) =>
                prev?.id === originalConversation.id
                  ? {
                      ...mergeConversationSummary(prev, streamEvent.conversation),
                      messages: replaceMessageById(
                        prev.messages,
                        pendingAssistantMessageId,
                        streamEvent.message,
                      ),
                    }
                  : prev,
              );
              setConversations((prev) => {
                const rest = prev.filter((item) => item.id !== streamEvent.conversation.id);
                return [streamEvent.conversation, ...rest];
              });
            }
          },
          abortController.signal,
        ),
      );
    } catch (err) {
      setActiveConversation((prev) => {
        if (prev?.id !== originalConversation.id) {
          return prev;
        }

        if (!didReceiveStreamEvent) {
          return originalConversation;
        }

        return {
          ...prev,
          messages: prev.messages.filter(
            (item) => item.id !== pendingAssistantMessageId || item.content.trim(),
          ),
        };
      });

      if (!isAbortError(err)) {
        setError(err instanceof Error ? err.message : "Failed to edit message.");
      }
    } finally {
      setIsSending(false);
      if (streamAbortControllerRef.current === abortController) {
        streamAbortControllerRef.current = null;
      }
    }
  }

  async function handleForkMessage(message: ChatMessage) {
    if (
      !authSession ||
      !activeConversation ||
      message.role !== "assistant" ||
      message.id < 0 ||
      forkingMessageId !== null
    ) {
      return;
    }

    setForkingMessageId(message.id);
    setOpenMessageMenuId(null);
    setError(null);
    const forkTab = window.open("about:blank", "_blank");

    try {
      const forkedConversation = await performAuthenticated(() =>
        forkConversationFromMessage(activeConversation.id, message.id),
      );

      setConversations((prev) => {
        const rest = prev.filter((item) => item.id !== forkedConversation.id);
        return [toConversationSummary(forkedConversation), ...rest];
      });

      if (forkTab) {
        forkTab.location.replace(buildConversationUrl(forkedConversation.id));
      } else {
        setError("Your browser blocked the new tab. Allow pop-ups and try forking again.");
      }
    } catch (err) {
      forkTab?.close();
      setError(err instanceof Error ? err.message : "Failed to fork chat.");
    } finally {
      setForkingMessageId(null);
    }
  }

  function renderMessageActions(message: ChatMessage) {
    if (!message.content.trim()) {
      return null;
    }

    const feedback = feedbackByMessageId[message.id];
    const isAssistant = message.role === "assistant";
    const isCopied = copiedMessageId === message.id;
    const isRegenerating = regeneratingMessageId === message.id;
    const isForking = forkingMessageId === message.id;
    const canRegenerate = isAssistant && message.id === latestAssistantMessageId;
    const canFork = isAssistant && message.id > 0;
    const canEdit = message.role === "user" && message.id > 0 && !isSending;
    const actionButtonClass = cn(
      "grid h-8 w-8 place-items-center rounded-lg transition disabled:cursor-not-allowed disabled:opacity-50",
      isDark
        ? "text-[#d7d7d7] hover:bg-[#2f2f2f] hover:text-[#f4f4f4]"
        : "text-[#555555] hover:bg-[#f1f1f1] hover:text-[#171717]",
    );
    const selectedFeedbackClass = isDark ? "text-[#f4f4f4]" : "text-[#171717]";

    return (
      <div
        data-message-actions
        dir="ltr"
        className={cn(
          "relative mt-2 flex items-center gap-1",
          message.role === "user" ? "justify-end" : "justify-start",
        )}
      >
        <button
          type="button"
          onClick={() => void handleCopyMessage(message)}
          className={actionButtonClass}
          title={isCopied ? "Copied" : "Copy"}
          aria-label={isCopied ? "Message copied" : "Copy message"}
        >
          {isCopied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </button>

        {canEdit ? (
          <button
            type="button"
            onClick={() => startEditMessage(message)}
            className={actionButtonClass}
            title="Edit message"
            aria-label="Edit message"
          >
            <Pencil className="h-4 w-4" />
          </button>
        ) : null}

        {isAssistant ? (
          <>
            <button
              type="button"
              onClick={() => handleToggleFeedback(message.id, "up")}
              className={cn(actionButtonClass, feedback === "up" ? selectedFeedbackClass : "")}
              title="Good response"
              aria-label="Good response"
              aria-pressed={feedback === "up"}
            >
              <ThumbsUp className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => handleToggleFeedback(message.id, "down")}
              className={cn(actionButtonClass, feedback === "down" ? selectedFeedbackClass : "")}
              title="Bad response"
              aria-label="Bad response"
              aria-pressed={feedback === "down"}
            >
              <ThumbsDown className="h-4 w-4" />
            </button>
            {canRegenerate ? (
              <button
                type="button"
                onClick={() => void handleRegenerateMessage(message)}
                disabled={isSending || message.id < 0}
                className={actionButtonClass}
                title="Try again"
                aria-label="Try again"
              >
                <RotateCcw className={cn("h-4 w-4", isRegenerating ? "animate-spin" : "")} />
              </button>
            ) : null}
            {canFork ? (
              <button
                type="button"
                onClick={() => void handleForkMessage(message)}
                disabled={forkingMessageId !== null}
                className={actionButtonClass}
                title="Fork chat"
                aria-label="Fork chat"
              >
                <GitFork className={cn("h-4 w-4", isForking ? "animate-spin" : "")} />
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setOpenMessageMenuId((currentId) => (currentId === message.id ? null : message.id))}
              className={actionButtonClass}
              title="More"
              aria-label="More message actions"
              aria-expanded={openMessageMenuId === message.id}
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>
            {openMessageMenuId === message.id ? (
              <div
                className={cn(
                  "absolute left-0 top-9 z-50 w-44 rounded-lg border p-1 text-sm shadow-xl",
                  isDark
                    ? "border-[#3a3a3a] bg-[#2f2f2f] text-[#ececec]"
                    : "border-[#dedede] bg-white text-[#171717]",
                )}
              >
                <button
                  type="button"
                  onClick={() => void handleCopyMessage(message)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-2 text-start transition",
                    isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                  )}
                >
                  {isCopied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                  <span>{isCopied ? "Copied" : "Copy"}</span>
                </button>
                {canRegenerate ? (
                  <button
                    type="button"
                    onClick={() => void handleRegenerateMessage(message)}
                    disabled={isSending || message.id < 0}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-2 text-start transition disabled:cursor-not-allowed disabled:opacity-50",
                      isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                    )}
                  >
                    <RotateCcw className={cn("h-4 w-4", isRegenerating ? "animate-spin" : "")} />
                    <span>Try again</span>
                  </button>
                ) : null}
                {canFork ? (
                  <button
                    type="button"
                    onClick={() => void handleForkMessage(message)}
                    disabled={forkingMessageId !== null}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-2 text-start transition disabled:cursor-not-allowed disabled:opacity-50",
                      isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                    )}
                  >
                    <GitFork className={cn("h-4 w-4", isForking ? "animate-spin" : "")} />
                    <span>Fork chat</span>
                  </button>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    );
  }

  async function handleRequestOtp(event: React.FormEvent) {
    event.preventDefault();
    const clean = getSubmitPhoneNumber(phoneInput);
    if (isAuthenticating) {
      return;
    }

    if (!clean) {
      setError("Phone number must be 11 English digits and start with 09.");
      return;
    }

    setIsAuthenticating(true);
    setError(null);

    try {
      const result = await requestOtp(clean, authMode);
      setPhoneInput(clean);
      setIsOtpRequested(true);
      setDevOtp(result.otp_code ?? "");
      setOtpInput(result.otp_code ? normalizeOtpDraft(result.otp_code) : "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send OTP.");
    } finally {
      setIsAuthenticating(false);
    }
  }

  async function handleVerifyOtp(event: React.FormEvent) {
    event.preventDefault();
    const clean = getSubmitPhoneNumber(phoneInput);
    const code = normalizeOtpDraft(otpInput);
    if (isAuthenticating) {
      return;
    }

    if (!clean) {
      setError("Phone number must be 11 English digits and start with 09.");
      return;
    }

    if (code.length !== 6) {
      setError("OTP must be a 6-digit code.");
      return;
    }

    setIsAuthenticating(true);
    setError(null);

    try {
      const session = await verifyOtp({ phone_number: clean, otp: code, auth_mode: authMode });
      persistAuthSession({ authenticated: true });
      setCurrentUser(session.user);
      setPhoneNumber(session.user.phone_number);
      setPhoneInput(session.user.phone_number);
      setChangePhoneInput(session.user.phone_number);
      setProfileFirstName(session.user.first_name);
      setProfileLastName(session.user.last_name);
      setActiveConversation(null);
      setIsComposingNewChat(true);
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
    closeProfileMenus();
    setIsAccountModalOpen(false);
  }

  function resizeDraftTextarea() {
    const textarea = draftTextareaRef.current;
    if (!textarea) {
      return;
    }

    const maxHeight = 176;
    textarea.style.height = "auto";
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }

  useEffect(() => {
    resizeDraftTextarea();
  }, [draft]);

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
          <div key={conversation.id} className="group relative" data-conversation-menu>
            {renamingConversationId === conversation.id ? (
              <form
                onSubmit={(event) => void handleRenameConversation(event, conversation.id)}
                className={cn(
                  "flex h-9 w-full items-center gap-2 rounded-lg px-2",
                  isDark ? "bg-[#303030]" : "bg-[#ececec]",
                )}
              >
                <MessageSquare className="h-4 w-4 shrink-0 opacity-70" />
                <input
                  value={renameDraft}
                  onChange={(event) => setRenameDraft(event.target.value)}
                  autoFocus
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      cancelRenameConversation();
                    }
                  }}
                  className={cn(
                    "min-w-0 flex-1 bg-transparent text-sm outline-none",
                    isDark ? "text-[#ececec]" : "text-[#171717]",
                  )}
                />
                <button
                  type="button"
                  onClick={cancelRenameConversation}
                  className="grid h-6 w-6 shrink-0 place-items-center rounded-md opacity-70 transition hover:opacity-100"
                  aria-label="Cancel rename"
                >
                  <X className="h-4 w-4" />
                </button>
              </form>
            ) : (
              <Link
                href={getConversationHref(conversation.id)}
                onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                    return;
                  }
                  event.preventDefault();
                  void handleSelectConversation(conversation.id);
                }}
                className={cn(
                  "group flex h-9 w-full cursor-pointer items-center gap-2 rounded-lg px-3 text-start text-sm transition",
                  activeConversationId === conversation.id
                    ? isDark
                      ? "bg-[#303030] text-[#ececec]"
                      : "bg-[#ececec] text-[#171717]"
                    : isDark
                      ? "text-[#ececec] hover:bg-[#2a2a2a]"
                      : "text-[#171717] hover:bg-[#ececec]",
                )}
              >
                {conversation.is_pinned ? (
                  <Pin className="h-4 w-4 shrink-0 opacity-70" />
                ) : (
                  <MessageSquare className="h-4 w-4 shrink-0 opacity-70" />
                )}
                <span className="min-w-0 flex-1 truncate">{conversation.title}</span>
              </Link>
            )}

            {renamingConversationId !== conversation.id ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setOpenConversationMenuId((value) =>
                    value === conversation.id ? null : conversation.id,
                  );
                }}
                className={cn(
                  "absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md transition",
                  openConversationMenuId === conversation.id
                    ? isDark
                      ? "bg-[#3a3a3a] opacity-100"
                      : "bg-[#dedede] opacity-100"
                    : "opacity-0 group-hover:opacity-100",
                  isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#dedede]",
                )}
                aria-label="Open chat menu"
              >
                <MoreHorizontal className="h-4 w-4" />
              </button>
            ) : null}

            {openConversationMenuId === conversation.id ? (
              <div
                className={cn(
                  "absolute right-1 top-9 z-50 w-44 rounded-lg border p-1 shadow-xl",
                  isDark
                    ? "border-[#3a3a3a] bg-[#2f2f2f] text-[#ececec]"
                    : "border-[#dedede] bg-white text-[#171717]",
                )}
              >
                <button
                  type="button"
                  onClick={() => startRenameConversation(conversation)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-2 text-start text-sm transition",
                    isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                  )}
                >
                  <Pencil className="h-4 w-4" />
                  <span>Rename</span>
                </button>
                <button
                  type="button"
                  onClick={() => void handleTogglePinConversation(conversation)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-2 text-start text-sm transition",
                    isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
                  )}
                >
                  {conversation.is_pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
                  <span>{conversation.is_pinned ? "Unpin chat" : "Pin chat"}</span>
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteConversation(conversation)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-2 text-start text-sm text-[#ef4444] transition",
                    isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#fef2f2]",
                  )}
                >
                  <Trash2 className="h-4 w-4" />
                  <span>Delete</span>
                </button>
              </div>
            ) : null}
          </div>
        ))}
      </section>
    );
  }

  function renderProfileMenu() {
    return (
      <>
        <div className="flex items-center gap-3 px-3 py-3">
          {renderAvatar("h-10 w-10")}
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{profileDisplayName}</p>
            <p className={cn("truncate text-xs", isDark ? "text-[#b4b4b4]" : "text-[#6f6f6f]")}>
              {currentUser?.phone_number || phoneNumber || "Not signed in"}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => openAccountModal("profile")}
          className={cn(
            "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
            isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
          )}
        >
          <UserRound className="h-4 w-4" />
          <span>Profile</span>
        </button>
        <button
          type="button"
          onClick={() => openAccountModal("personalization")}
          className={cn(
            "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
            isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
          )}
        >
          <Palette className="h-4 w-4" />
          <span>Personalization</span>
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          className={cn(
            "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
            isDark ? "hover:bg-[#3a3a3a]" : "hover:bg-[#f4f4f4]",
          )}
        >
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          <span>{isDark ? "Light mode" : "Night mode"}</span>
        </button>
        {isAuthenticated ? (
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
      </>
    );
  }

  function renderAccountModal() {
    if (!isAccountModalOpen) {
      return null;
    }

    const modalIsDark = isDark;
    const modalFieldClass = cn(
      "h-10 w-full rounded-lg border px-3 text-sm outline-none transition",
      modalIsDark
        ? "!border-[#4a4a4a] !bg-[#303030] !text-[#f4f4f4] !placeholder:text-[#a8a8a8] focus:!border-[#6f6f6f]"
        : "!border-[#d4d4d4] !bg-[#f7f7f7] !text-[#171717] !placeholder:text-[#777777] focus:!border-[#9a9a9a]",
    );
    const modalTextareaClass = cn(
      "min-h-32 w-full resize-y rounded-lg border px-3 py-2 text-sm leading-6 outline-none transition",
      modalIsDark
        ? "!border-[#4a4a4a] !bg-[#303030] !text-[#f4f4f4] !placeholder:text-[#a8a8a8] focus:!border-[#6f6f6f]"
        : "!border-[#d4d4d4] !bg-[#f7f7f7] !text-[#171717] !placeholder:text-[#777777] focus:!border-[#9a9a9a]",
    );
    const mutedTextClass = modalIsDark ? "text-[#b4b4b4]" : "text-[#6f6f6f]";
    const dividerClass = modalIsDark ? "border-[#3a3a3a]" : "border-[#dddddd]";
    const navItemClass = (tab: AccountTab) =>
      cn(
        "flex h-9 w-full items-center gap-3 rounded-lg px-3 text-sm transition",
        accountTab === tab
          ? modalIsDark
            ? "bg-[#3a3a3a] text-[#f4f4f4]"
            : "bg-[#e9e9e9] text-[#171717]"
          : modalIsDark
            ? "text-[#f4f4f4] hover:bg-[#303030]"
            : "text-[#303030] hover:bg-[#eeeeee]",
      );

    return (
      <div className="fixed inset-0 z-[80] flex items-center justify-center px-4 py-6">
        <button
          type="button"
          className="absolute inset-0 bg-black/70 backdrop-blur-[1px]"
          aria-label="Close account settings"
          onClick={() => setIsAccountModalOpen(false)}
        />
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Account settings"
          className={cn(
            "relative grid h-[min(600px,92vh)] w-full max-w-[680px] grid-cols-[196px_minmax(0,1fr)] overflow-hidden rounded-2xl border text-sm shadow-2xl",
            modalIsDark
              ? "border-[#2f2f2f] bg-[#212121] text-[#f4f4f4]"
              : "border-[#d9d9d9] bg-white text-[#171717]",
          )}
        >
          <aside
            className={cn(
              "flex min-h-0 flex-col border-r px-2 py-4",
              modalIsDark ? "border-[#363636] bg-[#212121]" : "border-[#e5e5e5] bg-[#f7f7f7]",
            )}
          >
            <button
              type="button"
              onClick={() => setIsAccountModalOpen(false)}
              className={cn(
                "mb-6 grid h-9 w-9 place-items-center rounded-lg transition",
                modalIsDark ? "hover:bg-[#303030]" : "hover:bg-[#e9e9e9]",
              )}
              aria-label="Close"
            >
              <X className="h-5 w-5" />
            </button>

            <nav className="space-y-1">
              <button type="button" onClick={() => setAccountTab("profile")} className={navItemClass("profile")}>
                <UserRound className="h-4 w-4" />
                <span>Profile</span>
              </button>
              <button
                type="button"
                onClick={() => setAccountTab("personalization")}
                className={navItemClass("personalization")}
              >
                <Palette className="h-4 w-4" />
                <span>Personalization</span>
              </button>
              <button
                type="button"
                disabled
                className={cn(
                  "flex h-9 w-full items-center gap-3 rounded-lg px-3 text-sm opacity-60",
                  modalIsDark ? "text-[#f4f4f4]" : "text-[#303030]",
                )}
              >
                <Settings className="h-4 w-4" />
                <span>General</span>
              </button>
            </nav>
          </aside>

          <section className={cn("min-h-0 overflow-y-auto px-6 py-5", modalIsDark ? "bg-[#212121]" : "bg-white")}>
            <div className={cn("mb-5 border-b pb-4", dividerClass)}>
              <h2 className="text-lg font-medium leading-7">
                {accountTab === "profile" ? "Profile" : "Personalization"}
              </h2>
            </div>

            {accountTab === "profile" ? (
              <div className="space-y-6">
                {!isAuthenticated ? (
                  <div className={cn("rounded-lg border px-4 py-3 text-sm", dividerClass, mutedTextClass)}>
                    Sign in to edit your profile.
                  </div>
                ) : (
                  <>
                    <form onSubmit={handleSaveProfile} className="space-y-5">
                      <div className="flex items-center gap-4">
                        {renderAvatar("h-16 w-16")}
                        <div className="min-w-0">
                          <label
                            htmlFor="profile-image-input"
                            className={cn(
                              "inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm font-medium transition",
                              modalIsDark ? "bg-[#303030] hover:bg-[#3a3a3a]" : "bg-[#ececec] hover:bg-[#e1e1e1]",
                            )}
                          >
                            <Camera className="h-4 w-4" />
                            <span>Upload image</span>
                          </label>
                          <input
                            id="profile-image-input"
                            type="file"
                            accept="image/*"
                            className="sr-only"
                            onChange={(event) => setProfileImageFile(event.target.files?.[0] ?? null)}
                          />
                          {profileImageFile ? (
                            <p className={cn("mt-2 truncate text-xs", mutedTextClass)}>
                              {profileImageFile.name}
                            </p>
                          ) : null}
                        </div>
                      </div>

                      <div className="grid gap-4 sm:grid-cols-2">
                        <label className="space-y-2 text-sm">
                          <span className="font-medium">Name</span>
                          <input
                            value={profileFirstName}
                            onChange={(event) => setProfileFirstName(event.target.value)}
                            maxLength={150}
                            autoComplete="given-name"
                            className={modalFieldClass}
                          />
                        </label>
                        <label className="space-y-2 text-sm">
                          <span className="font-medium">Family name</span>
                          <input
                            value={profileLastName}
                            onChange={(event) => setProfileLastName(event.target.value)}
                            maxLength={150}
                            autoComplete="family-name"
                            className={modalFieldClass}
                          />
                        </label>
                      </div>

                      <div className="flex flex-wrap items-center gap-3">
                        <Button
                          type="submit"
                          disabled={isSavingProfile}
                          className="h-9 rounded-full bg-[#10a37f] px-5 text-white hover:bg-[#0d8f6f]"
                        >
                          {isSavingProfile ? "Saving..." : "Save profile"}
                        </Button>
                        {profileMessage ? <span className="text-sm text-[#10a37f]">{profileMessage}</span> : null}
                        {profileError ? <span className="text-sm text-[#ef4444]">{profileError}</span> : null}
                      </div>
                    </form>

                    <div className={cn("border-t pt-5", dividerClass)}>
                      <div className="mb-3 flex items-center gap-2">
                        <Phone className="h-4 w-4 opacity-75" />
                        <h3 className="text-base font-medium">Phone number</h3>
                      </div>
                      <p className={cn("mb-3 text-sm", mutedTextClass)}>
                        Current: {currentUser?.phone_number || phoneNumber}
                      </p>
                      <form onSubmit={handleRequestPhoneChangeOtp} className="flex flex-col gap-3 sm:flex-row">
                        <input
                          value={changePhoneInput}
                          onChange={(event) => {
                            setChangePhoneInput(normalizePhoneDraft(event.target.value));
                            setIsPhoneChangeOtpRequested(false);
                            setChangePhoneOtpInput("");
                            setChangePhoneDevOtp("");
                            setPhoneChangeError(null);
                            setPhoneChangeMessage(null);
                          }}
                          inputMode="numeric"
                          autoComplete="tel-national"
                          maxLength={PHONE_LENGTH}
                          className={cn(modalFieldClass, "font-mono")}
                        />
                        <Button
                          type="submit"
                          disabled={isRequestingPhoneOtp || !isChangePhoneValid || isChangePhoneSame}
                          className="h-10 shrink-0 rounded-full bg-[#4668d9] px-5 text-white hover:bg-[#5577ea]"
                        >
                          {isRequestingPhoneOtp ? "Sending..." : "Send OTP"}
                        </Button>
                      </form>

                      {isPhoneChangeOtpRequested ? (
                        <form onSubmit={handleVerifyPhoneChangeOtp} className="mt-3 flex flex-col gap-3 sm:flex-row">
                          <input
                            value={changePhoneOtpInput}
                            onChange={(event) => setChangePhoneOtpInput(normalizeOtpDraft(event.target.value))}
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            placeholder="6-digit OTP"
                            maxLength={6}
                            className={cn(modalFieldClass, "font-mono")}
                          />
                          <Button
                            type="submit"
                            disabled={isVerifyingPhoneOtp || changePhoneOtpInput.length !== 6}
                            className="h-10 shrink-0 rounded-full bg-[#10a37f] px-5 text-white hover:bg-[#0d8f6f]"
                          >
                            {isVerifyingPhoneOtp ? "Verifying..." : "Verify"}
                          </Button>
                        </form>
                      ) : null}

                      {changePhoneDevOtp ? (
                        <div
                          className={cn(
                            "mt-3 rounded-lg px-3 py-2 text-sm",
                            modalIsDark ? "bg-[#303030] text-[#b4b4b4]" : "bg-[#f6f6f6] text-[#6f6f6f]",
                          )}
                        >
                          Dev OTP: <span className="font-mono">{changePhoneDevOtp}</span>
                        </div>
                      ) : null}
                      {phoneChangeMessage ? <p className="mt-3 text-sm text-[#10a37f]">{phoneChangeMessage}</p> : null}
                      {phoneChangeError ? <p className="mt-3 text-sm text-[#ef4444]">{phoneChangeError}</p> : null}
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="space-y-5">
                <div className={cn("border-b pb-5", dividerClass)}>
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h3 className="font-medium">Base style and tone</h3>
                      <p className={cn("mt-1 max-w-[340px] text-xs leading-5", mutedTextClass)}>
                        Set how this assistant responds to you.
                      </p>
                    </div>
                    <select
                      value={theme}
                      onChange={(event) => {
                        const nextTheme = event.target.value as Theme;
                        setTheme(nextTheme);
                        window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
                      }}
                      className={cn(
                        "h-9 rounded-lg border px-3 text-sm outline-none",
                        modalIsDark
                          ? "border-[#3f3f3f] bg-[#303030] text-[#f4f4f4]"
                          : "border-[#d4d4d4] bg-[#f7f7f7] text-[#171717]",
                      )}
                    >
                      <option value="dark">Night</option>
                      <option value="light">Light</option>
                    </select>
                  </div>
                </div>

                <div className={cn("space-y-4 border-b pb-5", dividerClass)}>
                  <div>
                    <h3 className="font-medium">Characteristics</h3>
                    <p className={cn("mt-1 text-xs", mutedTextClass)}>
                      Choose additional customizations on top of your base style and tone.
                    </p>
                  </div>
                  {["Warm", "Enthusiastic", "Headers & Lists", "Emoji"].map((label) => (
                    <div key={label} className="flex h-9 items-center justify-between gap-4">
                      <span>{label}</span>
                      <button
                        type="button"
                        className={cn(
                          "flex h-9 items-center gap-2 rounded-lg px-3 text-sm transition",
                          modalIsDark ? "bg-[#303030] hover:bg-[#3a3a3a]" : "bg-[#f2f2f2] hover:bg-[#e8e8e8]",
                        )}
                      >
                        <span>Default</span>
                        <ChevronDown className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>

                <div className={cn("border-b pb-5", dividerClass)}>
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h3 className="font-medium">Fast answers</h3>
                      <p className={cn("mt-1 max-w-[370px] text-xs leading-5", mutedTextClass)}>
                        Use shorter, faster replies when depth is not needed.
                      </p>
                    </div>
                    <button
                      type="button"
                      aria-label="Fast answers enabled"
                      className="relative h-5 w-9 rounded-full bg-[#4f7df3]"
                    >
                      <span className="absolute right-0.5 top-0.5 h-4 w-4 rounded-full bg-white" />
                    </button>
                  </div>
                </div>

                <label className="block space-y-2 text-sm">
                  <span className="font-medium">Custom instructions</span>
                  <textarea
                    value={systemInstruction}
                    onChange={(event) => handleSystemInstructionChange(event.target.value)}
                    dir="auto"
                    className={modalTextareaClass}
                  />
                </label>

                <div className={cn("border-t pt-5", dividerClass)}>
                  <h3 className="text-lg font-medium">About you</h3>
                  <div className="mt-4 space-y-4">
                    <label className="block space-y-2 text-sm">
                      <span className="font-medium">Nickname</span>
                      <input
                        value={profileFirstName || profileDisplayName}
                        readOnly
                        className={cn(modalFieldClass, "cursor-default")}
                      />
                    </label>
                    <label className="block space-y-2 text-sm">
                      <span className="font-medium">More about you</span>
                      <input
                        readOnly
                        placeholder="Interests, values, or preferences to keep in mind"
                        className={cn(modalFieldClass, "cursor-default")}
                      />
                    </label>
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    );
  }

  function renderEditWarningModal() {
    if (!isEditWarningOpen || !pendingEditMessage) {
      return null;
    }

    return (
      <div className="fixed inset-0 z-[90] flex items-center justify-center px-4 py-6">
        <button
          type="button"
          className="absolute inset-0 bg-black/70 backdrop-blur-[1px]"
          aria-label="Cancel message edit"
          onClick={() => {
            setPendingEditMessage(null);
            setIsEditWarningOpen(false);
            setShouldRememberEditWarningChoice(false);
          }}
        />
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Confirm message edit"
          className={cn(
            "relative w-full max-w-[440px] rounded-2xl border p-5 shadow-2xl",
            isDark
              ? "border-[#3a3a3a] bg-[#212121] text-[#f4f4f4]"
              : "border-[#dedede] bg-white text-[#171717]",
          )}
        >
          <span
            className={cn(
              "inline-flex h-7 items-center rounded-full px-3 text-xs font-medium",
              isDark ? "bg-[#303030] text-[#d1d1d1]" : "bg-[#f1f1f1] text-[#5f5f5f]",
            )}
          >
            Message edit
          </span>
          <h2 className="mt-4 text-lg font-medium">Replace this point in the conversation?</h2>
          <p className={cn("mt-2 text-sm leading-6", isDark ? "text-[#c5c5c5]" : "text-[#5f5f5f]")}>
            Sending this edit will remove the replies and follow-up messages after this request, then generate a
            fresh response from your revised message.
          </p>
          <label className="mt-4 flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={shouldRememberEditWarningChoice}
              onChange={(event) => setShouldRememberEditWarningChoice(event.target.checked)}
              className="h-4 w-4 accent-[#10a37f]"
            />
            <span>Don&apos;t warn me before replacing later messages again.</span>
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setPendingEditMessage(null);
                setIsEditWarningOpen(false);
                setShouldRememberEditWarningChoice(false);
              }}
              className={cn(
                "h-10 rounded-full px-4 text-sm font-medium transition",
                isDark ? "bg-black text-white hover:bg-[#171717]" : "bg-[#e5e5e5] text-[#171717] hover:bg-[#d7d7d7]",
              )}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmEditWarning}
              className={cn(
                "h-10 rounded-full px-5 text-sm font-medium transition",
                isDark ? "bg-white text-black hover:bg-[#e7e7e7]" : "bg-[#171717] text-white hover:bg-[#303030]",
              )}
            >
              Send edit
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <SidebarProvider
      open={isSidebarOpen}
      onOpenChange={setIsSidebarOpen}
      className={cn(
        "h-screen overflow-hidden font-sans",
        isDark ? "bg-[#212121] text-[#ececec]" : "bg-white text-[#171717]",
      )}
    >
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-[304px] shrink-0 flex-col border-r transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] will-change-transform md:relative",
            isSidebarOpen ? "translate-x-0 md:ml-0" : "-translate-x-full md:-ml-[304px] md:translate-x-0",
            isDark ? "border-[#2f2f2f] bg-[#171717]" : "border-[#e5e5e5] bg-[#f9f9f9]",
          )}
        >
          <div className="flex h-14 items-center justify-between px-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full">
              <Bot className="h-6 w-6" />
            </div>
            <SidebarTrigger
              className={cn(
                isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
              )}
              aria-label="Close sidebar"
            >
              <PanelLeftClose className="h-5 w-5" />
            </SidebarTrigger>
          </div>

          <div className="space-y-2 px-3 pb-3">
            <Link
              href="/"
              aria-disabled={!isAuthenticated}
              onClick={(event) => {
                if (!isAuthenticated) {
                  event.preventDefault();
                  setError("Sign in first.");
                  return;
                }
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                  return;
                }
                event.preventDefault();
                handleCreateConversation();
              }}
              className={cn(
                "group flex h-9 w-full items-center gap-2 rounded-lg px-3 text-start text-sm transition",
                isAuthenticated ? "cursor-pointer" : "cursor-not-allowed opacity-50",
                isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
              )}
            >
              <SquarePen className="h-4 w-4 shrink-0 opacity-70" />
              <span>New chat</span>
            </Link>

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
            {!isAuthenticated ? (
              <div
                className={cn(
                  "mx-1 rounded-lg border border-dashed px-3 py-4 text-sm",
                  isDark ? "border-[#3a3a3a] text-[#9b9b9b]" : "border-[#dedede] text-[#6f6f6f]",
                )}
              >
                Sign in to load your chats.
              </div>
            ) : null}

            {isAuthenticated && isLoadingConversations ? (
              <div className={cn("px-3 py-2 text-sm", isDark ? "text-[#9b9b9b]" : "text-[#6f6f6f]")}>
                Loading chats...
              </div>
            ) : null}

            {renderConversationGroup("Pinned", groupedConversations.pinned)}
            {renderConversationGroup("Today", groupedConversations.today)}
            {renderConversationGroup("Last week", groupedConversations.lastWeek)}
            {renderConversationGroup("More than 30 days", groupedConversations.olderThan30)}

            {isAuthenticated &&
            !isLoadingConversations &&
            conversations.length > 0 &&
            !groupedConversations.pinned.length &&
            !groupedConversations.today.length &&
            !groupedConversations.lastWeek.length &&
            !groupedConversations.olderThan30.length ? (
              <div className={cn("px-3 py-2 text-sm", isDark ? "text-[#9b9b9b]" : "text-[#6f6f6f]")}>
                No chats found.
              </div>
            ) : null}
          </ScrollArea>

          <div className={cn("relative border-t p-3", isDark ? "border-[#242424]" : "border-[#e9e9e9]")} data-profile-menu>
            <button
              type="button"
              onClick={() => {
                setIsSidebarProfileMenuOpen((value) => !value);
                setIsProfileMenuOpen(false);
              }}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg p-2 text-start transition",
                isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
              )}
            >
              {renderAvatar("h-9 w-9")}
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{profileDisplayName}</p>
                <p className={cn("truncate text-xs", isDark ? "text-[#9b9b9b]" : "text-[#6f6f6f]")}>
                  {currentUser?.phone_number || phoneNumber || "Not signed in"}
                </p>
              </div>
              <MoreHorizontal className="h-5 w-5 opacity-70" />
            </button>

            {isSidebarProfileMenuOpen ? (
              <div
                className={cn(
                  "absolute bottom-[72px] left-3 right-3 z-50 rounded-xl border p-2 shadow-2xl",
                  isDark
                    ? "border-[#3a3a3a] bg-[#2f2f2f] text-[#ececec]"
                    : "border-[#dedede] bg-white text-[#171717]",
                )}
              >
                {renderProfileMenu()}
              </div>
            ) : null}
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

        <SidebarInset>
          <header
            className={cn(
              "relative flex h-14 shrink-0 items-center justify-between border-b px-3 md:px-4",
              isDark ? "border-[#2f2f2f] bg-[#212121]" : "border-[#eeeeee] bg-white",
            )}
          >
            <div className="flex min-w-0 items-center gap-2">
              <SidebarTrigger
                className={cn(
                  isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
                )}
                aria-label="Toggle sidebar"
              >
                <Menu className="h-5 w-5" />
              </SidebarTrigger>

              <div ref={modelMenuRef} className="relative">
                <button
                  type="button"
                  onClick={() => {
                    setIsModelMenuOpen((value) => {
                      const nextValue = !value;
                      if (nextValue) {
                        void refreshModelStatuses();
                      }
                      return nextValue;
                    });
                  }}
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
                    aria-busy={isRefreshingModelStatuses}
                    className={cn(
                      "absolute left-0 top-12 z-50 w-[300px] rounded-xl border p-2 shadow-2xl",
                      isDark
                        ? "border-[#3a3a3a] bg-[#2f2f2f] text-[#ececec]"
                        : "border-[#dedede] bg-white text-[#171717]",
                    )}
                  >
                    {modelOptions.map((model) => (
                      <button
                        key={model.id}
                        type="button"
                        disabled={!model.enabled}
                        title={!model.enabled ? model.description : undefined}
                        onClick={() => {
                          if (!model.enabled) {
                            return;
                          }
                          setSelectedModel(model.id);
                          if (!(modelStatuses[model.id]?.supports_thinking_toggle ?? model.supportsThinkingToggle ?? false)) {
                            setThinkingEnabled(false);
                          }
                          setIsModelMenuOpen(false);
                        }}
                        className={cn(
                          "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-start text-sm transition disabled:cursor-not-allowed disabled:opacity-50",
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
                onClick={() => openAccountModal("personalization")}
                className={cn(
                  "grid h-9 w-9 place-items-center rounded-lg transition",
                  isDark ? "hover:bg-[#2a2a2a]" : "hover:bg-[#ececec]",
                )}
                aria-label="Settings"
              >
                <Settings className="h-5 w-5" />
              </button>
              <div className="relative" data-profile-menu>
                <button
                  type="button"
                  onClick={() => {
                    setIsProfileMenuOpen((value) => !value);
                    setIsSidebarProfileMenuOpen(false);
                  }}
                  className="block h-9 w-9 rounded-full"
                  aria-label="Profile"
                >
                  {renderAvatar("h-9 w-9")}
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
                    {renderProfileMenu()}
                  </div>
                ) : null}
              </div>
            </div>
          </header>

          <div className="relative min-h-0 flex-1">
            <ScrollArea
              ref={chatScrollAreaRef}
              onScroll={handleChatScroll}
              className="h-full min-h-0 overflow-x-hidden"
            >
              <div className="mx-auto flex min-h-full w-full min-w-0 max-w-3xl flex-col px-3 py-6 sm:px-4 md:px-6 md:py-8">
              {!isAuthenticated ? (
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
                            setError(null);
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
                      <div
                        className={cn(
                          "flex min-h-12 items-center gap-3 rounded-xl border px-3 transition",
                          isPhoneInputFocused
                            ? "border-[#10a37f] shadow-[0_0_0_3px_rgba(16,163,127,0.16)]"
                            : isDark
                              ? "border-[#4a4a4a]"
                              : "border-[#dedede]",
                          isDark ? "bg-[#212121] text-[#ececec]" : "bg-white text-[#171717]",
                        )}
                      >
                        <span
                          className={cn(
                            "grid h-9 w-11 shrink-0 select-none place-items-center rounded-lg font-mono text-sm font-semibold",
                            isDark ? "bg-[#303030] text-[#ececec]" : "bg-[#f2f2f2] text-[#171717]",
                          )}
                        >
                          {PHONE_PREFIX}
                        </span>
                        <div className="relative grid min-w-0 flex-1 grid-cols-9 gap-1.5">
                          {phoneDigitSlots.map((digit, index) => {
                            const isCurrentSlot =
                              isPhoneInputFocused &&
                              index === Math.min(phoneRestInput.length, PHONE_REST_LENGTH - 1);

                            return (
                              <span
                                key={index}
                                className={cn(
                                  "grid h-9 min-w-0 place-items-center rounded-md border text-sm font-semibold transition",
                                  digit
                                    ? isDark
                                      ? "border-[#4a4a4a] bg-[#2b2b2b] text-[#ececec]"
                                      : "border-[#d7d7d7] bg-[#fafafa] text-[#171717]"
                                    : isDark
                                      ? "border-[#3a3a3a] bg-[#242424] text-[#6f6f6f]"
                                      : "border-[#e6e6e6] bg-[#f8f8f8] text-[#b0b0b0]",
                                  isCurrentSlot ? "border-[#10a37f]" : "",
                                )}
                              >
                                {digit}
                              </span>
                            );
                          })}
                          <input
                            value={phoneRestInput}
                            onChange={(event) => setPhoneInput(normalizePhoneDraft(event.target.value))}
                            onFocus={() => setIsPhoneInputFocused(true)}
                            onBlur={() => setIsPhoneInputFocused(false)}
                            inputMode="numeric"
                            autoComplete="tel-national"
                            aria-label="Phone number after 09"
                            maxLength={PHONE_LENGTH}
                            className="absolute inset-0 h-full w-full cursor-text bg-transparent text-transparent caret-transparent outline-none"
                          />
                        </div>
                      </div>
                      {!isPhoneNumberValid && phoneRestInput ? (
                        <p className={cn("text-xs", isDark ? "text-[#b4b4b4]" : "text-[#6f6f6f]")}>
                          Enter 9 more digits after 09.
                        </p>
                      ) : null}

                      {isOtpRequested ? (
                        <Input
                          value={otpInput}
                          onChange={(event) => setOtpInput(normalizeOtpDraft(event.target.value))}
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          placeholder="6-digit OTP"
                          maxLength={6}
                          className={cn(
                            "h-11 rounded-lg font-mono",
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
                        disabled={isAuthenticating || !isPhoneNumberValid || (isOtpRequested && !isOtpValid)}
                        className="h-11 w-full rounded-full bg-[#10a37f] text-white hover:bg-[#0d8f6f]"
                      >
                        {isAuthenticating ? "Working..." : isOtpRequested ? "Verify OTP" : "Continue"}
                      </Button>
                    </div>
                  </form>
                </div>
              ) : (
                <div className="flex min-w-0 flex-1 flex-col">
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
                    <div className="min-w-0 space-y-8 pb-36">
                      {activeConversation.messages.map((message) => (
                        <div
                          key={message.id}
                          className={cn(
                            "flex w-full min-w-0",
                            message.role === "user" ? "justify-end" : "justify-start",
                          )}
                        >
                          <div
                            className={cn(
                              "flex min-w-0 w-full flex-col",
                              message.role === "user" ? "items-end" : "items-start",
                            )}
                          >
                            {editingMessageId === message.id && message.role === "user" ? (
                              <div
                                className={cn(
                                  "w-full max-w-[78%] rounded-[28px] px-5 py-4 sm:max-w-[64%] md:max-w-[58%]",
                                  isDark ? "bg-[#303030]" : "bg-[#f4f4f4]",
                                )}
                              >
                                <textarea
                                  value={editDraft}
                                  onChange={(event) => setEditDraft(event.target.value)}
                                  dir="auto"
                                  autoFocus
                                  rows={Math.min(8, Math.max(2, editDraft.split("\n").length))}
                                  className={cn(
                                    "min-h-20 w-full resize-none bg-transparent text-base leading-7 outline-none",
                                    isDark ? "text-[#f4f4f4]" : "text-[#171717]",
                                  )}
                                  onKeyDown={(event) => {
                                    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                                      event.preventDefault();
                                      handleEditSubmit(message);
                                    }
                                    if (event.key === "Escape") {
                                      cancelEditMessage();
                                    }
                                  }}
                                />
                                <div className="mt-4 flex justify-end gap-2">
                                  <button
                                    type="button"
                                    onClick={cancelEditMessage}
                                    className={cn(
                                      "h-10 rounded-full px-4 text-sm font-medium transition",
                                      isDark
                                        ? "bg-black text-white hover:bg-[#171717]"
                                        : "bg-[#e5e5e5] text-[#171717] hover:bg-[#d7d7d7]",
                                    )}
                                  >
                                    Cancel
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleEditSubmit(message)}
                                    disabled={!editDraft.trim() || isSending || !isActiveModelAvailable}
                                    className={cn(
                                      "h-10 rounded-full px-5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
                                      isDark
                                        ? "bg-white text-black hover:bg-[#e7e7e7]"
                                        : "bg-[#171717] text-white hover:bg-[#303030]",
                                    )}
                                  >
                                    Send
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <div
                                dir="auto"
                                className={cn(
                                  "min-w-0 max-w-full text-base leading-7",
                                  message.role === "user"
                                    ? isDark
                                      ? "w-fit max-w-[78%] rounded-3xl bg-[#303030] px-5 py-3 sm:max-w-[64%] md:max-w-[58%]"
                                      : "w-fit max-w-[78%] rounded-3xl bg-[#f4f4f4] px-5 py-3 sm:max-w-[64%] md:max-w-[58%]"
                                    : "w-full",
                                )}
                              >
                                <div className="chat-message-content" dir="auto">
                                {message.content ? (
                                  <ChatMessageRenderer
                                    content={message.content}
                                    thinkingDurationMs={message.thinking_duration_ms}
                                    isStreaming={message.role === "assistant" && message.id < 0}
                                    isDark={isDark}
                                  />
                                ) : null}
                                {message.role === "assistant" && message.id < 0 && !message.content ? (
                                  <span
                                    dir="auto"
                                    className={cn(isDark ? "text-[#a8a8a8]" : "text-[#6f6f6f]")}
                                  >
                                    Thinking...
                                  </span>
                                ) : null}
                                </div>
                              </div>
                            )}
                            {editingMessageId !== message.id ? renderMessageActions(message) : null}
                          </div>
                        </div>
                      ))}
                      <div ref={bottomSentinelRef} className="h-px" />
                    </div>
                  )}
                </div>
              )}
            </div>
            </ScrollArea>

            {showJumpToLatest ? (
              <button
                type="button"
                onClick={handleJumpToLatest}
                className={cn(
                  "absolute bottom-4 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full border px-3 py-2 text-sm shadow-lg transition",
                  isDark
                    ? "border-[#3f3f3f] bg-[#2f2f2f] text-[#f4f4f4] hover:bg-[#3a3a3a]"
                    : "border-[#dedede] bg-white text-[#171717] hover:bg-[#f4f4f4]",
                )}
              >
                <ChevronDown className="h-4 w-4" />
                <span>Jump to latest</span>
              </button>
            ) : null}
          </div>

            {isAuthenticated ? (
              <div className="shrink-0 pb-4">
                <div className="mx-auto w-full max-w-3xl px-3 sm:px-4 md:px-6">
                  <form
                    onSubmit={handleSendMessage}
                    className={cn(
                      "flex w-full items-end gap-2 rounded-[28px] border px-3 py-2 shadow-sm",
                      isDark
                        ? "border-[#3b3b3b] bg-[#303030] text-[#ececec]"
                        : "border-[#d9d9d9] bg-white text-[#171717]",
                    )}
                  >
                    <button
                      type="button"
                      className={cn(
                        "grid h-9 w-9 shrink-0 place-items-center rounded-full transition",
                        isDark ? "text-[#f4f4f4] hover:bg-[#3f3f3f]" : "text-[#2f2f2f] hover:bg-[#f2f2f2]",
                      )}
                      aria-label="Attach file"
                    >
                      <Plus className="h-5 w-5" />
                    </button>
                    <button
                      type="button"
                      disabled={!activeModelSupportsThinkingToggle || isSending}
                      aria-pressed={requestedThinkingEnabled}
                      title={
                        activeModelSupportsThinkingToggle
                          ? "Toggle thinking"
                          : `${activeModel.label} does not support thinking control`
                      }
                      onClick={() => setThinkingEnabled((value) => !value)}
                      className={cn(
                        "flex h-9 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium transition disabled:cursor-not-allowed",
                        requestedThinkingEnabled
                          ? "bg-[#0f766e] text-white hover:bg-[#128a80]"
                          : activeModelSupportsThinkingToggle
                            ? isDark
                              ? "text-[#d7d7d7] hover:bg-[#3f3f3f]"
                              : "text-[#2f2f2f] hover:bg-[#f2f2f2]"
                            : isDark
                              ? "text-[#777777]"
                              : "text-[#a0a0a0]",
                      )}
                    >
                      <Brain className="h-4 w-4" />
                      <span className="hidden sm:inline">Thinking</span>
                    </button>
                    <Textarea
                      ref={draftTextareaRef}
                      value={draft}
                      onChange={(event) => {
                        setDraft(event.target.value);
                        window.requestAnimationFrame(resizeDraftTextarea);
                      }}
                      placeholder="Ask anything"
                      dir="auto"
                      className={cn(
                        "!min-h-9 max-h-44 resize-none overflow-hidden !rounded-none !border-0 !bg-transparent !px-0 !py-2 text-base leading-6 !shadow-none outline-none focus-visible:!ring-0",
                        isDark
                          ? "!text-[#f4f4f4] !placeholder:text-[#c5c5c5]"
                          : "!text-[#171717] !placeholder:text-[#6b6b6b]",
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
                      type="submit"
                      disabled={!canSubmitDraft && !isSending}
                      onClick={(event) => {
                        if (isSending) {
                          event.preventDefault();
                          streamAbortControllerRef.current?.abort();
                        }
                      }}
                      className={cn(
                        "grid h-9 w-9 shrink-0 place-items-center rounded-full transition disabled:cursor-not-allowed",
                        canSubmitDraft || isSending
                          ? "bg-[#4668d9] text-white hover:bg-[#5577ea]"
                          : isDark
                            ? "bg-[#424242] text-[#a8a8a8]"
                            : "bg-[#d7d7d7] text-[#777777]",
                      )}
                      aria-label={isSending ? "Stop response" : "Send message"}
                    >
                      {isSending ? <X className="h-5 w-5" /> : <ArrowUp className="h-5 w-5" />}
                    </button>
                  </form>
                  <div
                    className={cn(
                      "mt-2 w-full text-center text-xs leading-4",
                      isDark ? "text-[#d1d1d1]" : "text-[#5f5f5f]",
                    )}
                  >
                    GPTClone can make mistakes. Check important info. See Cookie Preferences.
                  </div>
                </div>
              </div>
            ) : null}

          {error ? (
            <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-full bg-[#ef4444] px-4 py-2 text-sm text-white shadow-lg">
              {error}
            </div>
          ) : null}
        </SidebarInset>
        {renderAccountModal()}
        {renderEditWarningModal()}
    </SidebarProvider>
  );
}
