/* Provider ROI dashboard plugin. Plain IIFE: all runtime dependencies arrive via the SDK. */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  const { React } = SDK;
  const h = React.createElement;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Input, Label, Select, SelectOption, Separator } = SDK.components;
  const { useState, useEffect, useCallback, useMemo } = SDK.hooks;

  function tone(action) {
    return { keep: "success", observe: "secondary", reassign: "warning", downgrade: "warning", cancel: "destructive", exclude: "outline" }[action] || "secondary";
  }
  function number(value) { return Number(value || 0).toLocaleString(); }
  function money(value) { return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(value || 0); }
  function api(path, options) {
    const init = options ? Object.assign({ headers: { "Content-Type": "application/json" } }, options) : undefined;
    return SDK.fetchJSON("/api/plugins/provider-roi" + path, init);
  }
  function SortButton(props) {
    return h("button", { className: "provider-roi-sort", type: "button", onClick: props.onClick, "aria-label": "Sort by " + props.label }, props.label, props.active ? (props.desc ? " ↓" : " ↑") : " ↕");
  }
  function useLocalSort(items) {
    const [sort, setSort] = useState({ key: "activity", desc: true });
    const sorted = useMemo(function () {
      return items.slice().sort(function (a, b) {
        const left = a[sort.key] || 0; const right = b[sort.key] || 0;
        return (left > right ? 1 : left < right ? -1 : 0) * (sort.desc ? -1 : 1);
      });
    }, [items, sort]);
    return [sorted, sort, function (key) { setSort(function (old) { return { key: key, desc: old.key === key ? !old.desc : true }; }); }];
  }
  function Summary(props) {
    const stats = props.summary || {};
    return h("div", { className: "provider-roi-summary", "aria-label": "Provider ROI summary" },
      [["Activity", number(stats.activity)], ["Delivered", number(stats.delivered)], ["Approved", number(stats.approved)], ["Derived friction", number(stats.friction)]].map(function (item) {
        return h(Card, { key: item[0] }, h(CardContent, { className: "provider-roi-stat" }, h("span", null, item[0]), h("strong", null, item[1])));
      })
    );
  }
  function ProviderRow(props) {
    const provider = props.provider;
    const [expanded, setExpanded] = useState(false);
    const [saving, setSaving] = useState(false);
    const [notice, setNotice] = useState("");
    const toggle = function () {
      setSaving(true);
      api("/settings", { method: "POST", body: JSON.stringify({ kind: "provider", provider: provider.provider, included: !provider.rule.included }) })
        .then(function () { setNotice("Saved"); props.onRefresh(); })
        .catch(function (err) { setNotice("Save failed: " + (err.message || err)); })
        .finally(function () { setSaving(false); });
    };
    return h("li", { className: "provider-roi-provider" },
      h("div", { className: "provider-roi-row" },
        h("button", { className: "provider-roi-name", type: "button", onClick: function () { setExpanded(!expanded); }, "aria-expanded": expanded }, provider.rule.label || provider.provider),
        h("span", null, number(provider.activity)), h("span", null, number(provider.delivered)), h("span", null, number(provider.approved)),
        h(Badge, { tone: tone(provider.verdict.action) }, provider.verdict.action),
        h(Button, { size: "sm", variant: "outline", onClick: toggle, disabled: saving || provider.rule.classification === "critical" }, saving ? "Saving…" : provider.rule.included ? "Exclude" : "Include")
      ),
      notice ? h("p", { className: "provider-roi-notice", role: "status" }, notice) : null,
      expanded ? h(Card, { className: "provider-roi-detail" }, h(CardContent, null,
        h("dl", { className: "provider-roi-detail-grid" },
          h("div", null, h("dt", null, "Classification"), h("dd", null, provider.rule.classification)),
          h("div", null, h("dt", null, "Fixed plan"), h("dd", null, money(provider.rule.monthly_cost_usd))),
          h("div", null, h("dt", null, "Usage cost"), h("dd", null, money(provider.usage_cost_usd))),
          h("div", null, h("dt", null, "Profiles"), h("dd", null, provider.profiles.join(", ") || "None")),
          h("div", null, h("dt", null, "Verdict"), h("dd", null, provider.verdict.reason)),
          h("div", null, h("dt", null, "Friction"), h("dd", null, number(provider.friction))),
          h("div", null, h("dt", null, "Quota"), h("dd", null, provider.manual_quota ? number(provider.manual_quota.used_percent) + "% manual" : "No manual quota")),
          h("div", null, h("dt", null, "Exception"), h("dd", null, provider.exception ? provider.exception.reason + " until " + provider.exception.expires_on : "None"))
        ),
        h(Separator, { className: "my-3" }),
        h("h4", null, "Models and tasks"),
        h("ul", { className: "provider-roi-models" }, provider.models.map(function (model, index) { return h("li", { key: index }, model.model + " · " + model.task + " · " + number(model.api_calls) + " calls"); }))
      )) : null
    );
  }
  function QuotaForm(props) {
    const [provider, setProvider] = useState(""); const [percent, setPercent] = useState(""); const [message, setMessage] = useState("");
    function submit(event) {
      event.preventDefault();
      api("/settings", { method: "POST", body: JSON.stringify({ kind: "manual_quota", provider: provider, used_percent: Number(percent) }) })
        .then(function () { setMessage("Manual quota recorded with timestamp."); setPercent(""); })
        .catch(function (err) { setMessage("Quota not saved: " + (err.message || err)); });
    }
    return h("form", { className: "provider-roi-form", onSubmit: submit },
      h(Label, { htmlFor: "roi-quota-provider" }, "Manual quota (Kimi/OpenCode)"),
      h(Input, { id: "roi-quota-provider", value: provider, required: true, placeholder: "provider id", onChange: function (e) { setProvider(e.target.value); } }),
      h(Input, { type: "number", min: 0, max: 100, required: true, value: percent, placeholder: "used %", onChange: function (e) { setPercent(e.target.value); } }),
      h(Button, { type: "submit", variant: "outline" }, "Record quota"), message ? h("span", { role: "status" }, message) : null
    );
  }
  function ExceptionForm() {
    const [provider, setProvider] = useState(""); const [reason, setReason] = useState(""); const [expires, setExpires] = useState(""); const [message, setMessage] = useState("");
    function submit(event) {
      event.preventDefault();
      api("/settings", { method: "POST", body: JSON.stringify({ kind: "exception", provider: provider, reason: reason, expires_on: expires }) })
        .then(function () { setMessage("Exception saved."); setReason(""); })
        .catch(function (err) { setMessage("Exception not saved: " + (err.message || err)); });
    }
    return h("form", { className: "provider-roi-form", onSubmit: submit },
      h(Label, { htmlFor: "roi-exception-provider" }, "Time-limited exception"),
      h(Input, { id: "roi-exception-provider", value: provider, required: true, placeholder: "provider id", onChange: function (e) { setProvider(e.target.value); } }),
      h(Input, { value: reason, required: true, placeholder: "reason", onChange: function (e) { setReason(e.target.value); } }),
      h(Input, { type: "date", value: expires, required: true, onChange: function (e) { setExpires(e.target.value); } }),
      h(Button, { type: "submit", variant: "outline" }, "Save exception"), message ? h("span", { role: "status" }, message) : null
    );
  }
  function ProviderRoiPage() {
    const [report, setReport] = useState(null); const [error, setError] = useState(""); const [month, setMonth] = useState("");
    const load = useCallback(function () { setError(""); api("/overview" + (month ? "?month=" + encodeURIComponent(month) : "")).then(setReport).catch(function (err) { setError(err.message || "Unable to load Provider ROI."); }); }, [month]);
    useEffect(function () { load(); }, [load]);
    const providers = report && Array.isArray(report.providers) ? report.providers : [];
    const sorted = useLocalSort(providers); const list = sorted[0]; const sort = sorted[1]; const setSort = sorted[2];
    return h("section", { className: "provider-roi", "aria-busy": !report && !error },
      h("header", { className: "provider-roi-header" }, h("div", null, h("h1", null, "Provider ROI"), h("p", null, "Provider-first usage, delivery and cancellation review. Billing and provider configuration remain read-only.")),
        h("div", { className: "provider-roi-controls" }, h(Input, { type: "month", value: month, onChange: function (e) { setMonth(e.target.value); }, "aria-label": "Reporting month" }), h(Button, { onClick: load, variant: "outline" }, "Refresh"))),
      !report && !error ? h("p", { className: "provider-roi-loading", role: "status" }, "Loading Provider ROI…") : null,
      error ? h(Card, { className: "provider-roi-error", role: "alert" }, h(CardContent, null, error)) : null,
      report ? h(React.Fragment, null,
        report.snapshot ? h(Badge, { tone: "secondary" }, "Closed-month snapshot") : null,
        h(Summary, { summary: report.summary }),
        report.warnings.length ? h(Card, { className: "provider-roi-warning" }, h(CardContent, null, "Partial data: ", report.warnings.join(" · "))) : null,
        providers.length ? h(Card, null, h(CardHeader, null, h(CardTitle, null, "Provider comparison")), h(CardContent, null,
          h("div", { className: "provider-roi-table-head", "aria-hidden": "true" }, h(SortButton, { label: "Provider", active: sort.key === "provider", desc: sort.desc, onClick: function () { setSort("provider"); } }), h(SortButton, { label: "Activity", active: sort.key === "activity", desc: sort.desc, onClick: function () { setSort("activity"); } }), h("span", null, "Delivered"), h("span", null, "Approved"), h("span", null, "Verdict"), h("span", null, "Plan")),
          h("ul", { className: "provider-roi-list" }, list.map(function (provider) { return h(ProviderRow, { key: provider.provider, provider: provider, onRefresh: load }); }))
        )) : h(Card, null, h(CardContent, { className: "provider-roi-empty" }, "No classified provider usage for this month. Add usage or include a provider locally.")),
        h(QuotaForm, null),
        h(ExceptionForm, null),
        h("details", { className: "provider-roi-disclosure" }, h("summary", null, "Metric provenance and safeguards"), h("p", null, "Activity comes from session_model_usage. Delivered is Kanban review/done; approved is Kanban done. Friction is derived from Kanban diagnostics, not a stored field. OpenRouter/PAYG is excluded. Nous Portal is critical infrastructure and cannot be cancelled here."), h("p", null, "Private store: ", report.store_path))
      ) : null
    );
  }
  window.__HERMES_PLUGINS__.register("provider-roi", ProviderRoiPage);
})();
