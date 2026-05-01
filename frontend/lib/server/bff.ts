import { randomBytes } from "crypto";

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  ACCESS_COOKIE,
  ACCESS_MAX_AGE_SECONDS,
  CSRF_COOKIE,
  REFRESH_COOKIE,
  REFRESH_MAX_AGE_SECONDS,
} from "@/lib/auth-cookies";

const DJANGO_API_BASE_URL = process.env.DJANGO_API_BASE_URL ?? "http://127.0.0.1:8000";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

interface TokenRefreshResponse {
  access: string;
  refresh?: string;
}

interface ProxyOptions {
  auth?: boolean;
  csrf?: boolean;
  body?: BodyInit | null;
  headers?: HeadersInit;
}

export function newCsrfToken() {
  return randomBytes(24).toString("base64url");
}

export function cookieOptions(maxAge: number, httpOnly = true) {
  return {
    httpOnly,
    maxAge,
    path: "/",
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
  };
}

export function clearAuthCookies(response: NextResponse) {
  response.cookies.set(ACCESS_COOKIE, "", { ...cookieOptions(0), maxAge: 0 });
  response.cookies.set(REFRESH_COOKIE, "", { ...cookieOptions(0), maxAge: 0 });
}

export function setAuthCookies(response: NextResponse, access: string, refresh?: string) {
  response.cookies.set(ACCESS_COOKIE, access, cookieOptions(ACCESS_MAX_AGE_SECONDS));
  if (refresh) {
    response.cookies.set(REFRESH_COOKIE, refresh, cookieOptions(REFRESH_MAX_AGE_SECONDS));
  }
}

export function setCsrfCookie(response: NextResponse, token: string) {
  response.cookies.set(CSRF_COOKIE, token, cookieOptions(REFRESH_MAX_AGE_SECONDS, false));
}

export async function ensureCsrfResponse(payload: Record<string, unknown> = {}) {
  const cookieStore = await cookies();
  const csrf = cookieStore.get(CSRF_COOKIE)?.value ?? newCsrfToken();
  const response = NextResponse.json({ ...payload, csrf_token: csrf });
  setCsrfCookie(response, csrf);
  return response;
}

export async function requireCsrf(request: Request) {
  if (!UNSAFE_METHODS.has(request.method)) {
    return null;
  }

  const cookieStore = await cookies();
  const expected = cookieStore.get(CSRF_COOKIE)?.value;
  const actual = request.headers.get("x-csrf-token");
  if (!expected || !actual || expected !== actual) {
    return NextResponse.json({ detail: "Invalid CSRF token." }, { status: 403 });
  }
  return null;
}

async function refreshAccessToken() {
  const cookieStore = await cookies();
  const refresh = cookieStore.get(REFRESH_COOKIE)?.value;
  if (!refresh) {
    return null;
  }

  const response = await fetch(`${DJANGO_API_BASE_URL}/api/auth/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
    cache: "no-store",
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as TokenRefreshResponse;
}

async function djangoFetch(path: string, request: Request, access: string | null, options: ProxyOptions) {
  const headers = new Headers(options.headers ?? {});
  const contentType = request.headers.get("content-type");
  if (contentType && !headers.has("Content-Type")) {
    headers.set("Content-Type", contentType);
  }
  if (access) {
    headers.set("Authorization", `Bearer ${access}`);
  }

  return fetch(`${DJANGO_API_BASE_URL}${path}`, {
    method: request.method,
    headers,
    body: options.body,
    cache: "no-store",
    redirect: "manual",
  });
}

export async function proxyDjango(request: Request, path: string, options: ProxyOptions = {}) {
  if (options.csrf ?? UNSAFE_METHODS.has(request.method)) {
    const csrfError = await requireCsrf(request);
    if (csrfError) {
      return csrfError;
    }
  }

  const cookieStore = await cookies();
  let access = options.auth ? cookieStore.get(ACCESS_COOKIE)?.value ?? null : null;
  let refreshed: TokenRefreshResponse | null = null;
  if (options.auth && !access) {
    refreshed = await refreshAccessToken();
    if (!refreshed) {
      return NextResponse.json({ detail: "Authentication credentials were not provided." }, { status: 401 });
    }
    access = refreshed.access;
  }

  let response = await djangoFetch(path, request, access, options);
  if (options.auth && response.status === 401) {
    refreshed = await refreshAccessToken();
    if (!refreshed) {
      const nextResponse = NextResponse.json({ detail: "Authentication expired." }, { status: 401 });
      clearAuthCookies(nextResponse);
      return nextResponse;
    }
    access = refreshed.access;
    response = await djangoFetch(path, request, access, options);
  }

  const responseHeaders = new Headers(response.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  const proxied = new NextResponse(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
  if (refreshed) {
    setAuthCookies(proxied, refreshed.access, refreshed.refresh);
  }
  return proxied;
}

export async function readRequestBody(request: Request) {
  if (request.method === "GET" || request.method === "HEAD") {
    return null;
  }
  return request.arrayBuffer();
}

export function djangoPath(pathname: string, search: string) {
  return `${pathname}${search}`;
}
