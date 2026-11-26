const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1"]);

function requestLoopbackHost(
  requestUrl: string,
  hostHeader: string | null | undefined,
): string | null {
  if (hostHeader != null) {
    if (/[\u0000-\u0020\u007f]/.test(hostHeader)) return null;
    const match = /^(localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?$/i.exec(
      hostHeader,
    );
    if (!match) return null;
    if (match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535)) {
      return null;
    }
    return match[1]!.toLowerCase();
  }
  try {
    const hostname = new URL(requestUrl).hostname;
    return LOOPBACK_HOSTS.has(hostname) ? hostname : null;
  } catch {
    return null;
  }
}

/** Resolve only an operator-owned origin; browser query parameters are never targets. */
export function buildCvciLogoutUrl(
  configuredOrigin: string | undefined,
  requestUrl: string,
  hostHeader?: string | null,
): string | null {
  if (!configuredOrigin || /[\u0000-\u0020\u007f\\]/.test(configuredOrigin)) {
    return null;
  }
  // Check the raw shape before URL normalization can remove paths or empty queries.
  const originMatch = /^(https?):\/\/([^/?#\\]+)\/?$/i.exec(configuredOrigin);
  if (!originMatch || originMatch[2]!.includes("@")) return null;

  let target: URL;
  try {
    target = new URL(configuredOrigin);
  } catch {
    return null;
  }
  if (target.port === "0") return null;
  if (
    target.protocol === "http:" &&
    !/^(localhost|127\.0\.0\.1):[0-9]+$/i.test(originMatch[2]!)
  ) {
    return null;
  }

  if (LOOPBACK_HOSTS.has(target.hostname)) {
    // Next may expose an internal localhost request URL behind the frontend proxy.
    // Only the actual, strictly allowlisted Host may override that URL; never XFH.
    const requestHost = requestLoopbackHost(requestUrl, hostHeader);
    if (requestHost) target.hostname = requestHost;
  }
  target.pathname = "/logout/complete";
  target.search = "";
  return target.toString();
}
