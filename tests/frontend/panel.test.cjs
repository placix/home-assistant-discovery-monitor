const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "../../custom_components/discovery_monitor");
const translations = Object.fromEntries(["en", "de"].map((language) => [
  language, JSON.parse(readFileSync(path.join(root, "translations", `${language}.json`), "utf8")).common,
]));
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function openPanel(t, language = "en", width = 1100) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  await page.setContent('<style>body{margin:0;--primary-text-color:#222;--secondary-text-color:#666;--primary-background-color:#fafafa;--primary-color:#0288d1}</style>');
  await page.addScriptTag({ path: path.join(root, "frontend/panel.js") });
  await page.evaluate(({ language, translations }) => {
    window.calls = [];
    window.unsubscribed = 0;
    window.listeners = new Map();
    window.snapshot = {
      available: true,
      findings: [{ id: "opaque-reference", name: "Living room", integration: "example", source: "dhcp",
        count: 2, last_seen: "2026-10-18T12:00:00+00:00", can_ignore_device: true, can_ignore_type: true }],
      rules: [], texts: translations[language === "de" ? "de" : "en"],
    };
    window.connection = {
      addEventListener: (name, handler) => window.listeners.set(name, handler),
      removeEventListener: (name) => window.listeners.delete(name),
      subscribeMessage: async (handler, message) => {
        window.pushSnapshot = handler;
        window.snapshot.texts = translations[message.language === "de" ? "de" : "en"];
        handler(structuredClone(window.snapshot));
        return async () => { window.unsubscribed++; };
      },
    };
    window.panel = document.createElement("discovery-monitor-panel");
    window.panel.hass = {
      locale: { language }, user: { is_admin: true }, connection: window.connection,
      callWS: async (message) => { window.calls.push(message); },
    };
    document.body.append(window.panel);
  }, { language, translations });
  await page.locator("#items li").waitFor();
  return page;
}

test("live list, safe text rendering, search and mobile layout", async (t) => {
  const page = await openPanel(t, "en", 390);
  assert.equal(await page.locator("#title").innerText(), "Open discoveries · 1");
  assert.equal(await page.locator("#items button").count(), 3);
  assert.equal(await page.locator("#items").innerText().then((text) => text.includes("opaque-reference")), false);
  await page.locator("#search").fill("absent");
  assert.equal(await page.locator("#items li").count(), 0);
  assert.equal(await page.locator("#empty").innerText(), "No matching entries.");
  await page.locator("#search").fill("dhcp");
  assert.equal(await page.locator("#items li").count(), 1);
  await page.evaluate(() => {
    window.snapshot.findings[0].name = '<img src=x onerror="alert(1)">';
    window.snapshot.findings[0].count = 3;
    window.snapshot.findings[0].can_ignore_device = false;
    window.snapshot.findings[0].can_ignore_type = false;
    window.pushSnapshot(structuredClone(window.snapshot));
  });
  assert.equal(await page.locator("#items img").count(), 0);
  assert.match(await page.locator("#items h2").innerText(), /<img/);
  assert.equal(await page.locator("#items button").count(), 1);
  assert.equal(await page.locator("#items dd").nth(2).innerText(), "3");
  assert.equal(await page.evaluate(() => window.panel.scrollWidth <= window.innerWidth), true);
});

for (const operation of ["dismiss", "device", "device_type"]) {
  test(`confirmation, cancel and ${operation} action`, async (t) => {
    const page = await openPanel(t);
    const index = ["dismiss", "device", "device_type"].indexOf(operation);
    await page.locator("#items button").nth(index).click();
    assert.equal(await page.locator("dialog").isVisible(), true);
    assert.equal(await page.locator("#confirm-title").innerText(), "Living room");
    assert.deepEqual(await page.evaluate(() => window.calls), []);
    await page.locator("#cancel").click();
    assert.deepEqual(await page.evaluate(() => window.calls), []);
    await page.locator("#items button").nth(index).click();
    await page.locator("#confirm").click();
    assert.deepEqual(await page.evaluate(() => window.calls), [{
      type: "discovery_monitor/action", action: operation, target: "opaque-reference",
    }]);
    await page.evaluate(() => {
      window.snapshot.findings = [];
      window.pushSnapshot(structuredClone(window.snapshot));
    });
    assert.equal(await page.locator("#title").innerText(), "Open discoveries · 0");
    assert.equal(await page.locator("#empty").innerText(), "No open discoveries.");
  });
}

test("German labels, ignore-rule removal and fallback after language change", async (t) => {
  const page = await openPanel(t, "de");
  assert.equal(await page.locator("#title").innerText(), "Offene Funde · 1");
  assert.equal(await page.locator("#items button").nth(1).innerText(), "Dieses Gerät zukünftig ignorieren");
  await page.evaluate(() => {
    window.snapshot.rules = [{ id: "rule-ref", name: "Model", kind: "device_type", integration: "example" }];
    window.pushSnapshot(structuredClone(window.snapshot));
  });
  await page.locator("#views button").nth(1).click();
  assert.equal(await page.locator("#items dd").first().innerText(), "Gerätetyp");
  await page.locator("#items button").click();
  await page.locator("#confirm").click();
  assert.equal((await page.evaluate(() => window.calls))[0].action, "unignore");
  await page.evaluate(() => {
    window.snapshot.rules = [];
    window.pushSnapshot(structuredClone(window.snapshot));
  });
  assert.equal(await page.locator("#empty").innerText(), "Keine ignorierten Geräte oder Gerätetypen.");
  await page.evaluate(() => { window.panel.hass = { ...window.panel._hass, locale: { language: "fr" } }; });
  assert.equal(await page.locator("#title").innerText(), "Open discoveries · 1");
  assert.equal(await page.evaluate(() => window.unsubscribed), 1);
});

test("stale actions, connection loss, retry and unsubscribe on removal", async (t) => {
  const page = await openPanel(t);
  await page.evaluate(() => { window.panel._hass.callWS = async () => { throw { code: "stale_target" }; }; });
  await page.locator("#items button").first().click();
  await page.locator("#confirm").click();
  assert.match(await page.locator("#error").innerText(), /no longer available/);
  await page.evaluate(() => window.listeners.get("disconnected")());
  assert.equal(await page.locator("#items li").count(), 0);
  assert.match(await page.locator("#error").innerText(), /Connection unavailable/);
  await page.locator("#retry").click();
  assert.equal(await page.locator("#items li").count(), 1);
  await page.evaluate(() => window.pushSnapshot({ available: false, texts: window.snapshot.texts }));
  assert.equal(await page.locator("#items li").count(), 0);
  assert.match(await page.locator("#error").innerText(), /not loaded/);
  await page.evaluate(() => window.panel.remove());
  assert.equal(await page.evaluate(() => window.unsubscribed), 2);
  assert.equal(await page.evaluate(() => window.listeners.size), 0);
});

test("non-administrators get no controls or subscription", async (t) => {
  const page = await openPanel(t);
  await page.evaluate(() => { window.panel.hass = { ...window.panel._hass, user: { is_admin: false } }; });
  assert.equal(await page.locator("#items button").count(), 0);
  assert.equal(await page.locator("#retry").isVisible(), false);
  assert.match(await page.locator("#error").innerText(), /administrators/);
  assert.equal(await page.evaluate(() => window.listeners.size), 0);
});
