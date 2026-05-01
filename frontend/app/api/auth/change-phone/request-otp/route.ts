import { proxyDjango, readRequestBody } from "@/lib/server/bff";

export async function POST(request: Request) {
  return proxyDjango(request, "/api/auth/change-phone/request-otp/", {
    auth: true,
    body: await readRequestBody(request),
  });
}
