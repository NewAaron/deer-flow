import { buildCvciLogoutUrl } from "@/core/auth/cvci-logout-url";

export const dynamic = "force-dynamic";

const responseHeaders = {
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
};

const unavailablePage = `<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>登录入口</title></head>
<body style="margin:0;background:#f0f6ff;color:#23354b;font-family:system-ui,sans-serif">
<main style="max-width:34rem;margin:15vh auto;padding:2rem;background:white;border-radius:1rem">
<p>暂时无法打开CVCI登录页，请重试或联系管理员检查登录入口。</p>
<a href="/auth/cvci-logout">重试</a>
</main></body></html>`;

export function GET(request: Request): Response {
  const target = buildCvciLogoutUrl(
    process.env.CVCI_LOGIN_ORIGIN,
    request.url,
    request.headers.get("host"),
  );
  if (target) {
    return new Response(null, {
      status: 302,
      headers: { ...responseHeaders, Location: target },
    });
  }
  return new Response(unavailablePage, {
    status: 503,
    headers: {
      ...responseHeaders,
      "Content-Type": "text/html; charset=utf-8",
    },
  });
}
