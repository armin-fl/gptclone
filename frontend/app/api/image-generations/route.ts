import { NextRequest } from "next/server";

import { djangoPath, proxyDjango, readRequestBody } from "@/lib/server/bff";

export async function POST(request: NextRequest) {
  const url = new URL(request.url);
  return proxyDjango(request, djangoPath("/api/images/generations/", url.search), {
    auth: true,
    body: await readRequestBody(request),
  });
}
