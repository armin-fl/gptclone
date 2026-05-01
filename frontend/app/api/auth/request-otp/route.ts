import { proxyDjango, readRequestBody } from "@/lib/server/bff";

export async function POST(request: Request) {
  return proxyDjango(request, "/api/auth/request-otp/", {
    body: await readRequestBody(request),
    csrf: true,
  });
}
