(function () {
  "use strict";

  const ns = window.UkVillaTrip = window.UkVillaTrip || {};

  class UkVillaTripComponents {
    static create(options) {
      const SDK = options.SDK;
      const data = options.data;
      const leafletLoader = options.leafletLoader;
      const { React } = SDK;
      const h = React.createElement;
      const { useEffect, useMemo, useRef, useState } = SDK.hooks;

      function Tag({ children, hot }) {
        return h("span", { className: hot ? "uvt-tag uvt-tag-hot" : "uvt-tag" }, children);
      }

      function Day({ item }) {
        const gated = !!item[3] || item[1].includes("Villa") || item[2].includes("LDC");
        return h("div", { className: gated ? "uvt-day uvt-gate" : "uvt-day" },
          h("div", { className: "uvt-date" }, item[0]),
          h("div", null,
            h("strong", null, item[1]),
            h("small", null, item[2]),
          ),
        );
      }

      function RouteSummary({ scenario }) {
        const parts = [];
        scenario.summary.forEach((label, index) => {
          parts.push(h("span", { key: "pill-" + label + index, className: "uvt-pill" }, label));
          if (index < scenario.summary.length - 1) {
            parts.push(h("span", { key: "arrow-" + index, className: "uvt-arrow" }, "→"));
          }
        });
        return h("div", { className: "uvt-section" },
          h("h2", null, scenario.title),
          h("div", { className: "uvt-route-line" }, parts),
        );
      }

      function TripMap({ activeScenarioId }) {
        const mapRef = useRef(null);
        const controllerRef = useRef(null);
        const [loadError, setLoadError] = useState(null);
        const [leafletReady, setLeafletReady] = useState(false);

        useEffect(() => {
          let cancelled = false;
          const controller = new ns.TripMapController({
            node: mapRef.current,
            places: data.places,
            loader: leafletLoader,
          });
          controllerRef.current = controller;

          controller.mount()
            .then(() => {
              if (cancelled) return;
              setLoadError(null);
              setLeafletReady(true);
            })
            .catch((error) => {
              if (!cancelled) setLoadError(error.message || "La carte n’a pas chargé.");
            });

          return () => {
            cancelled = true;
            controller.destroy();
            if (controllerRef.current === controller) controllerRef.current = null;
          };
        }, []);

        useEffect(() => {
          const controller = controllerRef.current;
          if (!controller || !leafletReady) return;
          controller.drawScenario(data.scenarios[activeScenarioId]);
        }, [activeScenarioId, leafletReady]);

        if (loadError) {
          return h("div", { className: "uvt-map-fallback" },
            h("div", null,
              h("h2", null, "Carte indisponible"),
              h("p", null, loadError),
              h("p", null, "Fallback route: Londres → Stonehenge → Villa Park/Birmingham → York/Édimbourg → Glencoe."),
            ),
          );
        }
        return h("div", { ref: mapRef, className: "uvt-map", role: "img", "aria-label": "Carte interactive de l’itinéraire UK 2026" });
      }

      function StaticSection({ title, intro, items }) {
        return h("section", { className: "uvt-card uvt-pad uvt-section" },
          h("h2", null, title),
          intro ? h("p", null, intro) : null,
          h("div", { className: "uvt-timeline" }, items.map((item) => {
            return h(Day, { key: item[0] + item[1], item });
          })),
        );
      }

      function ScenarioTabs({ activeScenarioId, onChange }) {
        return h("div", { className: "uvt-tabs" },
          h("button", {
            className: activeScenarioId === "md2" ? "uvt-tab uvt-tab-active" : "uvt-tab",
            type: "button",
            onClick: () => onChange("md2"),
          }, "Scénario A — LDC J2 à domicile"),
          h("button", {
            className: activeScenarioId === "md3" ? "uvt-tab uvt-tab-active" : "uvt-tab",
            type: "button",
            onClick: () => onChange("md3"),
          }, "Scénario B — LDC J3 à domicile"),
        );
      }

      function Hero() {
        return h("section", { className: "uvt-hero" },
          h("div", { className: "uvt-card uvt-pad" },
            h("h1", null, "Voyage solo au Royaume-Uni — Villa Park d’abord, la belle carte ensuite."),
            h("p", null,
              "Fenêtre de base: ", h("b", null, "8–22 octobre 2026"),
              ". Le voyage est construit autour d’un match ", h("b", null, "Aston Villa en Ligue des champions à Villa Park"),
              " si le tirage UEFA nous bénit au lieu de nous niaiser.",
            ),
            h("div", { className: "uvt-tagrow" },
              h(Tag, { hot: true }, "Ancre #1: Ligue des champions à Villa Park"),
              h(Tag, null, "Plan B: Premier League à Villa Park"),
              h(Tag, null, "Sans voiture"),
              h(Tag, null, "Colonne vertébrale en train"),
              h(Tag, null, "Hôtels confort-pragmatiques"),
              h(Tag, null, "Londres + Stonehenge + Birmingham + York optionnel + Édimbourg + Highlands"),
            ),
          ),
          h("div", { className: "uvt-card uvt-pad" },
            h("div", { className: "uvt-statgrid" },
              h("div", { className: "uvt-stat" }, h("b", null, "14"), h("span", null, "nuits au Royaume-Uni approx.")),
              h("div", { className: "uvt-stat" }, h("b", null, "2"), h("span", null, "scénarios selon UEFA")),
              h("div", { className: "uvt-stat" }, h("b", null, "13–14"), h("span", null, "oct. — LDC J2")),
              h("div", { className: "uvt-stat" }, h("b", null, "20–21"), h("span", null, "oct. — LDC J3")),
            ),
            h("p", { className: "uvt-note" }, "Le plan final se verrouille après: calendrier de Premier League le 19 juin 2026, tirage de la LDC le 27 août 2026, puis ventes Aston Villa."),
          ),
        );
      }

      function DecisionsSection() {
        const decisionTags = useMemo(() => {
          return data.decisions.map(([label, text, hot]) => {
            return h(Tag, { key: label, hot }, label + ": " + text);
          });
        }, []);

        return h("section", { className: "uvt-card uvt-pad uvt-section" },
          h("h2", null, "Décisions verrouillées jusqu’ici"),
          h("div", { className: "uvt-tagrow" }, decisionTags),
        );
      }

      function ScenariosSection({ activeScenarioId, setActiveScenarioId, scenario }) {
        return h("section", { className: "uvt-grid" },
          h("div", { className: "uvt-card" }, h(TripMap, { activeScenarioId })),
          h("div", { className: "uvt-card" },
            h(ScenarioTabs, { activeScenarioId, onChange: setActiveScenarioId }),
            h("div", { className: "uvt-pad" },
              h(RouteSummary, { scenario }),
              h("div", { className: "uvt-timeline" }, scenario.days.map((item) => {
                return h(Day, { key: activeScenarioId + item[0], item });
              })),
            ),
          ),
        );
      }

      function UkVillaTripPage() {
        const [activeScenarioId, setActiveScenarioId] = useState("md2");
        const scenario = data.scenarios[activeScenarioId];

        return h("div", { className: "uk-villa-trip" },
          h("div", { className: "uvt-shell" },
            h(Hero),
            h(DecisionsSection),
            h(StaticSection, {
              title: "Matrice des zones d’hôtels — filtre anti-scam strict",
              intro: "Règle: chambre privée, réception 24h, bons avis solides, proche train/transit, réservation remboursable tôt, réservation directe avec l’hôtel préférée. Booking/Expedia seulement pour repérage ou backup.",
              items: data.hotelZones,
            }),
            h(StaticSection, {
              title: "Ancres par ville — sous-planifié, 1 ancre/jour",
              items: data.cityAnchors,
            }),
            h("section", { className: "uvt-card uvt-pad uvt-section" },
              h("h2", null, "V1 jour par jour — aperçu des scénarios"),
              h("div", { className: "uvt-timeline" },
                h(Day, { item: ["A: LDC J2 à domicile", "8–22 oct.: Londres → Stonehenge → Birmingham/Villa → York optionnel → Édimbourg/Highlands", "Meilleur si Villa joue à domicile le 13/14 oct. Le match tombe au milieu du voyage, puis l’arc vers l’Écosse devient clean.", true] }),
                h(Day, { item: ["B: LDC J3 à domicile", "8–22 oct.: Londres → Stonehenge → Édimbourg/Highlands → York optionnel → Birmingham/Villa", "Meilleur si Villa joue à domicile le 20/21 oct. Le voyage culmine à Villa Park, avec Birmingham comme bloc final.", true] }),
              ),
            ),
            h(ScenariosSection, { activeScenarioId, setActiveScenarioId, scenario }),
            h("footer", { className: "uvt-footer" }, "Brouillon vivant — pas un plan de réservation final. Les dates exactes et les villes pivotent selon Villa à domicile/extérieur et le prix des vols."),
          ),
        );
      }

      return { UkVillaTripPage };
    }
  }

  ns.UkVillaTripComponents = UkVillaTripComponents;
})();
