import { revalidateTag } from "next/cache";
import { NextRequest } from "next/server";

import { djangoPath, proxyDjango, readRequestBody } from "@/lib/server/bff";

function conversationPath(request: NextRequest) {
  const url = new URL(request.url);
  const prefix = "/api/conversations";
  const rawPath = url.pathname.startsWith(`${prefix}/`) ? url.pathname.slice(prefix.length + 1) : "";
  const path = rawPath ? `${rawPath.replace(/\/+$/, "")}/` : "";
  return djangoPath(`/api/conversations/${path}`, url.search);
}

function revalidateConversationTags() {
  revalidateTag("chat:conversations", "max");
}

export async function GET(request: NextRequest) {
  return proxyDjango(request, conversationPath(request), { auth: true, csrf: false });
}

export async function POST(request: NextRequest) {
  const response = await proxyDjango(request, conversationPath(request), {
    auth: true,
    body: await readRequestBody(request),
  });
  revalidateConversationTags();
  return response;
}

export async function PATCH(request: NextRequest) {
  const response = await proxyDjango(request, conversationPath(request), {
    auth: true,
    body: await readRequestBody(request),
  });
  revalidateConversationTags();
  return response;
}

export async function DELETE(request: NextRequest) {
  const response = await proxyDjango(request, conversationPath(request), {
    auth: true,
    body: await readRequestBody(request),
  });
  revalidateConversationTags();
  return response;
}
