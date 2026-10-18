/* Discovery Monitor management panel. No external resources or dependencies. */
class DiscoveryMonitorPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = "findings";
    this._search = "";
    this._generation = 0;
    this._busy = false;
    this._snapshot = null;
    this._error = "";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; overflow:auto; color:var(--primary-text-color);
          background:var(--primary-background-color); font-family:var(--paper-font-body1_-_font-family, sans-serif); }
        * { box-sizing:border-box; }
        header { display:flex; align-items:center; gap:16px; padding:12px 16px;
          background:var(--app-header-background-color, var(--primary-color));
          color:var(--app-header-text-color, white); min-height:64px; }
        header strong { font-size:20px; }
        header button { color:inherit; border:0; background:transparent; padding:12px; }
        main { max-width:1000px; padding:24px 16px 48px; margin:auto; }
        h1 { font-size:26px; margin:0 0 8px; }
        h2 { font-size:18px; margin:0; overflow-wrap:anywhere; }
        p { line-height:1.5; }
        .muted, dt { color:var(--secondary-text-color); }
        nav, .actions { display:flex; flex-wrap:wrap; gap:8px; }
        nav { margin:24px 0 16px; }
        button, input { font:inherit; }
        button { cursor:pointer; border:1px solid var(--divider-color, #aaa); border-radius:12px;
          background:var(--card-background-color, white); color:var(--primary-text-color); padding:10px 14px; min-height:44px; }
        button:hover { border-color:var(--primary-color, #03a9f4); }
        button:focus-visible, input:focus-visible { outline:2px solid var(--primary-color, #03a9f4); outline-offset:3px; }
        button:disabled { cursor:wait; opacity:.55; }
        nav button[aria-pressed="true"], .primary { color:var(--text-primary-color, white);
          background:var(--primary-color, #0288d1); border-color:transparent; }
        .ignore { color:var(--error-color, #b71c1c); }
        label { display:block; }
        input { width:100%; border:1px solid var(--divider-color, #aaa); border-radius:12px;
          padding:12px; margin:8px 0 16px; color:var(--primary-text-color); background:var(--card-background-color, white); }
        ul { list-style:none; padding:0; margin:0; display:grid; gap:12px; }
        li { padding:20px; background:var(--card-background-color, white);
          border:1px solid var(--divider-color, #ddd); border-radius:var(--ha-card-border-radius, 12px); }
        dl { display:flex; flex-wrap:wrap; gap:12px 24px; margin:14px 0 18px; }
        dl div { min-width:90px; }
        dt { font-size:12px; } dd { margin:4px 0 0; overflow-wrap:anywhere; }
        .notice { padding:16px; border-radius:12px; border:1px solid var(--divider-color, #ddd); }
        #error { color:var(--error-color, #b71c1c); }
        #status { min-height:24px; }
        [hidden] { display:none !important; }
        dialog { width:min(500px, calc(100vw - 32px)); padding:24px; border:1px solid var(--divider-color, #ddd);
          border-radius:16px; background:var(--card-background-color, white); color:var(--primary-text-color); }
        dialog::backdrop { background:rgba(0,0,0,.45); }
        dialog .actions { justify-content:flex-end; margin-top:24px; }
        @media (max-width:600px) { main { padding-top:20px; } li { padding:16px; } .actions button { flex-grow:1; } }
      </style>
      <header><button id="menu" type="button">☰</button><strong>Discovery Monitor</strong></header>
      <main>
        <h1 id="title"></h1><p id="intro" class="muted"></p>
        <nav id="views"></nav>
        <label><span id="search-label"></span><input id="search" type="search" autocomplete="off"></label>
        <p id="status" role="status" aria-live="polite"></p>
        <div id="error" class="notice" role="alert" hidden></div>
        <button id="retry" type="button" hidden></button>
        <ul id="items"></ul><p id="empty" class="notice" hidden></p>
      </main>
      <dialog aria-labelledby="confirm-title" aria-describedby="confirm-description">
        <h2 id="confirm-title"></h2><p id="confirm-description"></p>
        <div class="actions"><button id="cancel" type="button"></button><button id="confirm" type="button" class="primary"></button></div>
      </dialog>`;
    this._q("menu").addEventListener("click", () => {
      this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    this._q("search").addEventListener("input", (event) => {
      this._search = event.target.value;
      this._renderItems();
    });
    this._q("retry").addEventListener("click", () => this._connect());
    this._q("cancel").addEventListener("click", () => this.shadowRoot.querySelector("dialog").close());
    this._q("confirm").addEventListener("click", () => {
      const selection = this._selection;
      this.shadowRoot.querySelector("dialog").close();
      if (selection) void this._act(selection.action, selection.item);
    });
    this.shadowRoot.querySelector("dialog").addEventListener("close", () => { this._selection = null; });
  }

  _q(id) { return this.shadowRoot.getElementById(id); }

  set hass(value) {
    const language = value.locale?.language || value.language || value.config?.language || "en";
    const changed = this._hass?.connection !== value.connection || this._language !== language ||
      this._hass?.user?.is_admin !== value.user?.is_admin;
    this._hass = value;
    this._language = language;
    if (changed && this.isConnected) void this._connect();
  }

  connectedCallback() {
    if (this._hass) void this._connect();
  }

  disconnectedCallback() {
    this._generation++;
    this._disconnect();
  }

  _disconnect() {
    if (this._unsubscribe) {
      void this._unsubscribe().catch(() => {});
      this._unsubscribe = null;
    }
    if (this._connection && this._onDisconnected) {
      this._connection.removeEventListener("disconnected", this._onDisconnected);
    }
    this._connection = null;
  }

  async _connect() {
    const generation = ++this._generation;
    this._disconnect();
    this._snapshot = null;
    this._texts = null;
    this._error = "";
    this._status = "";
    this._selection = null;
    this.shadowRoot.querySelector("dialog").close();
    if (!this._hass?.user?.is_admin) {
      this._error = this._fallback("admin");
      this._render();
      return;
    }
    const connection = this._hass.connection;
    this._connection = connection;
    this._onDisconnected = () => {
      this._snapshot = null;
      this._error = this._fallback("connection");
      this._render();
    };
    connection.addEventListener("disconnected", this._onDisconnected);
    this._render();
    try {
      // Home Assistant re-subscribes this command automatically on reconnection.
      const unsubscribe = await connection.subscribeMessage((snapshot) => {
        if (generation !== this._generation || !this.isConnected) return;
        this._snapshot = snapshot;
        this._texts = snapshot.texts;
        this._error = snapshot.available ? "" : this._t("panel_unavailable");
        this._render();
      }, { type: "discovery_monitor/subscribe", language: this._language });
      if (generation !== this._generation || !this.isConnected) {
        await unsubscribe();
      } else {
        this._unsubscribe = unsubscribe;
      }
    } catch {
      if (generation === this._generation && this.isConnected) {
        this._error = this._fallback("connection");
        this._render();
      }
    }
  }

  _fallback(key) {
    const de = this._language?.toLowerCase().split(/[-_]/)[0] === "de";
    return ({
      loading: de ? "Funde werden geladen …" : "Loading discoveries …",
      connection: de ? "Verbindung nicht verfügbar. Bitte erneut versuchen." : "Connection unavailable. Please try again.",
      admin: de ? "Diese Verwaltung ist nur für Administratoren verfügbar." : "This management page is only available to administrators.",
      retry: de ? "Erneut versuchen" : "Try again",
      menu: de ? "Seitenleiste öffnen" : "Open sidebar",
    })[key];
  }

  _t(key) { return this._texts?.[key] || ""; }

  _el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  _button(text, handler, className) {
    const button = this._el("button", text, className);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
  }

  _render() {
    const available = Boolean(this._snapshot?.available);
    this._q("menu").setAttribute("aria-label", this._fallback("menu"));
    this._q("title").textContent = available
      ? `${this._t("panel_open_discoveries")} · ${this._snapshot.findings.length}` : "Discovery Monitor";
    this._q("intro").textContent = this._t("panel_intro");
    this._q("error").textContent = this._error;
    this._q("error").hidden = !this._error;
    this._q("retry").textContent = this._fallback("retry");
    this._q("retry").hidden = !this._error || !this._hass?.user?.is_admin;
    this._q("status").textContent = this._busy ? this._t("panel_saving")
      : this._status || (!available && !this._error ? this._fallback("loading") : "");
    this._q("views").hidden = !available;
    this._q("search").parentElement.hidden = !available;
    this._q("search-label").textContent = this._t("panel_search");
    const nav = this._q("views");
    const activeView = this.shadowRoot.activeElement?._viewKey;
    nav.replaceChildren();
    if (available) {
      for (const [key, label] of [["findings", "panel_recent"], ["rules", "panel_ignored"]]) {
        const button = this._button(`${this._t(label)} (${this._snapshot[key].length})`, () => {
          this._view = key;
          this._search = "";
          this._q("search").value = "";
          this._status = "";
          this._render();
        });
        button._viewKey = key;
        button.setAttribute("aria-pressed", String(this._view === key));
        nav.append(button);
        if (activeView === key) button.focus();
      }
    }
    this._renderItems();
  }

  _renderItems() {
    const list = this._q("items");
    const activeKey = this.shadowRoot.activeElement?._actionKey;
    list.replaceChildren();
    this._q("empty").hidden = true;
    if (!this._snapshot?.available) return;
    const query = this._search.toLocaleLowerCase();
    const items = this._snapshot[this._view].filter((item) =>
      [item.name, item.integration, item.source].some((value) => String(value || "").toLocaleLowerCase().includes(query))
    );
    this._q("empty").textContent = this._t(query ? "panel_no_matches"
      : this._view === "findings" ? "panel_no_findings" : "panel_no_rules");
    this._q("empty").hidden = items.length > 0;
    for (const item of items) {
      const card = this._el("li");
      card.append(this._el("h2", item.name || this._t("finding")));
      const details = this._el("dl");
      const fields = this._view === "findings"
        ? [["integration", item.integration], ["source", item.source], ["discoveries", item.count], ["last_seen", this._formatTime(item.last_seen)]]
        : [["panel_kind", this._t(item.kind)], ["integration", item.integration]];
      for (const [key, value] of fields) {
        const pair = this._el("div");
        pair.append(this._el("dt", this._t(key)), this._el("dd", value || this._t("unknown")));
        details.append(pair);
      }
      const actions = this._el("div", undefined, "actions");
      const choices = this._view === "rules" ? [["unignore", "panel_unignore"]] : [
        ["dismiss", "dismiss_finding"],
        ...(item.can_ignore_device ? [["device", "ignore_device"]] : []),
        ...(item.can_ignore_type ? [["device_type", "ignore_device_type"]] : []),
      ];
      for (const [action, text] of choices) {
        const button = this._button(this._t(text), () => this._confirm(action, item),
          action.startsWith("device") ? "ignore" : "");
        button._actionKey = `${item.id}:${action}`;
        button.disabled = this._busy;
        actions.append(button);
      }
      card.append(details, actions);
      list.append(card);
    }
    if (activeKey) {
      [...list.querySelectorAll("button")].find((button) => button._actionKey === activeKey)?.focus();
    }
  }

  _formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return this._t("unknown");
    return date.toLocaleString(this._language || "en");
  }

  _confirm(action, item) {
    if (this._busy) return;
    const keys = { dismiss: "dismiss_finding", device: "ignore_device", device_type: "ignore_device_type", unignore: "panel_unignore" };
    this._selection = { action, item };
    this._q("confirm-title").textContent = item.name || this._t("finding");
    this._q("confirm-description").textContent = this._t(`panel_confirm_${action}`);
    this._q("confirm").textContent = this._t(keys[action]);
    this._q("cancel").textContent = this._t("panel_cancel");
    this.shadowRoot.querySelector("dialog").showModal();
    this._q("cancel").focus();
  }

  async _act(action, item) {
    if (this._busy || !this._snapshot?.available) return;
    this._busy = true;
    this._error = "";
    this._status = "";
    this._render();
    try {
      await this._hass.callWS({ type: "discovery_monitor/action", action, target: item.id });
      this._status = this._t("panel_saved");
    } catch (error) {
      this._error = this._t(error?.code === "stale_target" ? "panel_stale" : "panel_action_failed");
    } finally {
      this._busy = false;
      if (this.isConnected) this._render();
    }
  }
}

if (!customElements.get("discovery-monitor-panel")) {
  customElements.define("discovery-monitor-panel", DiscoveryMonitorPanel);
}
