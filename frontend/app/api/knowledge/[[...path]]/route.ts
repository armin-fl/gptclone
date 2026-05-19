import { NextRequest } from "next/server";

import { djangoPath, proxyDjango, readRequestBody } from "@/lib/server/bff";

function knowledgePath(request: NextRequest) {
  const url = new URL(request.url);
  const prefix = "/api/knowledge";
  const rawPath = url.pathname.startsWith(`${prefix}/`) ? url.pathname.slice(prefix.length + 1) : "";
  const path = rawPath ? `${rawPath.replace(/\/+$/, "")}/` : "";
  return djangoPath(`/api/knowledge/${path}`, url.search);
}

export async function GET(request: NextRequest) {
  return proxyDjango(request, knowledgePath(request), { auth: true, csrf: false });
}

export async function POST(request: NextRequest) {
  return proxyDjango(request, knowledgePath(request), {
    auth: true,
    body: await readRequestBody(request),
  });
}

export async function DELETE(request: NextRequest) {
  return proxyDjango(request, knowledgePath(request), {
    auth: true,
    body: await readRequestBody(request),
  });
}
