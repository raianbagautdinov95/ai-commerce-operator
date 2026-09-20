import { NextResponse, type NextRequest } from "next/server";

/* ---------------------------------------------------------------------------
   Two hostnames, one deployment.

   aicommerceoperator.com is the front door: somebody arriving from a search
   or an ad gets the landing page with the calculator on it. app.… is the
   product. Serving both from one Next build keeps one codebase, one deploy
   and one set of styles; this file is the only place the difference lives.

   On the bare domain, "/" shows the landing page and every other path is sent
   to the app host, so a bookmark to /dashboard or a link to /pricing keeps
   working wherever it was typed. www is folded into the bare domain so the
   search engine sees one address.
   --------------------------------------------------------------------------- */

export const ROOT_HOSTS = new Set(["aicommerceoperator.com", "www.aicommerceoperator.com"]);
export const APP_ORIGIN = "https://app.aicommerceoperator.com";
export const CANONICAL_ORIGIN = "https://aicommerceoperator.com";

export function route(url: URL, host: string): NextResponse {
  const hostname = host.split(":")[0].toLowerCase();
  if (!ROOT_HOSTS.has(hostname)) return NextResponse.next();

  if (hostname.startsWith("www.")) {
    return NextResponse.redirect(new URL(url.pathname + url.search, CANONICAL_ORIGIN), 308);
  }
  if (url.pathname === "/") {
    const landing = new URL(url.toString());
    landing.pathname = "/home";
    return NextResponse.rewrite(landing);
  }
  if (url.pathname === "/home") return NextResponse.next();
  return NextResponse.redirect(new URL(url.pathname + url.search, APP_ORIGIN), 307);
}

export function middleware(request: NextRequest) {
  return route(request.nextUrl, request.headers.get("host") ?? "");
}

export const config = {
  // Everything except Next's own assets and the files in /public.
  matcher: ["/((?!_next/|favicon.ico|robots.txt|sitemap.xml).*)"],
};
