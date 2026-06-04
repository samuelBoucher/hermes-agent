(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!SDK || !registry) return;

  const ns = window.UkVillaTrip = window.UkVillaTrip || {};
  const React = SDK.React;
  const h = React.createElement;
  const { useEffect, useState } = SDK.hooks;
  const SCRIPT_ORDER = ["data.js", "leaflet-loader.js", "components.js"];

  const entryUrl = (document.currentScript && document.currentScript.src)
    || "/dashboard-plugins/uk-villa-trip/dist/index.js";
  const distBaseUrl = new URL(".", entryUrl).toString();

  function scriptId(fileName) {
    return "uk-villa-trip-" + fileName.replace(/[^a-z0-9]/gi, "-");
  }

  function loadScript(fileName) {
    const id = scriptId(fileName);
    const existing = document.getElementById(id);
    if (existing) {
      if (existing.dataset.loaded === "true") return Promise.resolve();
      return new Promise((resolve, reject) => {
        existing.addEventListener("load", resolve, { once: true });
        existing.addEventListener("error", reject, { once: true });
      });
    }

    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.id = id;
      script.src = new URL(fileName, distBaseUrl).toString();
      script.async = false;
      script.onload = () => {
        script.dataset.loaded = "true";
        resolve();
      };
      script.onerror = () => reject(new Error("Module UK Villa introuvable: " + fileName));
      document.body.appendChild(script);
    });
  }

  function loadModules() {
    if (!ns.ready) {
      ns.ready = SCRIPT_ORDER.reduce((chain, fileName) => {
        return chain.then(() => loadScript(fileName));
      }, Promise.resolve()).then(() => {
        if (!ns.DATA || !ns.LeafletAssetLoader || !ns.UkVillaTripComponents) {
          throw new Error("Assemblage UK Villa incomplet après chargement des modules.");
        }
        const leafletLoader = new ns.LeafletAssetLoader();
        return ns.UkVillaTripComponents.create({
          SDK,
          data: ns.DATA,
          leafletLoader,
        }).UkVillaTripPage;
      });
    }
    return ns.ready;
  }

  function UkVillaTripBootstrap() {
    const [Page, setPage] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      let cancelled = false;
      loadModules()
        .then((ResolvedPage) => {
          if (!cancelled) setPage(() => ResolvedPage);
        })
        .catch((err) => {
          if (!cancelled) setError(err.message || "Le plugin UK Villa n’a pas chargé.");
        });
      return () => { cancelled = true; };
    }, []);

    if (error) {
      return h("div", { className: "uk-villa-trip uvt-shell" },
        h("section", { className: "uvt-card uvt-pad" },
          h("h1", null, "Voyage UK 2026"),
          h("p", null, error),
        ),
      );
    }
    if (!Page) {
      return h("div", { className: "uk-villa-trip uvt-shell" },
        h("section", { className: "uvt-card uvt-pad" }, "Chargement du plan UK Villa…"),
      );
    }
    return h(Page);
  }

  registry.register("uk-villa-trip", UkVillaTripBootstrap);
})();
