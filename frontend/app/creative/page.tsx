"use client";

import { useEffect, useState } from "react";
import { generateCreative, type CreativeResult } from "../../lib/api";
import { Icon, IconPlate } from "../icons";

/* The one screen where the model is the product rather than the narrator, and
   the one place a picture can quietly become a lie: a generated concept is not
   a photograph of anything, and Amazon requires a photograph of the actual
   item. That warning sits above the images, not under them. */

export default function Creative() {
  const [name, setName] = useState("");
  const [features, setFeatures] = useState("");
  const [withImages, setWithImages] = useState(true);
  const [result, setResult] = useState<CreativeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const pre = localStorage.getItem("creative_prefill");
    if (pre) {
      localStorage.removeItem("creative_prefill");
      setName(pre);
    }
  }, []);

  async function onGenerate() {
    if (!name.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await generateCreative({ name, features: features || null, images: withImages }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="px-6 pb-20 lg:px-11">
      <section className="pb-9 pt-11">
        <div className="flex items-center gap-3">
          <IconPlate name="image" />
          <p className="lbl">Creative</p>
        </div>
        <h1 style={{ margin: "16px 0 0", fontSize: "34px", fontWeight: 600, letterSpacing: "-.025em" }}>
          Make somebody stop and want it
        </h1>
        <p style={{ margin: "16px 0 0", maxWidth: "70ch", fontSize: "15px", lineHeight: 1.65, color: "var(--ink-2)" }}>
          A persuasive listing, a shot-list your photographer can work from, and concept images
          to show them what you mean. This is the one screen where the model writes the product
          rather than explaining it.
        </p>

        <div className="mt-8 max-w-3xl space-y-4">
          <label style={{ display: "block" }}>
            <span className="lbl">Product name</span>
            <input className="field" style={{ marginTop: "7px" }}
                   value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="Silicone baking molds" />
          </label>
          <label style={{ display: "block" }}>
            <span className="lbl">Key features or materials — optional, but it makes the copy better</span>
            <input className="field" style={{ marginTop: "7px" }}
                   value={features} onChange={(e) => setFeatures(e.target.value)}
                   placeholder="food-grade silicone, oven safe to 230°C, six cavities" />
          </label>
          <div className="flex flex-wrap items-center gap-5">
            <button onClick={onGenerate} disabled={loading || !name.trim()}
                    className="btn-primary inline-flex items-center gap-2">
              <Icon name="spark" size={13} />
              {loading ? "CREATING… IMAGES TAKE 20-40s" : "GENERATE"}
            </button>
            <label className="flex cursor-pointer items-center gap-2.5">
              <input type="checkbox" checked={withImages}
                     onChange={(e) => setWithImages(e.target.checked)}
                     style={{ accentColor: "#4ADE80", width: "15px", height: "15px" }} />
              <span style={{ fontSize: "13px", color: "var(--ink-2)" }}>Also generate concept images</span>
            </label>
          </div>
        </div>
      </section>

      {error && (
        <p className="card-unproven flex items-start gap-3 px-5 py-4"
           style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                    fontSize: "13.5px", color: "var(--unproven)" }}>
          <Icon name="alert" size={17} className="mt-0.5 shrink-0" /> {error}
        </p>
      )}

      {result && (
        <div className="space-y-5">
          <Panel icon="tag" title="Listing copy">
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: "13.5px", lineHeight: 1.75,
                          color: "var(--ink-2)", fontFamily: "inherit" }}>
              {result.listing}
            </pre>
          </Panel>

          <Panel icon="list" title="Photo shot-list">
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: "13.5px", lineHeight: 1.75,
                          color: "var(--ink-2)", fontFamily: "inherit" }}>
              {result.image_plan}
            </pre>
          </Panel>

          <Panel icon="image" title="Concept images">
            {result.concept_images.length > 0 ? (
              <>
                <p className="card-unproven flex items-start gap-3 px-4 py-3"
                   style={{ borderWidth: "1px", borderStyle: "solid", borderRadius: "var(--r)",
                            fontSize: "13px", lineHeight: 1.65, color: "var(--ink-2)" }}>
                  <Icon name="alert" size={16} stroke="var(--unproven)" className="mt-0.5 shrink-0" />
                  <span>
                    <strong style={{ color: "var(--ink)" }}>These are not photographs of your product.</strong>{" "}
                    They are concepts, for showing a photographer what you want. Amazon requires
                    real images of the actual item, and listing one of these would be grounds to
                    take the listing down.
                  </span>
                </p>
                <div className="mt-5 grid gap-4"
                     style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
                  {result.concept_images.map((src, i) => (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img key={i} src={src} alt={`Concept ${i + 1}`}
                         style={{ width: "100%", borderRadius: "var(--r)", border: "1px solid var(--line)" }} />
                  ))}
                </div>
              </>
            ) : (
              <p style={{ margin: 0, fontSize: "13.5px", lineHeight: 1.7, color: "var(--ink-5)" }}>
                No images were generated. Set <span className="num">OPENAI_API_KEY</span> on the
                server, or untick the box to skip them.
              </p>
            )}
          </Panel>
        </div>
      )}
    </main>
  );
}

function Panel({ icon, title, children }: {
  icon: "tag" | "list" | "image"; title: string; children: React.ReactNode;
}) {
  return (
    <section className="card p-6 sm:p-8">
      <div className="flex items-center gap-3">
        <IconPlate name={icon} size={34} />
        <h2 className="lbl" style={{ margin: 0 }}>{title}</h2>
      </div>
      <div className="mt-6">{children}</div>
    </section>
  );
}
