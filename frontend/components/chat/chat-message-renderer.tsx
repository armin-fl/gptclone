"use client";

import { isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { formatThinkingDuration, splitThinkingBlocks } from "@/lib/thinking";
import { cn } from "@/lib/utils";

function getTextContent(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }

  if (Array.isArray(node)) {
    return node.map(getTextContent).join("");
  }

  if (isValidElement<{ children?: ReactNode }>(node)) {
    return getTextContent(node.props.children);
  }

  return "";
}

function isUrlText(value: string): boolean {
  return /^(https?:\/\/|www\.)\S+$/i.test(value.trim());
}

function withoutMarkdownNode<T extends { node?: unknown }>(props: T): Omit<T, "node"> {
  const { node, ...rest } = props;
  void node;
  return rest;
}

const markdownComponents: Components = {
  a(componentProps) {
    const { children, className, ...props } = withoutMarkdownNode(componentProps);
    const label = getTextContent(children);
    const isUrl = isUrlText(label);

    return (
      <a
        {...props}
        dir={isUrl ? "ltr" : "auto"}
        target="_blank"
        rel="noreferrer"
        className={cn("chat-message-link", isUrl ? "chat-message-ltr" : "", className)}
      >
        {children}
      </a>
    );
  },
  blockquote(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <blockquote {...props} dir="auto" className={cn("chat-message-blockquote", className)} />;
  },
  code(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <code {...props} dir="ltr" className={cn("chat-message-code", className)} />;
  },
  h1(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <h1 {...props} dir="auto" className={cn("chat-message-heading", className)} />;
  },
  h2(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <h2 {...props} dir="auto" className={cn("chat-message-heading", className)} />;
  },
  h3(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <h3 {...props} dir="auto" className={cn("chat-message-heading", className)} />;
  },
  h4(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <h4 {...props} dir="auto" className={cn("chat-message-heading", className)} />;
  },
  h5(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <h5 {...props} dir="auto" className={cn("chat-message-heading", className)} />;
  },
  h6(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <h6 {...props} dir="auto" className={cn("chat-message-heading", className)} />;
  },
  li(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <li {...props} dir="auto" className={cn("chat-message-list-item", className)} />;
  },
  ol(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <ol {...props} dir="auto" className={cn("chat-message-list chat-message-ordered-list", className)} />;
  },
  p(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <p {...props} dir="auto" className={cn("chat-message-paragraph", className)} />;
  },
  pre(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <pre {...props} dir="ltr" className={cn("chat-message-pre", className)} />;
  },
  table(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return (
      <div dir="auto" className="chat-message-table-wrapper">
        <table {...props} dir="auto" className={cn("chat-message-table", className)} />
      </div>
    );
  },
  td(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <td {...props} dir="auto" className={cn("chat-message-table-cell", className)} />;
  },
  th(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <th {...props} dir="auto" className={cn("chat-message-table-header", className)} />;
  },
  ul(componentProps) {
    const { className, ...props } = withoutMarkdownNode(componentProps);
    return <ul {...props} dir="auto" className={cn("chat-message-list", className)} />;
  },
};

function MarkdownContent({ content, className }: { content: string; className?: string }) {
  return (
    <div dir="auto" className={cn("chat-message", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false, throwOnError: false }]]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

interface ChatMessageRendererProps {
  content: string;
  thinkingDurationMs?: number | null;
  isStreaming?: boolean;
  isDark?: boolean;
}

export function ChatMessageRenderer({
  content,
  thinkingDurationMs = null,
  isStreaming = false,
  isDark = false,
}: ChatMessageRendererProps) {
  const segments = splitThinkingBlocks(content);
  const hasThinking = segments.some((segment) => segment.type === "thinking");

  if (!hasThinking) {
    return <MarkdownContent content={content} />;
  }

  return (
    <div dir="auto" className="space-y-3">
      {segments.map((segment, index) => {
        if (segment.type === "answer") {
          return <MarkdownContent key={`${segment.type}-${index}`} content={segment.content} />;
        }

        if (segment.complete && !segment.content.trim() && thinkingDurationMs === null) {
          return null;
        }

        const isLiveThinking = isStreaming && !segment.complete;
        return (
          <details
            key={`${segment.type}-${index}`}
            className={cn(
              "group rounded-lg border px-3 py-2",
              isDark
                ? "border-[#25564d] bg-[#10211f]/70 text-[#8bd8ca]"
                : "border-[#acd9d1] bg-[#eefaf7] text-[#0f766e]",
            )}
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium [&::-webkit-details-marker]:hidden">
              <span
                className={cn(
                  "inline-flex min-h-6 items-center rounded-full px-2.5",
                  isDark ? "bg-[#16352f] text-[#a7f3d0]" : "bg-[#d8f3ec] text-[#0f766e]",
                )}
              >
                {isLiveThinking ? "Thinking..." : formatThinkingDuration(thinkingDurationMs)}
              </span>
              <span className={cn("text-xs", isDark ? "text-[#6fd1c2]" : "text-[#2d8f83]")}>
                <span className="group-open:hidden">Show</span>
                <span className="hidden group-open:inline">Hide</span>
              </span>
            </summary>
            {segment.content.trim() ? (
              <MarkdownContent
                content={segment.content}
                className={cn(
                  "chat-message-thinking-body mt-2 border-t pt-2 text-sm leading-6",
                  isDark
                    ? "border-[#25564d] text-[#a7c7c1]"
                    : "border-[#acd9d1] text-[#3c6f68]",
                )}
              />
            ) : null}
          </details>
        );
      })}
    </div>
  );
}
