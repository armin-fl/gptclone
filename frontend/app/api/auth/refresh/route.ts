import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { REFRESH_COOKIE } from "@/lib/auth-cookies";
import { proxyDjango, setAuthCookies } from "@/lib/server/bff";

export async function POST(request: Request) {
  const refresh = (await cookies()).get(REFRESH_COOKIE)?.value;
  if (!refresh) {
    return NextResponse.json({ detail: "Authentication expired." }, { status: 401 });
  }

  const djangoResponse = await proxyDjango(request, "/api/auth/refresh/", {
    body: JSON.stringify({ refresh }),
    headers: { "Content-Type": "application/json" },
    csrf: true,
  });

  if (!djangoResponse.ok) {
    return djangoResponse;
  }

  const payload = await djangoResponse.json();
  const response = NextResponse.json({ access: "cookie", refresh: payload.refresh ? "cookie" : undefined });
  setAuthCookies(response, payload.access, payload.refresh);
  return response;
}
