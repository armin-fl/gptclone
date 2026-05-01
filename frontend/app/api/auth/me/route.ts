import { proxyDjango, readRequestBody } from "@/lib/server/bff";

export async function GET(request: Request) {
  return proxyDjango(request, "/api/auth/me/", { auth: true, csrf: false });
}

export async function PATCH(request: Request) {
  return proxyDjango(request, "/api/auth/me/", {
    auth: true,
    body: await readRequestBody(request),
  });
}
