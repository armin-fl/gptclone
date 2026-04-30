"use client";

import { isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

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

export function ChatMessageRenderer({ content }: { content: string }) {
  return (
    <div dir="auto" className="chat-message">
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
