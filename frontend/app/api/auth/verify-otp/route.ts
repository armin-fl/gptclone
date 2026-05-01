import { NextResponse } from "next/server";

import { proxyDjango, readRequestBody, setAuthCookies } from "@/lib/server/bff";

export async function POST(request: Request) {
  const djangoResponse = await proxyDjango(request, "/api/auth/verify-otp/", {
    body: await readRequestBody(request),
    csrf: true,
  });

  if (!djangoResponse.ok) {
    return djangoResponse;
  }

  const payload = await djangoResponse.json();
  const response = NextResponse.json({
    token_type: payload.token_type,
    access_expires_at: payload.access_expires_at,
    refresh_expires_at: payload.refresh_expires_at,
    user: payload.user,
  });
  setAuthCookies(response, payload.access, payload.refresh);
  return response;
}
