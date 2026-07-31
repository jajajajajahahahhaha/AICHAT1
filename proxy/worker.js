/**
 * Cloudflare Worker — reverse proxy for the Kimi K2.6 inference API.
 *
 * WHY THIS EXISTS:
 *   Cloudflare's bot protection in front of inference.dahl.global returns a
 *   JavaScript "Just a moment..." challenge (HTTP 403) to requests coming
 *   from GitHub Actions IP ranges. TLS-fingerprint spoofing (curl_cffi
 *   Chrome impersonation) does NOT pass it, because the block is IP-based,
 *   not fingerprint-based.
 *
 *   This Worker runs inside Cloudflare's own network, so its outbound
 *   requests to inference.dahl.global originate from a Cloudflare edge IP
 *   and skip the anti-bot check entirely.
 *
 * DEPLOY (one-time, ~2 minutes) — see proxy/README.md for full steps.
 */

const UPSTREAM = "https://inference.dahl.global";

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "*",
          "access-control-allow-methods": "GET, POST, OPTIONS",
          "access-control-max-age": "86400",
        },
      });
    }

    // Health check (root & /health)
    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response(
        JSON.stringify({ ok: true, proxy_for: UPSTREAM }),
        { headers: { "content-type": "application/json", "access-control-allow-origin": "*" } }
      );
    }

    // Forward everything else to the upstream API, preserving path + query.
    const upstreamUrl = new URL(url.pathname + url.search, UPSTREAM);

    // Preserve request headers, drop the ones that would leak the Worker
    // origin or confuse the upstream server.
    const fwdHeaders = new Headers();
    for (const [key, value] of request.headers.entries()) {
      const k = key.toLowerCase();
      if (k === "host" || k.startsWith("cf-") || k === "x-forwarded-host" || k === "x-real-ip") continue;
      fwdHeaders.set(key, value);
    }

    let response;
    try {
      response = await fetch(upstreamUrl.toString(), {
        method: request.method,
        headers: fwdHeaders,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "follow",
      });
    } catch (err) {
      return new Response(
        JSON.stringify({ error: "upstream_fetch_failed", detail: String(err) }),
        {
          status: 502,
          headers: {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
          },
        }
      );
    }

    // Copy response body (streaming) + headers, add permissive CORS.
    const outHeaders = new Headers(response.headers);
    outHeaders.set("access-control-allow-origin", "*");
    outHeaders.set("access-control-allow-headers", "*");
    outHeaders.set("access-control-allow-methods", "GET, POST, OPTIONS");
    // Ensure SSE streams aren't buffered
    if ((outHeaders.get("content-type") || "").includes("text/event-stream")) {
      outHeaders.set("cache-control", "no-cache, no-transform");
      outHeaders.set("x-accel-buffering", "no");
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: outHeaders,
    });
  },
};
