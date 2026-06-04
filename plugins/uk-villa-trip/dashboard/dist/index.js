(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  const { React } = SDK;
  const h = React.createElement;
  const { useEffect, useMemo, useRef, useState } = SDK.hooks;

  const LEAFLET_CSS_ID = "uk-villa-trip-leaflet-css";
  const LEAFLET_JS_ID = "uk-villa-trip-leaflet-js";
  const LEAFLET_CSS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
  const LEAFLET_JS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  const LEAFLET_CSS_INTEGRITY = "sha256-p4NxAoJBhIINfQPDJWD60x/P9fM5uaD8Zr1EHBVY9rg=";
  const LEAFLET_JS_INTEGRITY = "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";

  const places = {
    london: {
      name: "Londres",
      coords: [51.5074456, -0.1277653],
      note: "Arrivée, buffer jetlag, Tower/Thames, British Museum, soirée pub/marché.",
    },
    stonehenge: {
      name: "Excursion Stonehenge",
      coords: [51.1788293, -1.826183],
      note: "Must-have. À garder comme excursion depuis Londres via Salisbury pour éviter la friction d’hébergement.",
    },
    villa: {
      name: "Villa Park / Birmingham",
      coords: [52.5091264, -1.8850354],
      note: "Pèlerinage principal: Aston Villa à Villa Park.",
    },
    york: {
      name: "York",
      coords: [53.9656579, -1.0743052],
      note: "Ville médiévale, pause rail-friendly entre les Midlands et l’Écosse.",
    },
    edinburgh: {
      name: "Édimbourg",
      coords: [55.9533456, -3.1883749],
      note: "Base écossaise: ville + point de départ pour excursion.",
    },
    glencoe: {
      name: "Glencoe / excursion Highlands",
      coords: [56.6827647, -5.1014552],
      note: "Drame Highlands guidé sans voiture. Journée de 10–13h, snacks et météo existentielle inclus.",
    },
  };

  const scenarios = {
    md2: {
      title: "Scénario A: Villa à domicile le 13/14 oct.",
      color: "#f5c15c",
      dashArray: null,
      route: ["london", "stonehenge", "london", "villa", "york", "edinburgh", "glencoe", "edinburgh"],
      summary: ["Londres", "Excursion Stonehenge", "Birmingham / Villa Park", "York optionnel", "Édimbourg", "Excursion Highlands", "Retour via EDI/MAN"],
      days: [
        ["Oct 8–11", "Londres + Stonehenge", "Buffer d’arrivée avec Tower/Thames, British Museum, soirée pub/marché et une excursion Stonehenge via Salisbury."],
        ["Oct 11–15", "Bloc Birmingham / Villa Park", "Positionnement pour un weekend PL si utile + LDC J2 le 13/14 oct. si Villa est à domicile. Villa Park est l’ancre."],
        ["Oct 15–16", "York optionnel", "Garder comme arrêt compact en train seulement si le calendrier laisse du slack; sinon direct Birmingham → Édimbourg."],
        ["Oct 16–21", "Édimbourg + Highlands", "Base à Édimbourg, un tour guidé Highlands/Glencoe, assez de slack pour que la pluie tue pas toute l’affaire."],
        ["Oct 21–22", "Buffer de sortie", "Vol depuis Édimbourg si le prix a de l’allure; sinon train vers Manchester/Londres selon le calcul des vols."],
      ],
    },
    md3: {
      title: "Scénario B: Villa à domicile le 20/21 oct.",
      color: "#58d68d",
      dashArray: "8 8",
      route: ["london", "stonehenge", "london", "edinburgh", "glencoe", "edinburgh", "villa"],
      summary: ["Londres", "Excursion Stonehenge", "Édimbourg", "Excursion Highlands", "Birmingham / Villa Park", "Retour via MAN/BHX/LHR"],
      days: [
        ["Oct 8–12", "Londres + Stonehenge", "Début smooth: Tower/Thames, British Museum, soirée pub/marché et Stonehenge en excursion sans voiture via Salisbury."],
        ["Oct 12–17", "Édimbourg + Highlands", "Monter au nord avant le climax Villa. Base à Édimbourg + tour guidé Highlands/Glencoe. York saute probablement ici."],
        ["Oct 17–22", "Bloc Birmingham / Villa Park", "Retour vers le sud pour le weekend PL et la LDC J3 le 20/21 oct. si Villa est à domicile. C’est la version “climax LDC”."],
        ["Oct 22", "Sortie", "Préférer un départ de Birmingham/Manchester si les prix se comportent. Londres seulement si c’est significativement moins cher."],
      ],
    },
  };

  const decisions = [
    ["Échec à éviter #1", "rater Villa Park", true],
    ["Échec à éviter #1.1", "scam / pas de chambre / overpay de panique", true],
    ["Budget", "C$4k–5.5k sur place, hors vols + billets de match", false],
    ["Hôtels", "réservations remboursables tôt dans toutes les villes pivots", false],
    ["Réservation", "site direct de l’hôtel préféré; Booking.com/Expedia pour repérage/backup", false],
    ["Vols", "YQB si ça a de l’allure; YUL si ça sauve beaucoup d’argent/temps", false],
    ["Rythme", "sous-planifié, 1 ancre/jour", false],
    ["Stonehenge", "excursion depuis Londres; York devient optionnel si le calendrier est serré", false],
  ];

  const hotelZones = [
    ["Londres", "Priorité: bordure King’s Cross / St Pancras / Euston", "Meilleure utilité de route: Euston pour Birmingham, King’s Cross pour York/Édimbourg, Tube facile. Backup avec belle vibe: London Bridge/Borough. Option plus fancy/chère: Covent Garden."],
    ["Birmingham", "Priorité: centre-ville / New Street / Grand Central", "N’optimise pas pour dormir à côté de Villa Park. Dors central, transit/taxi vers le match. C’est le move anti-scam et anti-arrivée tardive.", true],
    ["York", "Optionnel: gare / Micklegate / dans les murs", "Garde seulement si le calendrier de Villa laisse du slack. Si inclus, priorise la marche facile vers la gare + vieux centre; évite le lodging cute-mais-loin qui ajoute de la friction taxi."],
    ["Édimbourg", "Priorité: Waverley / New Town / bordure Old Town", "Meilleur pour l’arrivée en train, le pickup de tour Highlands, et éviter de tirer ta valise en montée comme une sous-intrigue victorienne tragique. Grassmarket/Royal Mile si le prix se comporte."],
  ];

  const cityAnchors = [
    ["Londres", "Tower/Thames + British Museum + soirée pub/marché", "Stonehenge est l’excursion incontournable via Salisbury; Londres reste simple, pas saturé en musées."],
    ["Birmingham", "Pèlerinage Villa + canaux/pub/reset", "Pas de tourisme par obligation. Birmingham existe pour protéger l’énergie de matchday.", true],
    ["York", "Arrêt compact optionnel en train", "Seulement si le calendrier de Villa laisse du slack; sinon on saute proprement."],
    ["Édimbourg", "Une ancre urbaine + une journée météo flexible", "Old Town/Royal Mile/extérieur du château ou Calton Hill/Arthur’s Seat selon la météo et les jambes."],
    ["Highlands", "Glencoe + Glenfinnan, longue journée cinématique", "À réserver comme tour guidé sans voiture depuis Édimbourg; garder une marge de récupération autour."],
  ];

  function ensureLeafletAssets() {
    if (!document.getElementById(LEAFLET_CSS_ID)) {
      const link = document.createElement("link");
      link.id = LEAFLET_CSS_ID;
      link.rel = "stylesheet";
      link.href = LEAFLET_CSS_URL;
      link.integrity = LEAFLET_CSS_INTEGRITY;
      link.crossOrigin = "";
      document.head.appendChild(link);
    }

    if (window.L) return Promise.resolve(window.L);

    const existing = document.getElementById(LEAFLET_JS_ID);
    if (existing) {
      if (existing.dataset.loaded === "true" && window.L) return Promise.resolve(window.L);
      return new Promise(function (resolve, reject) {
        existing.addEventListener("load", function () { resolve(window.L); }, { once: true });
        existing.addEventListener("error", reject, { once: true });
      });
    }

    return new Promise(function (resolve, reject) {
      const script = document.createElement("script");
      script.id = LEAFLET_JS_ID;
      script.src = LEAFLET_JS_URL;
      script.integrity = LEAFLET_JS_INTEGRITY;
      script.crossOrigin = "";
      script.async = true;
      script.onload = function () {
        script.dataset.loaded = "true";
        resolve(window.L);
      };
      script.onerror = function () { reject(new Error("Leaflet n’a pas chargé.")); };
      document.head.appendChild(script);
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function Tag({ children, hot }) {
    return h("span", { className: hot ? "uvt-tag uvt-tag-hot" : "uvt-tag" }, children);
  }

  function Day({ item }) {
    const gated = !!item[3] || item[1].includes("Villa") || item[2].includes("LDC");
    return h("div", { className: gated ? "uvt-day uvt-gate" : "uvt-day" },
      h("div", { className: "uvt-date" }, item[0]),
      h("div", null,
        h("strong", null, item[1]),
        h("small", null, item[2])
      )
    );
  }

  function RouteSummary({ scenario }) {
    const parts = [];
    scenario.summary.forEach(function (label, index) {
      parts.push(h("span", { key: "pill-" + label + index, className: "uvt-pill" }, label));
      if (index < scenario.summary.length - 1) {
        parts.push(h("span", { key: "arrow-" + index, className: "uvt-arrow" }, "→"));
      }
    });
    return h("div", { className: "uvt-section" },
      h("h2", null, scenario.title),
      h("div", { className: "uvt-route-line" }, parts)
    );
  }

  function TripMap({ activeScenarioId }) {
    const mapRef = useRef(null);
    const leafletRef = useRef(null);
    const polyRef = useRef(null);
    const [loadError, setLoadError] = useState(null);
    const [leafletReady, setLeafletReady] = useState(false);

    useEffect(function () {
      let cancelled = false;
      ensureLeafletAssets()
        .then(function (L) {
          if (cancelled || !mapRef.current) return;
          if (!leafletRef.current) {
            const map = L.map(mapRef.current, { scrollWheelZoom: false }).setView([54.1, -2.6], 6);
            L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
              maxZoom: 19,
              attribution: "&copy; OpenStreetMap contributors",
            }).addTo(map);

            Object.entries(places).forEach(function ([key, place]) {
              L.circleMarker(place.coords, {
                radius: key === "villa" ? 10 : 7,
                color: key === "villa" ? "#f5c15c" : "#94bee5",
                fillColor: key === "villa" ? "#7a1538" : "#3f5f87",
                fillOpacity: 0.92,
                weight: 2,
              })
                .addTo(map)
                .bindPopup("<b>" + escapeHtml(place.name) + "</b><br>" + escapeHtml(place.note));
            });
            leafletRef.current = map;
          }
          setLoadError(null);
          setLeafletReady(true);
        })
        .catch(function (error) {
          if (!cancelled) setLoadError(error.message || "La carte n’a pas chargé.");
        });
      return function () { cancelled = true; };
    }, []);

    useEffect(function () {
      const L = window.L;
      const map = leafletRef.current;
      if (!L || !map) return;
      const scenario = scenarios[activeScenarioId];
      if (polyRef.current) map.removeLayer(polyRef.current);
      const points = scenario.route.map(function (key) { return places[key].coords; });
      polyRef.current = L.polyline(points, {
        color: scenario.color,
        weight: 4,
        opacity: 0.9,
        dashArray: scenario.dashArray,
      }).addTo(map);
      map.fitBounds(polyRef.current.getBounds(), { padding: [36, 36] });
      const resizeTimer = setTimeout(function () { map.invalidateSize(); }, 40);
      return function () { clearTimeout(resizeTimer); };
    }, [activeScenarioId, leafletReady]);

    useEffect(function () {
      return function () {
        if (leafletRef.current) {
          leafletRef.current.remove();
          leafletRef.current = null;
        }
      };
    }, []);

    if (loadError) {
      return h("div", { className: "uvt-map-fallback" },
        h("div", null,
          h("h2", null, "Carte indisponible"),
          h("p", null, loadError),
          h("p", null, "Fallback route: Londres → Stonehenge → Villa Park/Birmingham → York/Édimbourg → Glencoe.")
        )
      );
    }
    return h("div", { ref: mapRef, className: "uvt-map", role: "img", "aria-label": "Carte interactive de l’itinéraire UK 2026" });
  }

  function StaticSection({ title, intro, items }) {
    return h("section", { className: "uvt-card uvt-pad uvt-section" },
      h("h2", null, title),
      intro ? h("p", null, intro) : null,
      h("div", { className: "uvt-timeline" }, items.map(function (item) {
        return h(Day, { key: item[0] + item[1], item });
      }))
    );
  }

  function UkVillaTripPage() {
    const [activeScenarioId, setActiveScenarioId] = useState("md2");
    const scenario = scenarios[activeScenarioId];

    const decisionTags = useMemo(function () {
      return decisions.map(function ([label, text, hot]) {
        return h(Tag, { key: label, hot }, label + ": " + text);
      });
    }, []);

    return h("div", { className: "uk-villa-trip" },
      h("div", { className: "uvt-shell" },
        h("section", { className: "uvt-hero" },
          h("div", { className: "uvt-card uvt-pad" },
            h("h1", null, "Voyage solo au Royaume-Uni — Villa Park d’abord, la belle carte ensuite."),
            h("p", null,
              "Fenêtre de base: ", h("b", null, "8–22 octobre 2026"),
              ". Le voyage est construit autour d’un match ", h("b", null, "Aston Villa en Ligue des champions à Villa Park"),
              " si le tirage UEFA nous bénit au lieu de nous niaiser."
            ),
            h("div", { className: "uvt-tagrow" },
              h(Tag, { hot: true }, "Ancre #1: Ligue des champions à Villa Park"),
              h(Tag, null, "Plan B: Premier League à Villa Park"),
              h(Tag, null, "Sans voiture"),
              h(Tag, null, "Colonne vertébrale en train"),
              h(Tag, null, "Hôtels confort-pragmatiques"),
              h(Tag, null, "Londres + Stonehenge + Birmingham + York optionnel + Édimbourg + Highlands")
            )
          ),
          h("div", { className: "uvt-card uvt-pad" },
            h("div", { className: "uvt-statgrid" },
              h("div", { className: "uvt-stat" }, h("b", null, "14"), h("span", null, "nuits au Royaume-Uni approx.")),
              h("div", { className: "uvt-stat" }, h("b", null, "2"), h("span", null, "scénarios selon UEFA")),
              h("div", { className: "uvt-stat" }, h("b", null, "13–14"), h("span", null, "oct. — LDC J2")),
              h("div", { className: "uvt-stat" }, h("b", null, "20–21"), h("span", null, "oct. — LDC J3"))
            ),
            h("p", { className: "uvt-note" }, "Le plan final se verrouille après: calendrier de Premier League le 19 juin 2026, tirage de la LDC le 27 août 2026, puis ventes Aston Villa.")
          )
        ),

        h("section", { className: "uvt-card uvt-pad uvt-section" },
          h("h2", null, "Décisions verrouillées jusqu’ici"),
          h("div", { className: "uvt-tagrow" }, decisionTags)
        ),

        h(StaticSection, {
          title: "Matrice des zones d’hôtels — filtre anti-scam strict",
          intro: "Règle: chambre privée, réception 24h, bons avis solides, proche train/transit, réservation remboursable tôt, réservation directe avec l’hôtel préférée. Booking/Expedia seulement pour repérage ou backup.",
          items: hotelZones,
        }),

        h(StaticSection, {
          title: "Ancres par ville — sous-planifié, 1 ancre/jour",
          items: cityAnchors,
        }),

        h("section", { className: "uvt-card uvt-pad uvt-section" },
          h("h2", null, "V1 jour par jour — aperçu des scénarios"),
          h("div", { className: "uvt-timeline" },
            h(Day, { item: ["A: LDC J2 à domicile", "8–22 oct.: Londres → Stonehenge → Birmingham/Villa → York optionnel → Édimbourg/Highlands", "Meilleur si Villa joue à domicile le 13/14 oct. Le match tombe au milieu du voyage, puis l’arc vers l’Écosse devient clean.", true] }),
            h(Day, { item: ["B: LDC J3 à domicile", "8–22 oct.: Londres → Stonehenge → Édimbourg/Highlands → York optionnel → Birmingham/Villa", "Meilleur si Villa joue à domicile le 20/21 oct. Le voyage culmine à Villa Park, avec Birmingham comme bloc final.", true] })
          )
        ),

        h("section", { className: "uvt-grid" },
          h("div", { className: "uvt-card" }, h(TripMap, { activeScenarioId })),
          h("div", { className: "uvt-card" },
            h("div", { className: "uvt-tabs" },
              h("button", {
                className: activeScenarioId === "md2" ? "uvt-tab uvt-tab-active" : "uvt-tab",
                type: "button",
                onClick: function () { setActiveScenarioId("md2"); },
              }, "Scénario A — LDC J2 à domicile"),
              h("button", {
                className: activeScenarioId === "md3" ? "uvt-tab uvt-tab-active" : "uvt-tab",
                type: "button",
                onClick: function () { setActiveScenarioId("md3"); },
              }, "Scénario B — LDC J3 à domicile")
            ),
            h("div", { className: "uvt-pad" },
              h(RouteSummary, { scenario }),
              h("div", { className: "uvt-timeline" }, scenario.days.map(function (item) {
                return h(Day, { key: activeScenarioId + item[0], item });
              }))
            )
          )
        ),
        h("footer", { className: "uvt-footer" }, "Brouillon vivant — pas un plan de réservation final. Les dates exactes et les villes pivotent selon Villa à domicile/extérieur et le prix des vols.")
      )
    );
  }

  window.__HERMES_PLUGINS__.register("uk-villa-trip", UkVillaTripPage);
})();
