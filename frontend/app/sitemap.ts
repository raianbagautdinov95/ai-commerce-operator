import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    { url: "https://aicommerceoperator.com/", lastModified: now, changeFrequency: "weekly", priority: 1 },
    { url: "https://app.aicommerceoperator.com/try", lastModified: now, changeFrequency: "weekly", priority: 0.9 },
    { url: "https://app.aicommerceoperator.com/pricing", lastModified: now, changeFrequency: "monthly", priority: 0.6 },
    { url: "https://app.aicommerceoperator.com/privacy", lastModified: now, changeFrequency: "yearly", priority: 0.2 },
    { url: "https://app.aicommerceoperator.com/terms", lastModified: now, changeFrequency: "yearly", priority: 0.2 },
  ];
}
