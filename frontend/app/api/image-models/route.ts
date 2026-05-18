import { NextRequest } from "next/server";

import { djangoPath, proxyDjango } from "@/lib/server/bff";

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  return proxyDjango(request, djangoPath("/api/image-models/", url.search), {
    auth: true,
    csrf: false,
  });
}
