# UK Villa Trip Dashboard Plugin

Review-only Hermes Dashboard plugin for Sam's October 2026 solo UK trip plan.

The plugin adds a `Voyage UK 2026` dashboard tab with:

- trip constraints and booking gates;
- hotel-zone decision matrix;
- city anchors;
- interactive Leaflet/OpenStreetMap route map;
- two route scenarios depending on Aston Villa's Champions League home fixture window;
- post-Premier-League-fixture-release action plan for Sunday 21 June;
- pinned travel-agency decision: DIY trip, agency only for targeted flight/support value.

## Dashboard preview

When installed as a dashboard plugin and after a dashboard plugin rescan:

```text
http://127.0.0.1:9119/uk-villa-trip
```

Local user plugin path used during review:

```text
~/.hermes/plugins/uk-villa-trip -> <repo>/plugins/uk-villa-trip
```

## Review evidence

Screenshots captured with `npx --yes agent-browser` during the PR handoff:

- [desktop hero](docs/assets/uk-villa-trip-desktop.png)
- [route/map section, scenario B](docs/assets/uk-villa-trip-map-scenario-b.png)
- [mobile/narrow viewport](docs/assets/uk-villa-trip-mobile.png)

## Scope note

This branch is intended as a fork-local review surface, not an upstream NousResearch feature request. The plugin is personal trip-planning content and should not be proposed upstream unless Sam explicitly asks for that later.
