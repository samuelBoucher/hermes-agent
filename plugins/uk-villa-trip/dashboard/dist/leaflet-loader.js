(function () {
  "use strict";

  const ns = window.UkVillaTrip = window.UkVillaTrip || {};

  class HtmlEscaper {
    static escape(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }
  }

  class LeafletAssetLoader {
    constructor(options) {
      const config = options || {};
      this.cssId = config.cssId || "uk-villa-trip-leaflet-css";
      this.jsId = config.jsId || "uk-villa-trip-leaflet-js";
      this.cssUrl = config.cssUrl || "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      this.jsUrl = config.jsUrl || "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      this.cssIntegrity = config.cssIntegrity || "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=";
      this.jsIntegrity = config.jsIntegrity || "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";
    }

    ensure() {
      this.ensureCss();
      return this.ensureScript();
    }

    ensureCss() {
      if (document.getElementById(this.cssId)) return;

      const link = document.createElement("link");
      link.id = this.cssId;
      link.rel = "stylesheet";
      link.href = this.cssUrl;
      link.integrity = this.cssIntegrity;
      link.crossOrigin = "";
      document.head.appendChild(link);
    }

    ensureScript() {
      if (window.L) return Promise.resolve(window.L);

      const existing = document.getElementById(this.jsId);
      if (existing) return this.waitForExistingScript(existing);

      return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.id = this.jsId;
        script.src = this.jsUrl;
        script.integrity = this.jsIntegrity;
        script.crossOrigin = "";
        script.async = true;
        script.onload = () => {
          script.dataset.loaded = "true";
          resolve(window.L);
        };
        script.onerror = () => reject(new Error("Leaflet n’a pas chargé."));
        document.head.appendChild(script);
      });
    }

    waitForExistingScript(script) {
      if (script.dataset.loaded === "true" && window.L) return Promise.resolve(window.L);

      return new Promise((resolve, reject) => {
        script.addEventListener("load", () => resolve(window.L), { once: true });
        script.addEventListener("error", reject, { once: true });
      });
    }
  }

  class TripMapController {
    constructor(options) {
      this.node = options.node;
      this.places = options.places;
      this.loader = options.loader;
      this.map = null;
      this.polyline = null;
      this.resizeTimer = null;
    }

    mount() {
      return this.loader.ensure().then((L) => {
        if (!this.node) return this;
        if (!this.map) this.createMap(L);
        return this;
      });
    }

    createMap(L) {
      this.map = L.map(this.node, { scrollWheelZoom: false }).setView([54.1, -2.6], 6);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(this.map);

      Object.entries(this.places).forEach(([key, place]) => {
        this.addPlaceMarker(L, key, place);
      });
    }

    addPlaceMarker(L, key, place) {
      L.circleMarker(place.coords, {
        radius: key === "villa" ? 10 : 7,
        color: key === "villa" ? "#f5c15c" : "#94bee5",
        fillColor: key === "villa" ? "#7a1538" : "#3f5f87",
        fillOpacity: 0.92,
        weight: 2,
      })
        .addTo(this.map)
        .bindPopup("<b>" + HtmlEscaper.escape(place.name) + "</b><br>" + HtmlEscaper.escape(place.note));
    }

    drawScenario(scenario) {
      if (!window.L || !this.map || !scenario) return;

      if (this.polyline) this.map.removeLayer(this.polyline);

      const points = scenario.route.map((key) => this.places[key].coords);
      this.polyline = window.L.polyline(points, {
        color: scenario.color,
        weight: 4,
        opacity: 0.9,
        dashArray: scenario.dashArray,
      }).addTo(this.map);

      this.map.fitBounds(this.polyline.getBounds(), { padding: [36, 36] });
      this.scheduleResize();
    }

    scheduleResize() {
      if (!this.map) return;
      if (this.resizeTimer) clearTimeout(this.resizeTimer);
      this.resizeTimer = setTimeout(() => {
        if (this.map) this.map.invalidateSize();
      }, 40);
    }

    destroy() {
      if (this.resizeTimer) clearTimeout(this.resizeTimer);
      this.resizeTimer = null;
      if (this.map) this.map.remove();
      this.map = null;
      this.polyline = null;
    }
  }

  ns.HtmlEscaper = HtmlEscaper;
  ns.LeafletAssetLoader = LeafletAssetLoader;
  ns.TripMapController = TripMapController;
})();
