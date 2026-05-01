import { NextResponse } from "next/server";

import { ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE } from "@/lib/auth-cookies";
import { ensureCsrfResponse, proxyDjango } from "@/lib/server/bff";

export async function GET(request: Request) {
  const response = await proxyDjango(request, "/api/auth/me/", { auth: true, csrf: false });
  if (!response.ok) {
    return ensureCsrfResponse({ user: null, authenticated: false });
  }
  const user = await response.json();
  return ensureCsrfResponse({ user, authenticated: true });
}

export async function DELETE() {
  const response = NextResponse.json({ detail: "Signed out." });
  response.cookies.delete(ACCESS_COOKIE);
  response.cookies.delete(REFRESH_COOKIE);
  response.cookies.delete(CSRF_COOKIE);
  return response;
}
