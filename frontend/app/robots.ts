import type { MetadataRoute } from "next";

// One sitemap, on the canonical host. The app host carries the same routes,
// but a search engine should learn them from the front door.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: ["/", "/home", "/try", "/pricing", "/privacy", "/terms"],
              disallow: ["/api/", "/dashboard", "/signin", "/billing", "/integrations/"] }],
    sitemap: "https://aicommerceoperator.com/sitemap.xml",
  };
}
