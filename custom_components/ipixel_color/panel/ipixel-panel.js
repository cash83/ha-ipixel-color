/* iPixel Panel — tema "Neon a piastrelle"
 * Display LED simulato dal vivo in cima + pagine: Testo, Orologio,
 * Immagine, Colori, Sistema. Comunica con HA via hass.callService /
 * hass.callWS / hass.states.
 */

const PAGES = [
  { id: "testo", label: "Testo", icon: "✍️" },
  { id: "disegno", label: "Disegno", icon: "🖌️" },
  { id: "timer", label: "Timer", icon: "⏱️" },
  { id: "punti", label: "Segnapunti", icon: "🏆" },
  { id: "colori", label: "Colori", icon: "🎨" },
  { id: "sistema", label: "Sistema", icon: "⚙️" },
];

// Numerazione vera del firmware (ricerca ahzs645): 3 e 4 (scorri su/giù)
// mandano in BOOTLOOP i pannelli non 32x32 e sono bloccate anche dal server
const ANIMATIONS = [
  { v: 0, label: "Fisso" },
  { v: 1, label: "Scorri ←" },
  { v: 2, label: "Scorri →" },
  { v: 5, label: "Lampeggio" },
  { v: 6, label: "Brezza" },
  { v: 7, label: "Neve" },
  { v: 8, label: "Laser" },
];

/* Font 5x7 per la simulazione LED */
const GLYPHS = {
  A:["01110","10001","10001","11111","10001","10001","10001"],
  B:["11110","10001","10001","11110","10001","10001","11110"],
  C:["01111","10000","10000","10000","10000","10000","01111"],
  D:["11110","10001","10001","10001","10001","10001","11110"],
  E:["11111","10000","10000","11110","10000","10000","11111"],
  F:["11111","10000","10000","11110","10000","10000","10000"],
  G:["01111","10000","10000","10111","10001","10001","01110"],
  H:["10001","10001","10001","11111","10001","10001","10001"],
  I:["11111","00100","00100","00100","00100","00100","11111"],
  J:["00111","00010","00010","00010","00010","10010","01100"],
  K:["10001","10010","10100","11000","10100","10010","10001"],
  L:["10000","10000","10000","10000","10000","10000","11111"],
  M:["10001","11011","10101","10101","10001","10001","10001"],
  N:["10001","11001","10101","10011","10001","10001","10001"],
  O:["01110","10001","10001","10001","10001","10001","01110"],
  P:["11110","10001","10001","11110","10000","10000","10000"],
  Q:["01110","10001","10001","10001","10101","10010","01101"],
  R:["11110","10001","10001","11110","10100","10010","10001"],
  S:["01111","10000","10000","01110","00001","00001","11110"],
  T:["11111","00100","00100","00100","00100","00100","00100"],
  U:["10001","10001","10001","10001","10001","10001","01110"],
  V:["10001","10001","10001","10001","10001","01010","00100"],
  W:["10001","10001","10001","10101","10101","11011","10001"],
  X:["10001","01010","00100","00100","00100","01010","10001"],
  Y:["10001","01010","00100","00100","00100","00100","00100"],
  Z:["11111","00001","00010","00100","01000","10000","11111"],
  0:["01110","10001","10011","10101","11001","10001","01110"],
  1:["00100","01100","00100","00100","00100","00100","01110"],
  2:["01110","10001","00001","00010","00100","01000","11111"],
  3:["11110","00001","00001","01110","00001","00001","11110"],
  4:["00010","00110","01010","10010","11111","00010","00010"],
  5:["11111","10000","10000","11110","00001","00001","11110"],
  6:["01110","10000","10000","11110","10001","10001","01110"],
  7:["11111","00001","00010","00100","01000","01000","01000"],
  8:["01110","10001","10001","01110","10001","10001","01110"],
  9:["01110","10001","10001","01111","00001","00001","01110"],
  ",":["00000","00000","00000","00000","00000","00110","01100"],
  ".":["00000","00000","00000","00000","00000","01100","01100"],
  ":":["00000","01100","01100","00000","01100","01100","00000"],
  "-":["00000","00000","00000","01110","00000","00000","00000"],
  "%":["11001","11010","00010","00100","01000","01011","10011"],
  "/":["00001","00010","00010","00100","01000","01000","10000"],
  "?":["01110","10001","00001","00110","00100","00000","00100"],
  "!":["00100","00100","00100","00100","00100","00000","00100"],
  "+":["00000","00100","00100","11111","00100","00100","00000"],
  " ":["00000","00000","00000","00000","00000","00000","00000"],
};

/* Ripiego: alfabeto 5x7 centrato verticalmente nelle 16 righe */
function fallbackCols(text) {
  const cols = [];
  const clean = (text || "")
    .toUpperCase()
    .replace(/[ÀÁ]/g, "A").replace(/[ÈÉ]/g, "E").replace(/[ÌÍ]/g, "I")
    .replace(/[ÒÓ]/g, "O").replace(/[ÙÚ]/g, "U").replace(/°/g, "");
  for (const ch of clean) {
    const g = GLYPHS[ch] || GLYPHS["?"];
    for (let x = 0; x < 5; x++) {
      let col = 0;
      for (let y = 0; y < 7; y++) if (g[y][x] === "1") col |= 1 << (y + 4);
      cols.push(col);
    }
    cols.push(0);
  }
  return cols;
}

class IPixelPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._page = "testo";
    this._deviceIndex = 0;
    this._rendered = false;
    this._busy = false;
    this._resizeMethod = "crop";
    this._items = null;       // composizione (null = da caricare)
    this._editIndex = null;   // piastrella in modifica
    this._addMode = null;     // "entity" | "text" | null
    this._simText = "";
    this._simCols = [];
    this._simOffset = 0;
    this._simLast = 0;
    this._simScroll = true;
  }

  connectedCallback() { this._startSim(); }
  disconnectedCallback() {
    this._simRunning = false;
    if (this._frameTimer) { clearInterval(this._frameTimer); this._frameTimer = null; }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._rendered) { this._renderShell(); this._rendered = true; }
    this._refreshValues();
  }

  set panel(panel) { this._config = (panel && panel.config) || {}; }

  get _devices() { return (this._config && this._config.devices) || []; }
  get _dev() { return this._devices[this._deviceIndex] || null; }

  _eid(domain, suffix) {
    if (!this._dev) return null;
    return suffix ? `${domain}.${this._dev.slug}_${suffix}` : `${domain}.${this._dev.slug}`;
  }
  _state(domain, suffix) {
    const id = this._eid(domain, suffix);
    return id && this._hass ? this._hass.states[id] : undefined;
  }
  _val(domain, suffix, fallback = "") {
    const s = this._state(domain, suffix);
    return s ? s.state : fallback;
  }

  async _call(domain, service, data) {
    if (!this._hass) return;
    this._setBusy(true);
    try {
      await this._hass.callService(domain, service, data);
      this._toast("Fatto ✓");
    } catch (e) {
      this._toast("Errore: " + (e.message || e), true);
    } finally { this._setBusy(false); }
  }

  _setBusy(b) {
    this._busy = b;
    const el = this.shadowRoot.getElementById("busy");
    if (el) el.style.visibility = b ? "visible" : "hidden";
  }

  _authToken() {
    // Token per chiamare un endpoint HTTP autenticato dell'integrazione
    try {
      const a = this._hass && this._hass.auth;
      if (!a) return null;
      return (a.data && a.data.access_token) || a.accessToken || null;
    } catch (e) { return null; }
  }

  _toast(msg, isErr) {
    const t = this.shadowRoot.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.className = isErr ? "toast err show" : "toast show";
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => (t.className = "toast"), 3000);
  }

  /* ================================================== shell + tema */

  _renderShell() {
    const style = `
      :host { display:block; height:100vh; overflow:hidden;
        --bg:#05060a; --card:#0b0f1a; --card2:#0e1526; --line:#14324a;
        --text:#cdeeff; --muted:#5f7f99; --neon:#19e5ff; --neon2:#8b5cf6;
        font-family: Roboto, system-ui, sans-serif; color:var(--text); background:var(--bg); }
      * { box-sizing:border-box; }
      .layout { display:flex; height:100vh; }
      nav { width:190px; min-width:150px; background:#070a12;
        border-right:1px solid var(--line); padding-top:12px; z-index:2; }
      nav h1 { font-size:17px; margin:4px 16px 14px; font-weight:600; color:#fff;
        text-shadow:0 0 12px var(--neon); letter-spacing:1px; }
      nav h1 em { font-style:normal; color:var(--neon); }
      nav button { display:flex; align-items:center; gap:10px; width:100%;
        padding:12px 16px; background:none; border:none; color:var(--muted);
        font-size:14px; cursor:pointer; text-align:left; border-left:3px solid transparent; }
      nav button:hover { color:var(--text); background:#0ff1; }
      nav button.active { border-left-color:var(--neon); color:var(--neon);
        background:linear-gradient(90deg,#0ff2,transparent); text-shadow:0 0 10px #0ff8; }
      main { flex:1; overflow-y:auto; padding:0 20px 70px; }
      .screenbar { position:sticky; top:0; z-index:1; background:#000;
        margin:0 -20px 16px; padding:14px 22px 4px; border-bottom:1px solid #0ff3;
        box-shadow:0 8px 30px #000c; }
      canvas.led { width:100%; max-width:720px; display:block; margin:0 auto; }
      img.led { width:100%; max-width:720px; display:none; margin:0 auto;
        image-rendering:pixelated; }
      .screenlabel { color:#0cf; font-size:9px; letter-spacing:3px; text-transform:uppercase;
        text-align:center; padding:6px 0 8px; opacity:.6; }
      .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
        padding:16px 18px; margin-bottom:14px; max-width:760px;
        box-shadow:0 0 12px #0ff1 inset; }
      .card h2 { font-size:11px; margin:0 0 12px; font-weight:600; color:#6ee7ff99;
        text-transform:uppercase; letter-spacing:2px;
        cursor:pointer; user-select:none; display:flex; align-items:center;
        justify-content:space-between; gap:8px; }
      .card h2::after { content:"▾"; font-size:13px; opacity:.6;
        letter-spacing:0; transition:transform .2s; }
      .card.collapsed h2 { margin-bottom:0; }
      .card.collapsed h2::after { transform:rotate(-90deg); }
      .card.collapsed > *:not(h2) { display:none !important; }
      .row { display:flex; align-items:center; gap:12px; margin:10px 0; flex-wrap:wrap; }
      .row label { min-width:110px; font-size:13px; color:var(--muted); }
      textarea, input[type=text], select { flex:1; min-width:180px; background:#060a12;
        color:var(--text); border:1px solid var(--line); border-radius:9px;
        padding:9px 11px; font-size:14px; }
      textarea { min-height:80px; resize:vertical; font-family:monospace; }
      input[type=range] { flex:1; accent-color:var(--neon); }
      input[type=color] { width:52px; height:36px; border:none; background:none; cursor:pointer; }
      .num { width:40px; text-align:right; color:var(--neon); font-variant-numeric:tabular-nums; }
      .btn { background:linear-gradient(90deg,#06b6d4,var(--neon2)); color:#fff; border:none;
        border-radius:11px; padding:12px 20px; font-size:15px; cursor:pointer;
        box-shadow:0 0 20px #06b6d455; }
      .btn.sec { background:transparent; color:var(--neon); border:1px solid var(--neon);
        box-shadow:none; }
      .btn:disabled { opacity:.4; cursor:default; }
      .chips { display:flex; gap:8px; flex-wrap:wrap; flex:1; }
      .chip { padding:7px 14px; border-radius:15px; border:1px solid #0ff5;
        background:none; color:#9beeff; cursor:pointer; font-size:13px; }
      .chip.active { background:var(--neon); border-color:var(--neon); color:#001318;
        box-shadow:0 0 14px #0ff8; }
      .switch { position:relative; width:44px; height:24px; flex:none; min-width:44px !important; }
      .switch input { opacity:0; width:0; height:0; }
      .sl { position:absolute; inset:0; background:#13203a; border:1px solid var(--line);
        border-radius:12px; transition:.2s; cursor:pointer; }
      .sl:before { content:""; position:absolute; width:16px; height:16px; left:3px; top:3px;
        border-radius:50%; background:#5f7f99; transition:.2s; }
      .switch input:checked + .sl { background:#063a4a; border-color:var(--neon);
        box-shadow:0 0 10px #0ff5; }
      .switch input:checked + .sl:before { transform:translateX(20px); background:var(--neon);
        box-shadow:0 0 8px #0ff; }
      /* piastrelle */
      .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; }
      .tile { background:var(--card2); border:1px solid var(--line); border-radius:14px;
        padding:12px; display:flex; flex-direction:column; gap:5px; cursor:pointer;
        transition:transform .12s, box-shadow .15s, border-color .15s; position:relative; }
      .tile:hover { transform:translateY(-2px); }
      .tile.sel { border-color:var(--neon); box-shadow:0 0 16px #0ff4, 0 0 10px #0ff2 inset; }
      .tile .ic { width:34px; height:34px; border-radius:50%; display:grid; place-items:center;
        font-size:17px; background:#0e1a2e; }
      .tile.sel .ic { background:radial-gradient(circle at 35% 35%, #0ff3, #06364a); }
      .tile b { font-size:13px; color:#cdeeff; overflow:hidden; text-overflow:ellipsis;
        white-space:nowrap; font-weight:600; }
      .tile .v { font-size:20px; color:var(--neon); text-shadow:0 0 10px #0ff6;
        font-variant-numeric:tabular-nums; font-weight:600; }
      .tile:not(.sel) .v { color:#39506a; text-shadow:none; }
      .tile { user-select:none; -webkit-user-select:none; }
      .tile .drag { position:absolute; top:6px; left:8px; color:#39506a; cursor:grab;
        font-size:15px; touch-action:none; padding:2px 5px; }
      .tile .drag:hover { color:var(--neon); }
      .tile.dragging { opacity:.3; border-style:dashed; border-color:var(--neon);
        box-shadow:none; transform:none; }
      .tile.ghost { position:fixed; z-index:99; margin:0; pointer-events:none;
        transform:scale(1.07) rotate(1.5deg); transition:none; opacity:.95;
        border-color:var(--neon); box-shadow:0 14px 34px #000d, 0 0 20px #0ff6; }
      .tile .edit { position:absolute; top:8px; right:8px; background:none; border:none;
        color:var(--muted); cursor:pointer; font-size:13px; padding:3px; }
      .tile .edit:hover { color:var(--neon); }
      .tile.addtile { border-style:dashed; align-items:center; justify-content:center;
        color:var(--muted); min-height:96px; font-size:13px; gap:6px; }
      .tile.addtile:hover { color:var(--neon); border-color:var(--neon); }
      .tile.addtile .plus { font-size:24px; line-height:1; }
      #editor-slot:not(:empty) { position:fixed; inset:0; z-index:1000;
        background:rgba(2,8,18,.72); display:flex; align-items:flex-start;
        justify-content:center; padding:24px 12px; overflow:auto; }
      .editor { background:#0a1322; border:1px solid var(--neon); border-radius:12px;
        padding:14px 16px; box-shadow:0 12px 44px #000c, 0 0 16px #0ff3;
        width:100%; max-width:560px; position:relative; }
      .ed-x { position:absolute; top:8px; right:8px; width:30px; height:30px;
        line-height:28px; padding:0; border-radius:50%; font-size:17px;
        background:#0a1322; border:1px solid var(--line); color:var(--fg);
        cursor:pointer; z-index:2; }
      .ed-x:hover { border-color:#ff4d6d; color:#ff4d6d; }
      .gal-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:10px; }
      .gal-tile { background:#000; border:1px solid var(--line); border-radius:10px;
        padding:6px; cursor:pointer; transition:transform .12s, box-shadow .15s; }
      .gal-tile:hover { transform:scale(1.04); border-color:var(--neon); box-shadow:0 0 14px #0ff4; }
      .gal-tile img { width:100%; display:block; image-rendering:pixelated; border-radius:6px; }
      .drop { border:2px dashed #0ff5; border-radius:14px; padding:28px; text-align:center;
        color:var(--muted); cursor:pointer; }
      .drop.over { border-color:var(--neon); color:var(--neon); box-shadow:0 0 18px #0ff4; }
      .preview { image-rendering:pixelated; border:1px solid var(--line); border-radius:8px;
        background:#000; }
      .swatches { display:flex; gap:8px; flex-wrap:wrap; }
      .sw { width:34px; height:34px; border-radius:50%; border:2px solid #0003; cursor:pointer; }
      .sw:hover { transform:scale(1.12); }
      .info-grid { display:grid; grid-template-columns:140px 1fr; gap:6px 16px; font-size:13px; }
      .info-grid dt { color:var(--muted); } .info-grid dd { margin:0; color:var(--text); }
      details summary { cursor:pointer; color:var(--muted); font-size:13px; margin-bottom:8px; }
      .toast { position:fixed; bottom:18px; left:50%; transform:translateX(-50%);
        background:#063a4a; border:1px solid var(--neon); color:#cdeeff; padding:10px 22px;
        border-radius:20px; opacity:0; transition:opacity .25s; pointer-events:none;
        font-size:13px; z-index:9; box-shadow:0 0 18px #0ff5; }
      .toast.show { opacity:1; }
      .toast.err { background:#3a0a12; border-color:#ff4d6d; box-shadow:0 0 18px #ff4d6d55; }
      #busy { visibility:hidden; width:13px; height:13px; border:2px solid var(--neon);
        border-top-color:transparent; border-radius:50%; display:inline-block;
        animation:spin .8s linear infinite; margin-left:8px; }
      @keyframes spin { to { transform:rotate(360deg); } }
      .devsel { margin:0 16px 10px; width:calc(100% - 32px); }
      @media (max-width: 680px) {
        .layout { flex-direction:column; }
        nav { width:100%; display:flex; overflow-x:auto; padding:0; border-right:none;
          border-bottom:1px solid var(--line); }
        nav h1, .devsel { display:none; }
        nav button { width:auto; border-left:none; border-bottom:3px solid transparent;
          flex:none; }
        nav button.active { border-bottom-color:var(--neon);
          background:linear-gradient(0deg,#0ff2,transparent); }
      }
    `;

    const nav = PAGES.map(
      (p) => `<button data-page="${p.id}" class="${p.id === this._page ? "active" : ""}">
        <span>${p.icon}</span>${p.label}</button>`
    ).join("");
    const devOptions = this._devices
      .map((d, i) => `<option value="${i}">${d.name}</option>`).join("");

    this.shadowRoot.innerHTML = `
      <style>${style}</style>
      <div class="layout">
        <nav>
          <h1>i<em>Pixel</em> <span id="busy"></span></h1>
          ${this._devices.length > 1 ? `<select class="devsel" id="devsel">${devOptions}</select>` : ""}
          ${nav}
        </nav>
        <main id="mainwrap">
          <div class="screenbar">
            <canvas class="led" id="sim" width="384" height="96"></canvas>
            <img class="led" id="simgif" alt="">
            <div class="screenlabel" id="simlabel">● anteprima dal vivo</div>
          </div>
          <div id="main"></div>
        </main>
      </div>
      <div class="toast" id="toast"></div>
    `;

    this.shadowRoot.querySelectorAll("nav button").forEach((b) =>
      b.addEventListener("click", () => {
        this._page = b.dataset.page;
        this.shadowRoot.querySelectorAll("nav button")
          .forEach((x) => x.classList.toggle("active", x === b));
        this._renderPage();
      })
    );
    const ds = this.shadowRoot.getElementById("devsel");
    if (ds) ds.addEventListener("change", () => {
      this._deviceIndex = parseInt(ds.value, 10);
      this._items = null;
      this._renderPage();
    });

    this._renderPage();
    if (this._items === null) this._loadComposition();
    this._startSim();
  }

  /* ============================================= simulazione LED */

  async _ensureFont(fileName) {
    if (!fileName) return null;
    this._loadedFonts = this._loadedFonts || {};
    const family = "ipx_" + fileName.replace(/[^a-z0-9]/gi, "_");
    if (this._loadedFonts[family]) return family;
    try {
      const ff = new FontFace(
        family, `url(/ipixel_color_fonts/${encodeURIComponent(fileName)})`
      );
      await ff.load();
      document.fonts.add(ff);
      this._loadedFonts[family] = true;
      return family;
    } catch (e) {
      return null;
    }
  }

  /* Disegna il testo col FONT VERO e lo campiona sui 16 LED di altezza */
  async _rasterize(text) {
    const fontFile = this._val("select", "font", "");
    const family = await this._ensureFont(fontFile);
    if (!family) return fallbackCols(text);
    let hpx = parseInt(parseFloat(this._val("number", "font_size", "0")), 10) || 0;
    const { h: H } = this._panelSize();
    if (hpx <= 0 || hpx > H) hpx = H;
    const cv = document.createElement("canvas");
    cv.width = Math.max(32, Math.ceil(text.length * hpx * 1.2) + 8);
    cv.height = H;
    const ctx = cv.getContext("2d", { willReadFrequently: true });
    // Le emoji non stanno nel TTF del pannello: fallback al font
    // emoji di sistema, così nell'anteprima si vedono davvero
    ctx.font = `${hpx}px "${family}", "Segoe UI Emoji", "Noto Color Emoji", sans-serif`;
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#fff";
    ctx.fillText(text, 0, Math.floor(H / 2));
    const width = Math.min(
      cv.width, Math.ceil(ctx.measureText(text).width) + 2
    );
    const img = ctx.getImageData(0, 0, Math.max(1, width), H).data;
    const cols = [];
    for (let x = 0; x < width; x++) {
      let col = 0;
      for (let y = 0; y < H; y++) {
        if (img[(y * width + x) * 4 + 3] > 100) col |= 1 << y;
      }
      cols.push(col);
    }
    return cols;
  }

  _setSimText(text, scroll = true) {
    const fontKey =
      this._val("select", "font", "") + "|" + this._val("number", "font_size", "0");
    if (text === this._simText && scroll === this._simScroll &&
        fontKey === this._simFontKey) return;
    // Se cambia solo il TESTO (un sensore ha aggiornato il valore),
    // lo scorrimento non riparte da capo: il display vero intanto
    // continua a scorrere, l'anteprima deve fare lo stesso
    const keepOffset = scroll && this._simScroll && fontKey === this._simFontKey;
    this._simText = text;
    this._simScroll = scroll;
    this._simFontKey = fontKey;
    const token = (this._simToken = (this._simToken || 0) + 1);
    this._rasterize(scroll ? text + "   " : text).then((cols) => {
      if (token !== this._simToken) return; // nel frattempo è cambiato
      this._simCols = cols;
      this._simOffset = keepOffset
        ? this._simOffset % Math.max(1, cols.length) : 0;
    });
  }

  _startSim() {
    if (this._simRunning) return;
    this._simRunning = true;
    const step = (ts) => {
      if (!this._simRunning) return;
      const cv = this.shadowRoot && this.shadowRoot.getElementById("sim");
      if (cv) this._drawSim(cv, ts);
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    // Anteprima FEDELE: chiede al server l'ultima cosa davvero mandata
    // al display e la mostra al posto dell'emulazione, quando c'è
    if (!this._frameTimer)
      this._frameTimer = setInterval(() => this._pollFrame(), 1000);
  }

  async _pollFrame() {
    if (!this._hass || !this._dev || this._framePending) return;
    this._framePending = true;
    try {
      const res = await this._hass.callWS({
        type: "ipixel_color/frame", address: this._dev.address });
      this._applyLiveFrame(res);
    } catch (e) { /* server vecchio: resta l'emulazione locale */ }
    finally { this._framePending = false; }
  }

  _applyLiveFrame(res) {
    const cv = this.shadowRoot && this.shadowRoot.getElementById("sim");
    const gif = this.shadowRoot && this.shadowRoot.getElementById("simgif");
    if (!cv || !gif) return;
    const showGif = (on) => {
      gif.style.display = on ? "block" : "none";
      cv.style.display = on ? "none" : "block";
    };

    if (res && res.kind === "strip" && res.b64) {
      // Striscia della composizione resa dal server: stessi pixel del
      // display, la facciamo scorrere pixel-per-pixel (anteprima FEDELE)
      this._liveKind = "strip";
      showGif(false);
      const src = `data:image/${res.ext || "png"};base64,${res.b64}`;
      if (this._simStripSrc !== src) {
        this._simStripSrc = src;
        const im = new Image();
        im.onload = () => {
          if (this._simStripSrc !== src) return;
          const sw = im.width, sh = im.height;
          const oc = document.createElement("canvas");
          oc.width = sw; oc.height = sh;
          const octx = oc.getContext("2d");
          octx.drawImage(im, 0, 0);
          this._simStrip = octx.getImageData(0, 0, sw, sh);
        };
        im.src = src;
      }
      return;
    }

    if (res && res.kind === "image" && res.b64) {
      this._liveKind = "image";
      if (res.ext === "gif") {
        // GIF animata: il canvas la appiattirebbe, meglio l'<img>
        this._simFrame = null;
        const src = `data:image/gif;base64,${res.b64}`;
        if (gif.dataset.src !== src) { gif.src = src; gif.dataset.src = src; }
        showGif(true);
        return;
      }
      showGif(false);
      const src = `data:image/${res.ext || "png"};base64,${res.b64}`;
      if (this._simFrameSrc !== src) {
        this._simFrameSrc = src;
        const im = new Image();
        im.onload = () => {
          if (this._simFrameSrc !== src) return;
          const { w, h } = this._panelSize();
          const oc = document.createElement("canvas");
          oc.width = w; oc.height = h;
          const octx = oc.getContext("2d");
          octx.drawImage(im, 0, 0, w, h);
          this._simFrame = octx.getImageData(0, 0, w, h);
        };
        im.src = src;
      }
      return;
    }

    showGif(false);
    if (res && res.kind === "clock") {
      // Orologio stock del firmware: emulalo con l'ora vera
      this._liveKind = "clock";
      this._simFrame = null;
      const now = new Date();
      const t = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
      this._setSimText(t, false);
      return;
    }
    if (res && res.kind === "text" && res.running && res.text) {
      // Testo mandato da un timer/orologio in moto: quello vero
      this._liveKind = "text";
      this._simFrame = null;
      this._setSimText(res.text, !!res.scroll);
      return;
    }
    // Niente dal server: emulazione locale
    this._simFrame = null;
    this._simFrameSrc = null;
    this._simStrip = null;
    this._simStripSrc = null;
    if (this._liveKind) { this._liveKind = null; this._updateSimFromState(); }
  }

  _simStretch() {
    if (this._stretch) return this._stretch;
    try {
      const v = parseFloat(localStorage.getItem("ipixel_sim_stretch"));
      this._stretch = v >= 1 && v <= 2.5 ? v : 1.4;
    } catch (e) { this._stretch = 1.4; }
    return this._stretch;
  }

  /* Taratura della velocità dell'anteprima (percentuale, 100 = la
     formula dell'emulatore): la velocità vera del firmware non è
     documentata, quindi si regola a occhio in Sistema. */
  _simSpeedPct() {
    if (this._simSpd) return this._simSpd;
    try {
      const v = parseInt(localStorage.getItem("ipixel_sim_speedpct"), 10);
      this._simSpd = v >= 25 && v <= 600 ? v : 300;
    } catch (e) { this._simSpd = 300; }
    return this._simSpd;
  }

  _drawSim(cv, ts) {
    const ctx = cv.getContext("2d");
    const { w: W, h: H } = this._panelSize();
    const stretch = this._simStretch();
    const wantW = Math.round(W * 6 * stretch);
    if (cv.width !== wantW || cv.height !== H * 6) { cv.width = wantW; cv.height = H * 6; }
    const cell = cv.width / W;
    const cellY = cv.height / H;
    const cols = this._simCols;
    const fits = cols.length <= W;
    if (this._simScroll && !fits) {
      // Velocità vera del firmware mai misurata: formula dell'emulatore
      // (500-(v-1)*4.7 ms/px) corretta dalla taratura in Sistema
      const speed = parseInt(this._val("number", "text_speed", "50"), 10) || 0;
      const interval = (500 - (speed - 1) * 4.7) * 100 / this._simSpeedPct();
      if (ts - this._simLast > Math.max(25, interval)) {
        this._simOffset = (this._simOffset + 1) % Math.max(1, cols.length);
        this._simLast = ts;
      }
    } else this._simOffset = 0;

    // Frame vero dal display (orologio italiano, icone, immagini):
    // ogni LED col SUO colore, pixel per pixel
    const fr = this._simFrame;
    if (fr && fr.width === W && fr.height === H) {
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, cv.width, cv.height);
      for (let x = 0; x < W; x++) {
        for (let y = 0; y < H; y++) {
          const i = (y * W + x) * 4;
          const r = fr.data[i], g = fr.data[i + 1], b = fr.data[i + 2];
          if (r + g + b > 30) {
            ctx.fillStyle = `rgb(${r},${g},${b})`;
            ctx.shadowColor = ctx.fillStyle;
            ctx.shadowBlur = 5;
          } else {
            ctx.fillStyle = "#0a1420";
            ctx.shadowBlur = 0;
          }
          ctx.beginPath();
          ctx.arc(x * cell + cell / 2, y * cellY + cellY / 2,
            Math.min(cell, cellY) * 0.32, 0, 7);
          ctx.fill();
        }
      }
      ctx.shadowBlur = 0;
      return;
    }

    // Striscia della composizione resa dal server: scorre pixel-per-pixel
    // (anteprima FEDELE: stessi pixel/colori/posizioni del display)
    const strip = (this._liveKind === "strip") ? this._simStrip : null;
    if (strip && strip.height === H && strip.width > 0) {
      const sw = strip.width;
      const sfits = sw <= W;
      const vw = sw + W;  // gap di uno schermo prima di ripetere
      if (!sfits) {
        const speed = parseInt(this._val("number", "text_speed", "50"), 10) || 0;
        const interval = (500 - (speed - 1) * 4.7) * 100 / this._simSpeedPct();
        if (ts - this._simLast > Math.max(25, interval)) {
          this._simStripOffset = ((this._simStripOffset || 0) + 1) % vw;
          this._simLast = ts;
        }
      } else this._simStripOffset = 0;
      const off = sfits ? 0 : (this._simStripOffset || 0);
      const cStart = sfits ? Math.floor((W - sw) / 2) : 0;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, cv.width, cv.height);
      for (let x = 0; x < W; x++) {
        const sx = sfits ? (x - cStart) : ((off + x) % vw);
        for (let y = 0; y < H; y++) {
          let r = 0, g = 0, b = 0;
          if (sx >= 0 && sx < sw) {
            const i = (y * sw + sx) * 4;
            r = strip.data[i]; g = strip.data[i + 1]; b = strip.data[i + 2];
          }
          if (r + g + b > 30) {
            ctx.fillStyle = `rgb(${r},${g},${b})`;
            ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 5;
          } else { ctx.fillStyle = "#0a1420"; ctx.shadowBlur = 0; }
          ctx.beginPath();
          ctx.arc(x * cell + cell / 2, y * cellY + cellY / 2,
            Math.min(cell, cellY) * 0.32, 0, 7);
          ctx.fill();
        }
      }
      ctx.shadowBlur = 0;
      return;
    }

    const tcState = this._state("light", "text_color");
    const rgb = (tcState && tcState.attributes && tcState.attributes.rgb_color) || [25, 229, 255];
    const onColor = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;

    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, cv.width, cv.height);
    const startX = fits ? Math.floor((W - cols.length) / 2) : 0;
    for (let x = 0; x < W; x++) {
      let col = 0;
      if (fits) {
        const idx = x - startX;
        if (idx >= 0 && idx < cols.length) col = cols[idx];
      } else {
        col = cols.length ? cols[(x + this._simOffset) % cols.length] : 0;
      }
      for (let y = 0; y < H; y++) {
        const lit = (col >> y) & 1;
        ctx.fillStyle = lit ? onColor : "#0a1420";
        if (lit) { ctx.shadowColor = onColor; ctx.shadowBlur = 5; } else ctx.shadowBlur = 0;
        ctx.beginPath();
        ctx.arc(x * cell + cell / 2, y * cellY + cellY / 2, Math.min(cell, cellY) * 0.32, 0, 7);
        ctx.fill();
      }
    }
    ctx.shadowBlur = 0;
  }

  /* La stessa icona automatica che il server mette sul display
     (composition.py default_icon_for): tienile allineate */
  _autoIcon(st, eid) {
    const BY_CLASS = { temperature: "🌡", humidity: "💧", power: "⚡",
      energy: "⚡", voltage: "⚡", current: "⚡", battery: "🔋",
      illuminance: "☀", pm25: "🌫", co2: "🌫", carbon_dioxide: "🌫" };
    const BY_DOMAIN = { light: "💡", switch: "🔌", person: "🧍",
      device_tracker: "🧍", climate: "🌡", fan: "🌀",
      media_player: "🎵", cover: "🪟", lock: "🔒" };
    const dc = st && st.attributes && st.attributes.device_class;
    if (dc && BY_CLASS[dc]) return BY_CLASS[dc];
    const unit = (st && st.attributes && st.attributes.unit_of_measurement) || "";
    if (unit.includes("°")) return "🌡";
    if (unit === "%") return "💧";
    if (["W", "KW", "WH", "KWH", "V", "A"].includes(unit.toUpperCase())) return "⚡";
    return BY_DOMAIN[(eid || "").split(".")[0]] || "";
  }

  _updateSimFromState() {
    // Se l'anteprima sta rispecchiando il display vero, non toccarla
    if (this._liveKind) return;
    if (this._page === "orologio") {
      const now = new Date();
      const h24 = this._val("switch", "clock_24h", "on") === "on";
      let hh = now.getHours();
      if (!h24) hh = hh % 12 || 12;
      const t = `${String(hh).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
      this._setSimText(t, false);
      return;
    }
    // testo composto (o entità testo come ripiego)
    const parts = [];
    for (const it of this._items || []) {
      if (it.enabled === false) continue;
      if (it.type === "text") { if ((it.text || "").trim()) parts.push({ s: it.text.trim(), gap: it.gap }); continue; }
      if (!it.entity_id) continue;
      const st = this._hass && this._hass.states[it.entity_id];
      let v = "?";
      if (st && !["unknown", "unavailable", ""].includes(st.state)) {
        v = st.state;
        const f = parseFloat(v);
        if (!isNaN(f)) v = f.toFixed(it.decimals == null ? 1 : it.decimals).replace(".", ",");
      }
      const ic = (it.icon == null ? this._autoIcon(st, it.entity_id) : it.icon).trim();
      let base = ((it.label || "").trim() + " " + v).trim();
      if (it.show_unit && st) {
        // Come il server: l'unità senza il simbolo dei gradi
        const u = ((st.attributes && st.attributes.unit_of_measurement) || "")
          .replace(/°/g, "").trim();
        if (u) base += u;
      }
      const ig = it.icon_gap == null ? 1 : Math.max(0, it.icon_gap);
      parts.push({ s: ic ? ic + " ".repeat(ig) + base : base, gap: it.gap });
    }
    const dsep = this._sep || 2;
    let text = "";
    parts.forEach((p, i) => {
      text += p.s;
      if (i < parts.length - 1)
        text += " ".repeat(p.gap == null ? dsep : Math.max(0, p.gap));
    });
    if (!text) text = this._val("text", "display") || "IPIXEL";
    if (text.includes("{")) text = "TEMPLATE";
    this._setSimText(text, true);
  }

  /* ===================================================== pagine */

  _renderPage() {
    const main = this.shadowRoot.getElementById("main");
    if (!main) return;
    if (!this._dev) {
      main.innerHTML = `<div class="card"><h2>Nessun display</h2>
        <p>Nessun display iPIXEL configurato.</p></div>`;
      return;
    }
    this._editIndex = null;
    this._addMode = null;
    switch (this._page) {
      case "testo": main.innerHTML = this._pageTesto(); break;
      case "galleria": main.innerHTML = this._pageGalleria(); break;
      case "disegno": main.innerHTML = this._pageDisegno(); break;
      case "timer": main.innerHTML = this._pageTimer(); break;
      case "punti": main.innerHTML = this._pagePunti(); break;
      case "orologio": main.innerHTML = this._pageOrologio(); break;
      case "immagine": main.innerHTML = this._pageImmagine(); break;
      case "colori": main.innerHTML = this._pageColori(); break;
      case "sistema": main.innerHTML = this._pageSistema(); break;
    }
    this._bindPage();
    this._refreshValues();
    this._updateSimFromState();
  }

  _pageTesto() {
    const fonts = (this._state("select", "font") || {}).attributes || {};
    const fontOpts = (fonts.options || [])
      .map((o) => `<option value="${o}">${o.replace(/\.(ttf|otf)$/, "")}</option>`).join("");
    const animChips = ANIMATIONS.map(
      (a) => `<button class="chip anim-chip" data-v="${a.v}">${a.label}</button>`).join("");
    return `
      <div class="card">
        <h2>Tocca i sensori da mostrare</h2>
        <datalist id="ent-list">${this._entityDatalist()}</datalist>
        <div class="grid" id="tiles"></div>
        <div id="editor-slot"></div>
        <div class="row" style="margin-top:10px">
          <label>Tutte le caselle</label>
          <div class="chips">
            <button class="chip all-chip" data-k="icon" data-v="auto">Icone sì</button>
            <button class="chip all-chip" data-k="icon" data-v="off">Icone no</button>
            <button class="chip all-chip" data-k="unit" data-v="on">Unità sì</button>
            <button class="chip all-chip" data-k="unit" data-v="off">Unità no</button>
            <button class="chip all-chip" data-k="dec" data-v="0">,0</button>
            <button class="chip all-chip" data-k="dec" data-v="1">,1</button>
          </div>
        </div>
        <p style="color:var(--muted);font-size:12px;margin:2px 0 8px">
          Vale per tutte le caselle in un colpo solo e si applica subito;
          con la matita ✎ ritocchi poi la singola.</p>
        <div class="row">
          <label>Spazio tra elementi</label>
          <div class="chips">
            <button class="chip sep-chip" data-v="1">1</button>
            <button class="chip sep-chip" data-v="2">2</button>
            <button class="chip sep-chip" data-v="3">3</button>
          </div>
        </div>
        <div class="row">
          <button class="btn" id="send-comp">⚡ Invia al display</button>
        </div>
      </div>
    ` + this._pageOrologio() + `
      <div class="card">
        <h2>Stile del testo</h2>
        <div class="row"><label>Modalità</label><div class="chips">
          <button class="chip mode-chip" data-v="text">Scorrevole</button>
          <button class="chip mode-chip" data-v="textimage">Adattato</button></div></div>
        <div class="row"><label>Font</label><select id="font">${fontOpts}</select></div>
        <div class="row"><label>Colore testo</label>
          <input type="color" id="tx-color">
          <label style="min-width:auto">Sfondo</label>
          <input type="color" id="tx-bg"></div>
        <div class="row"><label>Grandezza</label>
          <input type="range" id="fsize" min="0" max="16" step="1">
          <span class="num" id="fsize-v"></span></div>
        <div class="row"><label>Velocità</label>
          <input type="range" id="speed" min="0" max="100" step="1"><span class="num" id="speed-v"></span></div>
        <div class="row"><label>Arcobaleno</label>
          <input type="range" id="rainbow" min="0" max="9" step="1"><span class="num" id="rainbow-v"></span></div>
        <p style="color:var(--muted);font-size:12px;margin:2px 0 8px">
          Valgono per tutta la riga che scorre (sensori, testo, icone).
          Velocità a 0 = minimo del firmware; Grandezza a 0 = Auto.</p>
      </div>
    `
      // Tutto il "visivo" vive qui nel tab Testo: immagine/GIF, orologio,
      // animazioni e rotazione. Riuso l'HTML delle vecchie pagine (stessi
      // id) cosi' il loro codice di aggancio eventi gira da solo.
      + this._pageImmagine()
      + this._pageGalleria();
  }

  _pageGalleria() {
    const base = "/ipixel_color_files/gallery";
    const tile = (f) => `
      <div class="gal-tile" data-f="${f}" title="Invia al display">
        <img src="${base}/${f}" loading="lazy">
      </div>`;
    const sfondi = Array.from({ length: 10 }, (_, i) =>
      tile(`sfondo_${String(i + 1).padStart(2, "0")}.gif`)).join("");
    const ritmo = [1, 2].map((i) => tile(`ritmo_${i}.gif`)).join("");
    return `
      <div class="card">
        <h2>Animazioni e ritmo</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Le animazioni originali dell'app, già nel formato del pannello:
          tocca per mandarla sul display.</p>
        <div class="gal-grid">${sfondi}</div>
        <div class="row" style="margin-top:16px"><b>Ritmo / equalizzatore</b></div>
        <div class="gal-grid">${ritmo}</div>
        <div class="row" style="margin-top:12px"><label>Equalizzatore</label><div class="chips">
          <button class="chip ry-style" data-v="0">Stile 1</button>
          <button class="chip ry-style" data-v="1">Stile 2</button>
          <button class="chip ry-style" data-v="2">Stile 3</button>
          <button class="chip ry-style" data-v="3">Stile 4</button>
          <button class="chip ry-style" data-v="4">Stile 5</button>
        </div></div>
        <div class="row">
          <button class="btn" id="ry-start">🎵 Avvia ritmo</button>
          <button class="btn sec" id="ry-stop">■ Ferma</button>
        </div>
        <p style="color:var(--muted);font-size:12px;margin:2px 0 0">
          Le barre dell'equalizzatore di bordo, mosse dal server (è una
          demo: il display non ha un microfono). Ferma = tornano i sensori.</p>
      </div>
      <div class="card">
        <h2>Rotazione automatica</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Alterna da sola i contenuti che scegli tu (sensori, immagini,
          orologio), ognuno con la sua durata, e torna da capo. Gira sul
          server e riparte anche dopo i riavvii di HA; si salva da sola e
          la rilegge a ogni giro, anche mentre gira.</p>
        <div class="row" style="margin-top:4px"><b>Scaletta — scegli tu cosa e quando</b></div>
        <p style="color:var(--muted);font-size:12px;margin:2px 0 8px">
          Aggiungi i passi nell'ordine che vuoi, ognuno con la sua durata.</p>
        <div id="pl-list"></div>
        <div class="row">
          <button class="chip" id="pl-add-s">+ Sensori</button>
          <button class="chip" id="pl-add-i">+ Immagine</button>
          <button class="chip" id="pl-add-c">+ Orologio</button>
        </div>
        <div class="row">
          <button class="btn" id="rot-start">🔄 Avvia rotazione</button>
          <button class="btn sec" id="rot-stop">■ Ferma</button>
        </div>
        <div class="row">
          <span id="rot-status" style="font-size:13px;color:var(--muted)">…</span>
        </div>
      </div>
    `;
  }

  async _refreshRotStatus() {
    const el = this.shadowRoot.getElementById("rot-status");
    if (!el || !this._hass || !this._dev) return;
    try {
      const st = await this._hass.callWS({
        type: "ipixel_color/status", address: this._dev.address,
      });
      // Precompila il modulo con gli ultimi valori salvati
      const pre = (inpId, unitId, secs) => {
        const inp = this.shadowRoot.getElementById(inpId);
        const unit = this.shadowRoot.getElementById(unitId);
        if (!inp || !unit || this.shadowRoot.activeElement === inp) return;
        if (secs % 60 === 0 && secs >= 60) { inp.value = secs / 60; unit.value = "60"; }
        else { inp.value = secs; unit.value = "1"; }
      };
      if (st.rot_comp_per_ent) {
        const inp = this.shadowRoot.getElementById("rot-comp");
        const unit = this.shadowRoot.getElementById("rot-comp-u");
        if (inp && unit && this.shadowRoot.activeElement !== inp) {
          inp.value = st.rot_comp_s; unit.value = "ent";
        }
      } else if (st.rot_comp_s) pre("rot-comp", "rot-comp-u", st.rot_comp_s);
      if (st.rot_anim_s) pre("rot-anim-s", "rot-anim-u", st.rot_anim_s);
      if (st.rot_clock_s) pre("rot-clock-s", "rot-clock-u", st.rot_clock_s);
      const chk = (id, v) => {
        const el = this.shadowRoot.getElementById(id);
        if (el && v !== undefined) el.checked = !!v;
      };
      chk("rot-use-s", st.rot_use_sensors);
      chk("rot-use-a", st.rot_use_anim);
      chk("rot-use-c", st.rot_use_clock);
      if (st.rot_anim) {
        const sel = this.shadowRoot.getElementById("rot-anim");
        const trySet = (attempt) => {
          if (!sel) return;
          sel.value = st.rot_anim;
          if (sel.value !== st.rot_anim && attempt < 4)
            setTimeout(() => trySet(attempt + 1), 400); // la libreria carica async
        };
        trySet(0);
      }
      // Scaletta: caricata una volta (poi comanda l'utente nel modulo)
      if (this._playlist === undefined || this._playlist === null) {
        this._playlist = Array.isArray(st.rot_playlist) ? st.rot_playlist : [];
        this._plRender();
      }
      if (st.running && st.mode === "rotation") {
        const fmt = (s) => (s % 60 === 0 && s >= 60) ? `${s / 60} min` : `${s} s`;
        const parts = [];
        if ((st.rot_playlist || []).length) {
          parts.push(`scaletta di ${st.rot_playlist.length} passi`);
        } else {
        if (st.rot_use_sensors !== false)
          parts.push(st.rot_comp_per_ent
            ? `sensori ${st.rot_comp_s} s a entità`
            : `sensori ${fmt(st.rot_comp_s)}`);
        if (st.rot_use_anim !== false) parts.push(`animazione ${fmt(st.rot_anim_s)}`);
        if (st.rot_use_clock) parts.push(`orologio ${fmt(st.rot_clock_s || 60)}`);
        }
        el.textContent = `● ROTAZIONE ATTIVA — ${parts.join(", ")}`;
        el.style.color = "var(--neon)";
        el.style.textShadow = "0 0 8px #0ff8";
      } else if (st.running) {
        el.textContent = "● In corso: " +
          (st.mode === "clock_it" ? "orologio italiano" :
           st.mode === "down" ? "conto alla rovescia" :
           st.mode === "up" ? "cronometro" : st.mode);
        el.style.color = "var(--neon)";
        el.style.textShadow = "0 0 8px #0ff8";
      } else {
        el.textContent = "Rotazione non attiva";
        el.style.color = "var(--muted)";
        el.style.textShadow = "none";
      }
    } catch (e) {
      el.textContent = "—";
    }
  }

  /* ----- Scaletta della rotazione: decide l'utente cosa e quando ----- */

  _plRender() {
    const box = this.shadowRoot.getElementById("pl-list");
    if (!box) return;
    const pl = this._playlist || [];
    if (!pl.length) {
      box.innerHTML = `<p style="color:var(--muted);font-size:12px;margin:2px 0 6px">
        Scaletta vuota: vale lo schema qui sopra.</p>`;
      return;
    }
    const labels = { sensors: "Sensori", image: "Immagine", clock: "Orologio" };
    const libOpts = (this._libItems || [])
      .map((it, i) => `<option value="${it.file}">Immagine ${i + 1}</option>`).join("");
    box.innerHTML = pl.map((s, i) => {
      const unitEnt = s.type === "sensors"
        ? `<option value="ent">sec. a entità</option>` : "";
      let val = s.seconds, unit = "1";
      if (s.per_ent) { unit = "ent"; }
      else if (s.seconds % 60 === 0 && s.seconds >= 60) { val = s.seconds / 60; unit = "60"; }
      const fileSel = s.type === "image"
        ? `<select class="pl-file" data-i="${i}" style="min-width:130px">
             ${this._galleryOptions()}${libOpts ? `<optgroup label="Le mie immagini">${libOpts}</optgroup>` : ""}
           </select>`
        : `<b style="min-width:80px">${labels[s.type] || s.type}</b>`;
      return `<div class="row" data-i="${i}">
        <span style="color:var(--muted)">${i + 1}.</span>
        ${s.type === "image" ? `<b style="min-width:80px">Immagine</b>` : ""}
        ${fileSel}
        <span style="color:var(--muted)">per</span>
        <input class="pl-sec" data-i="${i}" type="text" inputmode="numeric"
          value="${val}" style="max-width:70px;flex:none">
        <select class="pl-unit" data-i="${i}" style="flex:none;min-width:105px">
          <option value="1">secondi</option>
          <option value="60">minuti</option>
          ${unitEnt}
        </select>
        <button class="chip pl-up" data-i="${i}" title="Sposta su">▲</button>
        <button class="chip pl-down" data-i="${i}" title="Sposta giù">▼</button>
        <button class="chip pl-del" data-i="${i}" title="Togli"
          style="color:#ff4d6d;border-color:#ff4d6d">✕</button>
      </div>`;
    }).join("");

    const $$ = (sel) => box.querySelectorAll(sel);
    $$(".pl-file").forEach((el2) => {
      const s = pl[el2.dataset.i];
      if (s && s.file) el2.value = s.file;
      el2.addEventListener("change", () => {
        pl[el2.dataset.i].file = el2.value; this._plSave();
      });
    });
    const readRow = (i) => {
      const inp = box.querySelector(`.pl-sec[data-i="${i}"]`);
      const unit = box.querySelector(`.pl-unit[data-i="${i}"]`);
      const n = Math.max(1, parseInt(inp.value, 10) || 1);
      if (unit.value === "ent") {
        pl[i].per_ent = true; pl[i].seconds = n;
      } else {
        delete pl[i].per_ent; pl[i].seconds = n * parseInt(unit.value, 10);
      }
    };
    $$(".pl-sec").forEach((el2) => el2.addEventListener("change", () => {
      readRow(el2.dataset.i); this._plSave();
    }));
    $$(".pl-unit").forEach((el2) => el2.addEventListener("change", () => {
      readRow(el2.dataset.i); this._plSave();
    }));
    $$(".pl-up").forEach((el2) => el2.addEventListener("click", () => {
      const i = parseInt(el2.dataset.i, 10);
      if (i > 0) { [pl[i - 1], pl[i]] = [pl[i], pl[i - 1]];
        this._plRender(); this._plSave(); }
    }));
    $$(".pl-down").forEach((el2) => el2.addEventListener("click", () => {
      const i = parseInt(el2.dataset.i, 10);
      if (i < pl.length - 1) { [pl[i + 1], pl[i]] = [pl[i], pl[i + 1]];
        this._plRender(); this._plSave(); }
    }));
    $$(".pl-del").forEach((el2) => el2.addEventListener("click", () => {
      pl.splice(parseInt(el2.dataset.i, 10), 1);
      this._plRender(); this._plSave();
    }));
  }

  async _plSave() {
    if (!this._hass || !this._dev) return;
    clearTimeout(this._plSaveT);
    this._plSaveT = setTimeout(async () => {
      try {
        await this._hass.callWS({
          type: "ipixel_color/set_playlist", address: this._dev.address,
          playlist: this._playlist || [],
        });
        this._toast("Scaletta salvata ✓");
      } catch (e) {
        this._toast("Errore scaletta: " + ((e && e.message) || e), true);
      }
    }, 400);
  }

  _galleryOptions() {
    const opts = [];
    for (let i = 1; i <= 10; i++) {
      const f = `sfondo_${String(i).padStart(2, "0")}.gif`;
      opts.push(`<option value="${f}">Sfondo ${i}</option>`);
    }
    opts.push(`<option value="ritmo_1.gif">Ritmo 1</option>`);
    opts.push(`<option value="ritmo_2.gif">Ritmo 2</option>`);
    return opts.join("");
  }

  async _loadLibrary() {
    const grid = this.shadowRoot.getElementById("lib-grid");
    if (!grid || !this._hass) return;
    let items = [];
    try {
      const res = await this._hass.callWS({ type: "ipixel_color/library", action: "list" });
      items = res.items || [];
    } catch (e) { /* lista vuota */ }
    if (!items.length) {
      grid.innerHTML = `<p style="color:var(--muted);font-size:13px">Ancora vuota: invia un'immagine e la ritroverai qui.</p>`;
      return;
    }
    grid.innerHTML = items.map((it) => `
      <div class="gal-tile" data-f="${it.file}" style="position:relative" title="Rimanda al display">
        <button class="edit lib-del" data-f="${it.file}" title="Elimina">✕</button>
        <img src="/ipixel_color_library/${it.file}?t=${Date.now()}" loading="lazy">
      </div>`).join("");
    grid.querySelectorAll(".gal-tile").forEach((t) =>
      t.addEventListener("click", async (ev) => {
        if (ev.target.classList.contains("lib-del")) return;
        if (this._busy) return this._toast("Attendi: invio ancora in corso…", true);
        this._setBusy(true);
        this._toast("Invio in corso…");
        try {
          const res = await this._hass.callWS({
            type: "ipixel_color/library", action: "show",
            address: this._dev.address, file: t.dataset.f,
          });
          this._toast(res.displayed ? "Inviata al display ✓" : "Invio fallito", !res.displayed);
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
        finally { this._setBusy(false); }
      }));
    grid.querySelectorAll(".lib-del").forEach((b) =>
      b.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          await this._hass.callWS({
            type: "ipixel_color/library", action: "delete", file: b.dataset.f,
          });
          this._loadLibrary();
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
      }));
  }

  async _sendGalleryGif(fileName) {
    if (!this._dev) return;
    if (this._busy) return this._toast("Attendi: invio ancora in corso…", true);
    this._setBusy(true);
    this._toast("Invio in corso… le animazioni richiedono un po' di tempo");
    try {
      const res = await fetch(`/ipixel_color_files/gallery/${fileName}`);
      if (!res.ok) throw new Error("file non trovato");
      const buf = await res.arrayBuffer();
      let bin = "";
      const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i += 32768)
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
      const b64 = btoa(bin);
      await this._hass.callService("ipixel_color", "display_image", {
        address: this._dev.address, image: b64,
        resize_method: "crop", file_extension: ".gif",
      });
      this._toast("Animazione inviata ✓");
    } catch (e) {
      this._toast("Errore: " + (e.message || e), true);
    } finally { this._setBusy(false); }
  }

  _pageDisegno() {
    const mk = (c) => `<button class="sw dr-sw" data-c="${c}" style="background:${c};box-shadow:0 0 10px ${c}66"></button>`;
    const palette = ["#ffffff","#19e5ff","#3b82f6","#8b5cf6","#ff00ff","#ff4d6d","#ff8c00","#ffd400","#4ade80","#00ff6a"].map(mk).join("");
    return `
      <div class="card">
        <h2>Disegna sul pannello</h2>
        <canvas id="paint" class="preview" style="width:100%;max-width:720px;touch-action:none;cursor:crosshair"></canvas>
        <div class="row" style="margin-top:12px">
          <div class="swatches">${palette}</div>
          <input type="color" id="dr-color" value="#19e5ff" title="Colore personalizzato">
        </div>
        <div class="row">
          <button class="chip" id="dr-pen">🖌️ Pennello</button>
          <button class="chip" id="dr-eraser">🧽 Gomma</button>
          <button class="chip" id="dr-fill">🪣 Riempi tutto</button>
          <button class="chip" id="dr-clear">🗑️ Pulisci</button>
        </div>
        <div class="row">
          <label>In diretta sul display</label>
          <label class="switch"><input type="checkbox" id="dr-live"><span class="sl"></span></label>
          <span style="color:var(--muted);font-size:12px">ogni pennellata accende subito i LED</span>
        </div>
        <div class="row">
          <button class="btn" id="dr-send">⚡ Invia al display</button>
        </div>
        <p style="color:var(--muted);font-size:12px;margin:6px 0 0">
          Disegna col dito o col mouse. Il disegno resta salvato in questo browser.</p>
      </div>
    `;
  }

  _pageTimer() {
    return `
      <div class="card">
        <h2>Conto alla rovescia</h2>
        <div class="row"><label>Durata</label><div class="chips">
          <button class="chip tm-preset" data-v="60">1 min</button>
          <button class="chip tm-preset" data-v="180">3 min</button>
          <button class="chip tm-preset" data-v="300">5 min</button>
          <button class="chip tm-preset" data-v="600">10 min</button>
        </div></div>
        <div class="row"><label>Minuti a scelta</label>
          <input type="text" id="tm-min" inputmode="numeric" placeholder="es. 7" style="max-width:100px">
        </div>
        <div class="row">
          <button class="btn" id="tm-start">▶ Avvia</button>
          <button class="btn sec" id="tm-stop">■ Ferma</button>
        </div>
        <p style="color:var(--muted);font-size:12px;margin:6px 0 0">
          Il timer gira sul server: puoi chiudere questa pagina, il display continua.
          Alla fine lampeggia 00:00.</p>
      </div>
      <div class="card">
        <h2>Cronometro</h2>
        <div class="row">
          <button class="btn" id="cr-start">▶ Avvia</button>
          <button class="btn sec" id="cr-pause">⏸ Pausa</button>
          <button class="btn sec" id="cr-stop">■ Ferma</button>
        </div>
        <p style="color:var(--muted);font-size:12px;margin:6px 0 0">
          Il cronometro gira DENTRO il display: conta da solo, zero
          Bluetooth, e la pausa è al centesimo.</p>
      </div>
    `;
  }

  _pagePunti() {
    const s = this._score || { a: 0, b: 0 };
    return `
      <div class="card">
        <h2>Segnapunti</h2>
        <div class="row" style="justify-content:center;gap:30px">
          <div style="text-align:center">
            <div style="font-size:52px;color:var(--neon);text-shadow:0 0 16px #0ff8;font-variant-numeric:tabular-nums" id="sc-a">${s.a}</div>
            <div class="row" style="justify-content:center">
              <button class="chip" id="sc-a-up">+1</button>
              <button class="chip" id="sc-a-dn">−1</button>
            </div>
          </div>
          <div style="font-size:36px;color:var(--muted)">:</div>
          <div style="text-align:center">
            <div style="font-size:52px;color:#ff4d6d;text-shadow:0 0 16px #ff4d6d88;font-variant-numeric:tabular-nums" id="sc-b">${s.b}</div>
            <div class="row" style="justify-content:center">
              <button class="chip" id="sc-b-up">+1</button>
              <button class="chip" id="sc-b-dn">−1</button>
            </div>
          </div>
        </div>
        <div class="row" style="justify-content:center">
          <button class="btn sec" id="sc-reset">Azzera</button>
        </div>
        <p style="color:var(--muted);font-size:12px;margin:6px 0 0;text-align:center">
          Ogni tocco aggiorna subito il tabellone: lo disegna il display
          da solo (0-99 a giocatore).</p>
      </div>
    `;
  }

  _pageOrologio() {
    const styles = Array.from({ length: 9 }, (_, i) => i + 1)
      .map((v) => `<button class="chip clock-chip" data-v="${v}">Stile ${v}</button>`).join("");
    return `
      <div class="card">
        <h2>Orologio</h2>
        <div class="row"><label>Stile</label><div class="chips">${styles}</div></div>
        <div class="row">
          <label>Formato 24h</label>
          <label class="switch"><input type="checkbox" id="c24"><span class="sl"></span></label>
          <label style="min-width:auto">Mostra data</label>
          <label class="switch"><input type="checkbox" id="cdate"><span class="sl"></span></label>
        </div>
        <div class="row">
          <button class="btn" id="clock-on">🕒 Attiva orologio</button>
          <button class="btn sec" id="sync-time">Sincronizza ora</button>
        </div>
      </div>
      <div class="card">
        <h2>Orologio italiano</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Scritto dal server, col giorno della settimana in italiano.
          Scegli lo stile: se l'orologio è già in moto cambia da solo.</p>
        <div class="row"><label>Stile</label>
          <select id="clock-it-style" style="flex:1;min-width:220px">
            <option value="a">A — Classico (GIO 02:09)</option>
            <option value="b">B — Due righe (data sopra, ora sotto)</option>
            <option value="c">C — Ora grande</option>
            <option value="d">D — Ora + giorno a lato</option>
            <option value="e">E — Con i secondi</option>
            <option value="f">F — Ora + temperatura</option>
            <option value="g">G — Due punti lampeggianti</option>
            <option value="i">I — A parole (LE DUE E MEZZA)</option>
            <option value="l">L — Sole e luna</option>
            <option value="m">M — Fiamme sotto l'ora</option>
            <option value="n">N — Cielo stellato</option>
            <option value="o">O — Ora + icona a scelta</option>
          </select></div>
        <div class="row"><label>Grandezza</label>
          <input type="range" id="clk-size" min="5" max="16" step="1" value="7">
          <span class="num" id="clk-size-v">piccola</span></div>
        <div class="row"><label>Distanza</label>
          <input type="range" id="clk-gap" min="0" max="12" step="1" value="2">
          <span class="num" id="clk-gap-v">2</span></div>
        <p style="color:var(--muted);font-size:12px;margin:0 0 8px">
          Grandezza (altezza in pixel, il display è alto 16) e distanza
          valgono per lo <b>Stile A</b> (giorno + ora). Il giorno si
          ingrandisce da solo quando c'è spazio.</p>
        <div class="row"><label>Icona (stile O)</label>
          <input type="text" id="clock-icon" placeholder="es. heart o 😍" style="max-width:140px">
          <input type="color" id="clock-icon-color" value="#ff0000"></div>
        <p style="color:var(--muted);font-size:12px;margin:0 0 6px">
          Nome Material Design (<i>heart</i>, <i>home</i>…) o un'emoji.
          Toccandola lo stile passa da solo a O, l'unico che la mostra.</p>
        <p style="color:var(--muted);font-size:12px;margin:0 0 10px" id="clock-it-note">
          Gli stili E, G e H aggiornano il display ogni secondo (radio più occupata).
          "Mostra data" della scheda qui sopra vale anche qui.</p>
        <div class="row">
          <button class="btn" id="clock-it-on">🇮🇹 Avvia orologio italiano</button>
          <button class="btn sec" id="clock-it-off">■ Ferma</button>
        </div>
      </div>
    `;
  }

  _pageImmagine() {
    return `
      <div class="card">
        <h2>Invia un'immagine</h2>
        <div class="drop" id="drop">Trascina un'immagine qui, o tocca per sceglierla<br>
          <small>PNG, JPG o GIF — adattata al pannello.<br>
          📱 Sul telefono, per le GIF scegli <b>«File»/«Scegli file»</b>
          (da «Foto» il telefono può dire «impossibile inserire questo
          contenuto»). Oppure incolla il <b>link</b> qui sotto: funziona
          sempre.</small>
          <input type="file" id="file" accept="image/*" style="display:none">
        </div>
        <div class="row" style="margin-top:14px"><label>Adattamento</label><div class="chips">
          <button class="chip rez-chip" data-v="crop">Riempi (taglia)</button>
          <button class="chip rez-chip" data-v="fit">Contieni (bordi)</button></div></div>
        <div class="row"><label>Anteprima</label>
          <canvas id="preview" class="preview" width="64" height="16" style="width:512px;max-width:100%"></canvas></div>
        <div class="row"><button class="btn" id="send-img" disabled>⚡ Invia immagine</button></div>
        <div class="row" style="margin-top:8px;border-top:1px solid var(--line);padding-top:12px">
          <label><b>Oppure da un link</b></label></div>
        <p style="color:var(--muted);font-size:12px;margin:0 0 8px">
          Incolla il link di una GIF o immagine (Giphy, Tenor, qualsiasi
          sito): la scarica il server e la adatta da sola. Funziona
          sempre, anche dal telefono.</p>
        <div class="row">
          <input type="text" id="url-img" placeholder="https://...gif">
          <button class="btn" id="send-url">⚡ Scarica e invia</button>
        </div>
      </div>
      <div class="card">
        <h2>Le mie immagini</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Tutto quello che invii resta salvato qui: tocca per rimandarlo
          al display, ✕ per eliminarlo.</p>
        <div class="gal-grid" id="lib-grid">
          <p style="color:var(--muted);font-size:13px">Ancora vuota.</p>
        </div>
      </div>
      <div class="card">
        <h2>Memoria del display</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Quattro cassetti DENTRO il display: salvaci l'ultima cosa
          inviata (immagine, scritta, icona…) e richiamala all'istante
          con un comando da 7 byte. Restano anche senza Home Assistant.</p>
        <div id="slot-list"></div>
      </div>
    `;
  }

  _pageColori() {
    const mk = (c) => `<button class="sw" data-c="${c}" style="background:${c};box-shadow:0 0 10px ${c}88"></button>`;
    const palette = ["#ffffff","#19e5ff","#3b82f6","#8b5cf6","#f0f","#ff4d6d","#ff8c00","#ffd400","#4ade80"].map(mk).join("");
    return `
      <div class="card">
        <h2>Colore del testo</h2>
        <div class="row"><div class="swatches" id="sw-text">${palette}</div></div>
        <div class="row"><label>Personalizzato</label><input type="color" id="col-text"></div>
      </div>
      <div class="card">
        <h2>Colore dello sfondo</h2>
        <div class="row"><div class="swatches" id="sw-bg">${mk("#000000")}${palette}</div></div>
        <div class="row"><label>Personalizzato</label><input type="color" id="col-bg"></div>
      </div>
      <div class="card">
        <h2>Luminosità</h2>
        <div class="row"><input type="range" id="bright" min="1" max="100" step="1">
          <span class="num" id="bright-v"></span></div>
      </div>
    `;
  }

  _pageSistema() {
    return `
      <div class="card">
        <h2>Alimentazione</h2>
        <div class="row">
          <label>Display acceso</label>
          <label class="switch"><input type="checkbox" id="power"><span class="sl"></span></label>
          <label style="min-width:auto">Aggiorna da solo</label>
          <label class="switch"><input type="checkbox" id="autoupd"><span class="sl"></span></label>
        </div>
        <div class="row"><button class="btn" id="force-update">⚡ Aggiorna display adesso</button></div>
      </div>
      <div class="card">
        <h2>Anteprima</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Regola le proporzioni della simulazione in alto finché non
          somiglia al pannello fisico (i LED veri sono più distanziati
          in orizzontale). Non cambia nulla sul display.</p>
        <div class="row">
          <label>Proporzioni</label>
          <input type="range" id="stretch" min="100" max="200" step="5">
          <span class="num" id="stretch-v"></span>
        </div>
        <div class="row">
          <label>Velocità anteprima</label>
          <input type="range" id="simspeed" min="25" max="600" step="25">
          <span class="num" id="simspeed-v"></span>
        </div>
        <p style="color:var(--muted);font-size:12px;margin:2px 0 0">
          La velocità vera dello scorrimento del firmware non è
          documentata: guarda il display e regola finché l'anteprima va
          allo stesso passo. Solo anteprima, non cambia il display.</p>
      </div>
      <div class="card">
        <h2>Accensione programmata (nel display)</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Orari scritti NEL firmware: il display si accende e spegne da
          solo, anche con Home Assistant spento.</p>
        <div class="row"><label>Accendi alle</label>
          <input type="time" id="sch-on" value="08:00" style="max-width:130px"></div>
        <div class="row"><label>Spegni alle</label>
          <input type="time" id="sch-off" value="23:00" style="max-width:130px"></div>
        <div class="row"><label>Giorni</label><div class="chips">
          <button class="chip sch-day" data-v="mon">Lun</button>
          <button class="chip sch-day" data-v="tue">Mar</button>
          <button class="chip sch-day" data-v="wed">Mer</button>
          <button class="chip sch-day" data-v="thu">Gio</button>
          <button class="chip sch-day" data-v="fri">Ven</button>
          <button class="chip sch-day" data-v="sat">Sab</button>
          <button class="chip sch-day" data-v="sun">Dom</button>
        </div></div>
        <p style="color:var(--muted);font-size:12px;margin:2px 0 8px">
          Nessun giorno selezionato = tutti i giorni.</p>
        <div class="row">
          <button class="btn" id="sch-set">✓ Attiva</button>
          <button class="btn sec" id="sch-del">Disattiva</button>
        </div>
        <p id="sch-status" style="color:var(--muted);font-size:12px;margin:4px 0 0"></p>
      </div>
      <div class="card">
        <h2>App del telefono</h2>
        <p style="color:var(--muted);font-size:13px;margin:0 0 10px">
          Il display accetta un solo collegamento Bluetooth: con questo
          bottone Home Assistant lo lascia libero per 10 minuti, così
          puoi usare l'app iPIXEL Color senza toccare nulla. Poi si
          ricollega da solo (o premi Ricollega).</p>
        <div class="row">
          <button class="btn" id="rel-app">📱 Libera per l'app (10 min)</button>
          <button class="btn sec" id="rel-end">🔗 Ricollega</button>
        </div>
        <div class="row">
          <span id="rel-status" style="font-size:13px;color:var(--muted)"></span>
        </div>
      </div>
      <div class="card">
        <h2>Dispositivo</h2>
        <dl class="info-grid" id="devinfo"></dl>
      </div>
    `;
  }

  /* ============================================ piastrelle (Testo) */

  _entityDatalist() {
    if (!this._hass) return "";
    const rows = [];
    for (const [id, st] of Object.entries(this._hass.states)) {
      const dom = id.split(".")[0];
      if (!["sensor", "input_number", "number", "counter", "binary_sensor"].includes(dom)) continue;
      rows.push({ id, name: (st.attributes && st.attributes.friendly_name) || id });
    }
    rows.sort((a, b) => a.name.localeCompare(b.name));
    return rows.map((r) => `<option value="${r.id}">${r.name}</option>`).join("");
  }

  _guessLabel(entityId) {
    const st = this._hass && this._hass.states[entityId];
    let name = (st && st.attributes && st.attributes.friendly_name) || entityId.split(".")[1] || "";
    name = name.replace(/temperatura/gi, "").replace(/sensore?/gi, "")
      .replace(/umidit[aà]/gi, "").replace(/\s+/g, " ").trim();
    const first = name.split(" ")[0] || entityId.split(".")[1];
    return first.toUpperCase().slice(0, 10);
  }

  _tileIcon(it) {
    if (it.type === "text") {
      const t = (it.text || "").trim();
      return (t && !/[a-zA-Z0-9]/.test(t) && [...t].length <= 4) ? t : "📝";
    }
    const st = this._hass && this._hass.states[it.entity_id];
    const unit = (st && st.attributes && st.attributes.unit_of_measurement) || "";
    const dc = (st && st.attributes && st.attributes.device_class) || "";
    if (dc === "temperature" || unit.includes("°")) return "🌡️";
    if (dc === "humidity" || unit === "%") return "💧";
    if (dc === "power" || unit === "W" || unit === "kW") return "⚡";
    if (dc === "battery") return "🔋";
    if (dc === "energy") return "🔌";
    return "📟";
  }

  _tileValue(it) {
    if (it.type === "text") return (it.text || "").slice(0, 12) || "…";
    const st = this._hass && this._hass.states[it.entity_id];
    if (!st || ["unknown", "unavailable", ""].includes(st.state)) return "?";
    const f = parseFloat(st.state);
    if (isNaN(f)) return st.state.slice(0, 10);
    return f.toFixed(it.decimals == null ? 1 : it.decimals).replace(".", ",");
  }

  _renderTiles() {
    const box = this.shadowRoot.getElementById("tiles");
    if (!box) return;
    const items = this._items || [];
    const tiles = items.map((it, i) => `
      <div class="tile ${it.enabled === false ? "" : "sel"}" data-i="${i}">
        <span class="drag" title="Trascina per riordinare">⠿</span>
        <button class="edit" data-i="${i}" title="Modifica">✎</button>
        <div class="ic">${this._tileIcon(it)}</div>
        <b>${it.type === "text" ? "Testo" : (it.label || "—")}</b>
        <div class="v">${this._tileValue(it)}</div>
      </div>`).join("");
    box.innerHTML = tiles + `
      <div class="tile addtile" id="add-ent"><span class="plus">+</span>Sensore</div>
      <div class="tile addtile" id="add-txt"><span class="plus">+</span>Testo fisso</div>
      <div class="tile addtile" id="add-emoji"><span class="plus">+</span>Icona/Emoji</div>`;

    box.querySelectorAll(".tile[data-i]").forEach((t) =>
      t.addEventListener("click", (ev) => {
        if (ev.target.classList.contains("edit")) return;
        // Un rilascio a fine trascinamento non è un tocco
        if (Date.now() - (this._dragEndAt || 0) < 400) return;
        const it = this._items[t.dataset.i];
        it.enabled = it.enabled === false ? true : false;
        this._renderTiles(); this._updateSimFromState(); this._autoSend();
      }));
    this._bindTileDrag(box);
    box.querySelectorAll(".edit").forEach((b) =>
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._editIndex = parseInt(b.dataset.i, 10);
        this._addMode = null;
        this._renderEditor();
      }));
    this._refreshAllChips();
    box.querySelector("#add-ent").addEventListener("click", () => {
      this._addMode = "entity"; this._editIndex = null; this._renderEditor();
    });
    box.querySelector("#add-txt").addEventListener("click", () => {
      this._addMode = "text"; this._editIndex = null; this._renderEditor();
    });
    box.querySelector("#add-emoji").addEventListener("click", () => {
      this._addMode = "emoji"; this._editIndex = null; this._renderEditor();
    });
  }

  /* Accende i bottoni "Tutte le caselle" secondo lo stato reale:
     tutti uguali = chip acceso, situazione mista = nessuno acceso */
  _refreshAllChips() {
    const chips = this.shadowRoot.querySelectorAll(".all-chip");
    if (!chips.length) return;
    const ents = (this._items || []).filter(
      (it) => it.type !== "text" && it.entity_id);
    const st = { icon: null, unit: null, dec: null };
    if (ents.length) {
      const icons = ents.map((it) => (it.icon === "" ? "off" : "auto"));
      if (icons.every((v) => v === "auto")) st.icon = "auto";
      else if (icons.every((v) => v === "off")) st.icon = "off";
      const units = ents.map((it) => (it.show_unit ? "on" : "off"));
      if (units.every((v) => v === "on")) st.unit = "on";
      else if (units.every((v) => v === "off")) st.unit = "off";
      const decs = ents.map((it) => String(it.decimals == null ? 1 : it.decimals));
      if (decs.every((v) => v === decs[0])) st.dec = decs[0];
    }
    chips.forEach((c) =>
      c.classList.toggle("active", st[c.dataset.k] === c.dataset.v));
  }

  _bindTileDrag(box) {
    // Riordino col trascinamento: corpo della piastrella col mouse,
    // maniglia ⠿ anche col dito (il resto della piastrella lascia
    // scorrere la pagina sul telefono). Al rilascio salva e applica.
    const tiles = () => [...box.querySelectorAll(".tile[data-i]")];
    let drag = null;

    const onMove = (ev) => {
      if (!drag) return;
      const dx = ev.clientX - drag.startX, dy = ev.clientY - drag.startY;
      if (!drag.active) {
        if (Math.hypot(dx, dy) < 7) return;
        drag.active = true;
        // La piastrella "si solleva": una copia segue il puntatore,
        // l'originale resta come segnaposto tratteggiato
        const r = drag.tile.getBoundingClientRect();
        drag.offX = drag.startX - r.left;
        drag.offY = drag.startY - r.top;
        const g = drag.tile.cloneNode(true);
        g.classList.add("ghost");
        g.style.width = r.width + "px";
        g.style.height = r.height + "px";
        this.shadowRoot.appendChild(g);
        drag.ghost = g;
        drag.tile.classList.add("dragging");
      }
      ev.preventDefault();
      drag.ghost.style.left = (ev.clientX - drag.offX) + "px";
      drag.ghost.style.top = (ev.clientY - drag.offY) + "px";
      const el = this.shadowRoot.elementFromPoint(ev.clientX, ev.clientY);
      const over = el && el.closest ? el.closest(".tile[data-i]") : null;
      if (!over || over === drag.tile) return;
      const r = over.getBoundingClientRect();
      const before = ev.clientX - r.left < r.width / 2;
      over.parentNode.insertBefore(drag.tile, before ? over : over.nextSibling);
    };

    const finish = () => {
      if (!drag) return;
      const wasActive = drag.active;
      drag.tile.classList.remove("dragging");
      if (drag.ghost) drag.ghost.remove();
      drag = null;
      if (!wasActive) return;
      this._dragEndAt = Date.now();
      const order = tiles().map((t) => parseInt(t.dataset.i, 10));
      if (order.some((v, i) => v !== i)) {
        this._items = order.map((i) => this._items[i]);
        this._renderTiles();
        this._updateSimFromState();
        this._autoSend(); // nuovo ordine dritto al display
      }
    };

    tiles().forEach((t) => {
      const begin = (ev) => {
        drag = { tile: t, startX: ev.clientX, startY: ev.clientY, active: false };
        try { t.setPointerCapture(ev.pointerId); } catch (e) { /* ok */ }
      };
      const handle = t.querySelector(".drag");
      if (handle)
        handle.addEventListener("pointerdown", (ev) => {
          ev.stopPropagation(); begin(ev);
        });
      t.addEventListener("pointerdown", (ev) => {
        if (ev.target.classList.contains("edit")) return;
        if (ev.pointerType === "mouse" && ev.button === 0) begin(ev);
      });
      t.addEventListener("pointermove", onMove);
      t.addEventListener("pointerup", finish);
      t.addEventListener("pointercancel", () => {
        if (drag) {
          drag.tile.classList.remove("dragging");
          if (drag.ghost) drag.ghost.remove();
          drag = null;
        }
      });
    });
  }

  _renderEditor() {
    const slot = this.shadowRoot.getElementById("editor-slot");
    if (!slot) return;
    const close = `<button class="ed-x" id="ed-close" title="Chiudi" aria-label="Chiudi">✕</button>`;

    if (this._addMode === "entity") {
      slot.innerHTML = `<div class="editor">
        <div class="row"><label>Sensore</label>
          <input type="text" id="ed-entity" list="ent-list" placeholder="Cerca il sensore..."></div>
        <div class="row"><button class="btn" id="ed-add">Aggiungi</button>${close}</div>
      </div>`;
      slot.querySelector("#ed-add").addEventListener("click", () => {
        const eid = slot.querySelector("#ed-entity").value.trim();
        if (!eid || !this._hass.states[eid]) return this._toast("Scegli un sensore dalla lista", true);
        if (!this._items) this._items = [];
        this._items.push({ type: "entity", entity_id: eid, label: this._guessLabel(eid), decimals: 1, enabled: true });
        this._addMode = null; this._renderEditor(); this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      slot.querySelector("#ed-close").addEventListener("click", () => { this._addMode = null; this._renderEditor(); });
      slot.querySelector("#ed-entity").focus();
      return;
    }

    if (this._addMode === "text") {
      slot.innerHTML = `<div class="editor">
        <div class="row"><label>Testo fisso</label>
          <input type="text" id="ed-text" placeholder="Es. CIAO! ❤️"></div>
        <p style="color:var(--muted);font-size:12px;margin:4px 0 8px">
          Puoi scrivere anche le emoji (❤️ 🌡️ ☀️ 🏠…): scorrono sul
          display insieme al testo. Sul PC si aprono con Win + punto.</p>
        <div class="row"><button class="btn" id="ed-add">Aggiungi</button>${close}</div>
      </div>`;
      slot.querySelector("#ed-add").addEventListener("click", () => {
        const txt = slot.querySelector("#ed-text").value.trim();
        if (!txt) return;
        if (!this._items) this._items = [];
        this._items.push({ type: "text", text: txt, enabled: true });
        this._addMode = null; this._renderEditor(); this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      slot.querySelector("#ed-close").addEventListener("click", () => { this._addMode = null; this._renderEditor(); });
      slot.querySelector("#ed-text").focus();
      return;
    }

    if (this._addMode === "emoji") {
      const picks = ["❤️","🧡","💛","💚","💙","💜","🤍","🌡️","💧","⚡","🔋","🔌",
        "☀️","🌙","⛅","🌧️","❄️","🔥","🌈","🏠","🚗","🔔","✅","⚠️","⭐","🎵",
        "😀","😍","👍","🕐","📅","📈","🎉","💪","🍀","🎁"];
      slot.innerHTML = `<div class="editor">
        <div class="row"><label>Icona / Emoji</label>
          <input type="text" id="ed-emoji" placeholder="❤️  (tocca qui sotto)" style="max-width:140px"></div>
        <div class="row"><div class="chips" id="emoji-picks" style="flex-wrap:wrap;gap:6px">
          ${picks.map((e) => `<button class="chip emoji-pick" data-e="${e}" style="font-size:18px;padding:4px 8px">${e}</button>`).join("")}
        </div></div>
        <p style="color:var(--muted);font-size:12px;margin:4px 0 8px">
          Diventa una casella che SCORRE nella riga insieme ai sensori e
          al testo. Puoi metterne più d'una e spostarle dove vuoi con ✎.</p>
        <div class="row"><button class="btn" id="ed-add">Aggiungi</button>${close}</div>
      </div>`;
      const inp = slot.querySelector("#ed-emoji");
      slot.querySelectorAll(".emoji-pick").forEach((b) =>
        b.addEventListener("click", () => { inp.value += b.dataset.e; inp.focus(); }));
      slot.querySelector("#ed-add").addEventListener("click", () => {
        const e = (inp.value || "").trim();
        if (!e) return;
        if (!this._items) this._items = [];
        this._items.push({ type: "text", text: e, enabled: true });
        this._addMode = null; this._renderEditor(); this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      slot.querySelector("#ed-close").addEventListener("click", () => { this._addMode = null; this._renderEditor(); });
      inp.focus();
      return;
    }

    if (this._editIndex == null) { slot.innerHTML = ""; return; }
    const it = this._items[this._editIndex];
    if (!it) { slot.innerHTML = ""; return; }

    if (it.type === "text") {
      slot.innerHTML = `<div class="editor">
        <div class="row"><label>Testo</label>
          <input type="text" id="ed-text" value="${(it.text || "").replace(/"/g, "&quot;")}"></div>
        <div class="row"><label>Distanza dopo</label>
          <input type="range" id="ed-gap" min="0" max="8" step="1"
            value="${it.gap == null ? 2 : it.gap}">
          <span class="num" id="ed-gap-v">${it.gap == null ? "normale" : it.gap}</span></div>
        <div class="row">
          <button class="btn sec" id="ed-del" style="color:#ff4d6d;border-color:#ff4d6d">Elimina</button>
          ${close}</div>
      </div>`;
      slot.querySelector("#ed-text").addEventListener("input", (e) => {
        it.text = e.target.value; this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
    } else {
      slot.innerHTML = `<div class="editor">
        <div class="row"><label>Etichetta</label>
          <input type="text" id="ed-label" value="${(it.label || "").replace(/"/g, "&quot;")}"></div>
        <div class="row"><label>Colore</label>
          <input type="color" id="ed-color"
            value="${it.color ? "#" + String(it.color).replace("#", "") : "#ffffff"}">
          <button class="chip" id="ed-color-auto">Generale</button></div>
        <div class="row"><label>➕ Sensore sotto</label>
          <input type="text" id="ed-below" list="ent-list" style="max-width:140px"
            placeholder="2° sensore impilato"
            value="${it.below && it.below.entity_id ? it.below.entity_id : ""}">
          <input type="color" id="ed-below-color" title="colore della riga sotto"
            value="${it.below && it.below.color ? "#" + String(it.below.color).replace("#", "") : "#00c8ff"}">
          <button class="chip" id="ed-below-clear">Togli</button></div>
        <p style="color:var(--muted);font-size:12px;margin:4px 0 8px">
          Un secondo sensore <b>impilato sotto</b> il primo, nello stesso
          spazio (due righe da 7px che scorrono insieme). Ogni riga ha il
          <b>suo colore</b> e le sue icone; con un sensore sotto
          grandezza/posizione non si applicano.</p>
        <div class="row"><label>Decimali</label>
          <div class="chips">
            <button class="chip dec-chip" data-v="0">0</button>
            <button class="chip dec-chip" data-v="1">1</button>
            <button class="chip dec-chip" data-v="2">2</button>
          </div></div>
        <div class="row"><label>Unità di misura</label><div class="chips">
          <button class="chip unit-chip" data-v="no">Nascondi</button>
          <button class="chip unit-chip" data-v="si">Mostra (C, W, %…)</button>
        </div></div>
        <div class="row"><label>Icona sul display</label>
          <input type="text" id="ed-icon" style="max-width:80px"
            value="${(it.icon == null ? "" : it.icon).replace(/"/g, "&quot;")}"
            placeholder="${this._autoIcon(this._hass && this._hass.states[it.entity_id], it.entity_id) || "nessuna"}">
          <button class="chip" id="ed-icon-auto">Auto</button>
          <button class="chip" id="ed-icon-none">Niente</button></div>
        <p style="color:var(--muted);font-size:12px;margin:4px 0 8px">
          L'emoji che scorre davanti al valore. Vuoto = automatica dal
          tipo di sensore; "Niente" la toglie solo per questa casella.</p>
        <div class="row"><label>Distanza icona</label>
          <input type="range" id="ed-icon-gap" min="0" max="5" step="1"
            value="${it.icon_gap == null ? 1 : it.icon_gap}">
          <span class="num" id="ed-icon-gap-v">${it.icon_gap == null ? "normale" : it.icon_gap}</span></div>
        <div class="row"><label>Grandezza</label>
          <input type="range" id="ed-size" min="5" max="16" step="1"
            value="${it.size || 16}">
          <span class="num" id="ed-size-v">${it.size ? it.size : "piena"}</span></div>
        <div class="row"><label>Posizione</label>
          <input type="range" id="ed-voff" min="0" max="11" step="1"
            value="${it.voff != null ? it.voff : (it.valign === "bottom" ? Math.max(0, 16 - (it.size || 16)) : it.valign === "top" ? 0 : Math.round(Math.max(0, 16 - (it.size || 16)) / 2))}">
          <span class="num" id="ed-voff-v">${it.voff != null ? it.voff + " px" : "auto"}</span></div>
        <p style="color:var(--muted);font-size:12px;margin:0 0 6px">
          Posizione verticale (px dall'alto, 0 = in cima). Vale quando la
          casella è rimpicciolita (a piena non c'è spazio per spostarla).</p>
        <div class="row"><label>Distanza dopo</label>
          <input type="range" id="ed-gap" min="0" max="8" step="1"
            value="${it.gap == null ? 2 : it.gap}">
          <span class="num" id="ed-gap-v">${it.gap == null ? "normale" : it.gap}</span></div>
        <div class="row" style="color:var(--muted);font-size:12px">${it.entity_id}</div>
        <div class="row">
          <button class="btn sec" id="ed-del" style="color:#ff4d6d;border-color:#ff4d6d">Elimina</button>
          ${close}</div>
      </div>`;
      slot.querySelector("#ed-label").addEventListener("input", (e) => {
        it.label = e.target.value; this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      slot.querySelector("#ed-size").addEventListener("input", (e) => {
        const v = parseInt(e.target.value, 10);
        if (v >= 16) { delete it.size; } else { it.size = v; }
        slot.querySelector("#ed-size-v").textContent = v >= 16 ? "piena" : v;
      });
      slot.querySelector("#ed-size").addEventListener("change", () => {
        this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      const voffEl = slot.querySelector("#ed-voff");
      if (voffEl) {
        voffEl.addEventListener("input", (e) => {
          it.voff = parseInt(e.target.value, 10);
          delete it.valign;  // la posizione a px ha la precedenza
          const v = slot.querySelector("#ed-voff-v");
          if (v) v.textContent = it.voff + " px";
          this._updateSimFromState();
        });
        voffEl.addEventListener("change", () => {
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        });
      }
      const belowEl = slot.querySelector("#ed-below");
      const belowCol = slot.querySelector("#ed-below-color");
      if (belowEl) {
        const setBelow = () => {
          const eid = belowEl.value.trim();
          if (eid && this._hass && this._hass.states[eid]) {
            it.below = {
              entity_id: eid, label: this._guessLabel(eid),
              decimals: 1, show_unit: !!it.show_unit,
              color: belowCol ? belowCol.value.slice(1) : undefined,
            };
          } else if (!eid) {
            delete it.below;
          } else {
            return this._toast("Scegli un sensore dalla lista", true);
          }
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        };
        belowEl.addEventListener("change", setBelow);
        if (belowCol) belowCol.addEventListener("change", () => {
          if (it.below) {
            it.below.color = belowCol.value.slice(1);
            this._renderTiles(); this._updateSimFromState(); this._autoSend();
          }
        });
        const clr = slot.querySelector("#ed-below-clear");
        if (clr) clr.addEventListener("click", () => {
          delete it.below; belowEl.value = "";
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        });
      }
      const colEl = slot.querySelector("#ed-color");
      if (colEl) {
        colEl.addEventListener("change", () => {
          it.color = colEl.value.slice(1);
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        });
        const auto = slot.querySelector("#ed-color-auto");
        if (auto) auto.addEventListener("click", () => {
          delete it.color;
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        });
      }
      slot.querySelectorAll(".unit-chip").forEach((c) => {
        c.classList.toggle("active", (it.show_unit ? "si" : "no") === c.dataset.v);
        c.addEventListener("click", () => {
          it.show_unit = c.dataset.v === "si";
          slot.querySelectorAll(".unit-chip").forEach((x) =>
            x.classList.toggle("active", x === c));
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        });
      });
      slot.querySelector("#ed-icon").addEventListener("input", (e) => {
        const v = e.target.value.trim();
        if (v) it.icon = v; else delete it.icon;  // vuoto = automatica
        this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      slot.querySelector("#ed-icon-auto").addEventListener("click", () => {
        delete it.icon; slot.querySelector("#ed-icon").value = "";
        this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      slot.querySelector("#ed-icon-none").addEventListener("click", () => {
        it.icon = ""; slot.querySelector("#ed-icon").value = "";
        this._toast("Icona tolta per questa casella");
        this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
      const igap = slot.querySelector("#ed-icon-gap");
      if (igap) {
        igap.addEventListener("input", (e) => {
          it.icon_gap = parseInt(e.target.value, 10);
          const g = slot.querySelector("#ed-icon-gap-v");
          if (g) g.textContent = it.icon_gap;
          this._updateSimFromState();
        });
        igap.addEventListener("change", () => {
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        });
      }
      slot.querySelectorAll(".dec-chip").forEach((c) => {
        c.classList.toggle("active", String(it.decimals == null ? 1 : it.decimals) === c.dataset.v);
        c.addEventListener("click", () => {
          it.decimals = parseInt(c.dataset.v, 10);
          slot.querySelectorAll(".dec-chip").forEach((x) => x.classList.toggle("active", x === c));
          this._renderTiles(); this._updateSimFromState(); this._autoSend();
        });
      });
    }

    const gapEl = slot.querySelector("#ed-gap");
    if (gapEl) {
      gapEl.addEventListener("input", (e) => {
        it.gap = parseInt(e.target.value, 10);
        const gv = slot.querySelector("#ed-gap-v");
        if (gv) gv.textContent = it.gap;
      });
      gapEl.addEventListener("change", () => {
        this._renderTiles(); this._updateSimFromState(); this._autoSend();
      });
    }
    slot.querySelector("#ed-del").addEventListener("click", () => {
      this._items.splice(this._editIndex, 1);
      this._editIndex = null; this._renderEditor(); this._renderTiles(); this._updateSimFromState(); this._autoSend();
    });
    slot.querySelector("#ed-close").addEventListener("click", () => {
      this._editIndex = null; this._renderEditor();
    });
  }

  async _loadComposition() {
    if (!this._hass || !this._dev) return;
    try {
      const res = await this._hass.callWS({
        type: "ipixel_color/get_composition", address: this._dev.address,
      });
      this._items = res.items || [];
      this._sep = res.separator || 2;
      if (res.panel_width && res.panel_height)
        this._sizeOverride = { w: res.panel_width, h: res.panel_height };
    } catch (e) { this._items = []; }
    this.shadowRoot.querySelectorAll(".sep-chip").forEach((c) =>
      c.classList.toggle("active", parseInt(c.dataset.v, 10) === (this._sep || 2)));
    this._renderTiles();
    this._updateSimFromState();
  }

  async _saveComposition(silent = false) {
    if (!this._hass || !this._dev) return;
    const items = (this._items || []).filter(
      (it) => (it.type === "text" ? (it.text || "").trim() : it.entity_id));
    this._setBusy(true);
    try {
      const res = await this._hass.callWS({
        type: "ipixel_color/set_composition", address: this._dev.address, items,
        separator: this._sep || 2,
      });
      if (!silent) {
        this._toast(res.displayed ? "Inviato al display ✓" : "Salvato, ma invio fallito", !res.displayed);
      } else if (!res.displayed) {
        this._toast("Invio fallito", true);
      }
      // aggiorna subito l'anteprima fedele (striscia resa dal server)
      this._pollFrame();
    } catch (e) {
      this._toast("Errore: " + (e.message || e), true);
    } finally { this._setBusy(false); }
  }

  /* Invio automatico "subito subito": a ogni modifica delle caselle la
     composizione parte da sola verso il display, senza premere "Invia al
     display". Debounce ~400ms per non intasare il BLE; se c'è un invio in
     corso riprova a breve. Si può spegnere con this._autoApply = false. */
  _autoSend(delay = 400) {
    if (this._autoApply === false) return;
    if (!this._items) return;                 // niente da inviare finché non carica
    clearTimeout(this._autoT);
    this._autoT = setTimeout(() => {
      if (this._busy) { this._autoSend(250); return; }
      this._saveComposition(true);
    }, delay);
  }

  /* Rimanda i sensori sul display: toglie scritta/icona/immagine.
     Funziona da qualunque pagina (se serve, la composizione la
     chiede al server). */
  async _restoreComposition() {
    if (!this._hass || !this._dev) return;
    if (this._busy) return this._toast("Attendi: invio ancora in corso…", true);
    this._setBusy(true);
    try {
      let items = this._items;
      let sep = this._sep;
      if (!items) {
        const res = await this._hass.callWS({
          type: "ipixel_color/get_composition", address: this._dev.address });
        items = (res && res.items) || [];
        sep = (res && res.separator) || sep;
      }
      const payload = {
        type: "ipixel_color/set_composition", address: this._dev.address, items };
      if (sep) payload.separator = sep;
      await this._hass.callWS(payload);
      this._toast("Sensori di nuovo sul display ✓");
    } catch (e) {
      this._toast("Errore: " + ((e && e.message) || e), true);
    } finally { this._setBusy(false); }
  }

  /* Colore testo/sfondo con la guardia: se il display è occupato da
     orologio/rotazione/immagini si salva e basta. Usato dalla pagina
     Colori e dalla card "Stile del testo". */
  async _sendColor(suffix, hex) {
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16),
      b = parseInt(hex.slice(5, 7), 16);
    if (this._busy) return this._toast("Attendi: invio ancora in corso…", true);
    this._setBusy(true);
    this._toast("Applico il colore…");
    try {
      await this._hass.callService("light", "turn_on", {
        entity_id: this._eid("light", suffix), rgb_color: [r, g, b], brightness: 255 });
      let st2 = null;
      try {
        st2 = await this._hass.callWS({
          type: "ipixel_color/status", address: this._dev.address });
      } catch (e) { /* server vecchio */ }
      if (st2 && (st2.running || (st2.owner && st2.owner !== "composition"))) {
        this._toast("Colore salvato ✓");
        return;
      }
      await this._hass.callService("button", "press", {
        entity_id: this._eid("button", "update_display") });
      this._toast("Colore applicato ✓");
    } catch (e) { this._toast("Errore: " + (e.message || e), true); }
    finally { this._setBusy(false); }
  }

  /* ================================================ interazioni */

  _bindPage() {
    const $ = (id) => this.shadowRoot.getElementById(id);
    if (!this._dev) return;

    // Sezioni richiudibili: clic sul titolo piega/espande la card.
    // Lo stato (per titolo) resta durante la sessione.
    this._collapsed = this._collapsed || new Set();
    this.shadowRoot.querySelectorAll(".card > h2").forEach((h) => {
      const key = h.textContent.trim();
      if (this._collapsed.has(key)) h.parentElement.classList.add("collapsed");
      h.addEventListener("click", () => {
        const on = h.parentElement.classList.toggle("collapsed");
        if (on) this._collapsed.add(key); else this._collapsed.delete(key);
      });
    });

    // --- Testo
    if ($("send-comp")) {
      // Applica una regolazione e aggiorna subito il display — ma SOLO
      // se il display sta mostrando la composizione: se c'è in moto un
      // orologio/timer/rotazione, o un'immagine/icona/scritta, il
      // valore si salva e basta (niente "reset" di quel che si vede)
      const applyNow = async (domain, service, data) => {
        if (this._busy) return this._toast("Attendi: invio in corso…", true);
        this._setBusy(true);
        try {
          await this._hass.callService(domain, service, data);
          let st = null;
          try {
            st = await this._hass.callWS({
              type: "ipixel_color/status", address: this._dev.address });
          } catch (e) { /* server vecchio: applica come prima */ }
          if (st && (st.running || (st.owner && st.owner !== "composition"))) {
            this._toast("Salvato: si applica al prossimo testo ✓");
          } else {
            await this._hass.callService("button", "press", {
              entity_id: this._eid("button", "update_display") });
            this._toast("Applicato ✓");
          }
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
        finally { this._setBusy(false); }
      };
      this._applyNow = applyNow;

      $("send-comp").addEventListener("click", () => this._saveComposition());
      // "Tutte le caselle": applica a ogni entità e invia subito
      this.shadowRoot.querySelectorAll(".all-chip").forEach((c) =>
        c.addEventListener("click", () => {
          const k = c.dataset.k, v = c.dataset.v;
          for (const it of this._items || []) {
            if (it.type === "text" || !it.entity_id) continue;
            if (k === "icon") {
              if (v === "auto") delete it.icon; else it.icon = "";
            } else if (k === "unit") {
              it.show_unit = v === "on";
            } else if (k === "dec") {
              it.decimals = parseInt(v, 10);
            }
          }
          this._renderTiles(); this._renderEditor(); this._updateSimFromState();
          this._saveComposition();
        }));
      this.shadowRoot.querySelectorAll(".sep-chip").forEach((c) =>
        c.addEventListener("click", () => {
          this._sep = parseInt(c.dataset.v, 10);
          this.shadowRoot.querySelectorAll(".sep-chip").forEach((x) =>
            x.classList.toggle("active", x === c));
          this._updateSimFromState();
          this._saveComposition(); // applica subito anche sul display
        }));
      if (this._items === null) this._loadComposition();
      else {
        this._renderTiles();
        this.shadowRoot.querySelectorAll(".sep-chip").forEach((c) =>
          c.classList.toggle("active", parseInt(c.dataset.v, 10) === (this._sep || 2)));
      }
      $("font").addEventListener("change", () =>
        this._call("select", "select_option", {
          entity_id: this._eid("select", "font"), option: $("font").value }));
      $("tx-color").addEventListener("change", () =>
        this._sendColor("text_color", $("tx-color").value));
      $("tx-bg").addEventListener("change", () =>
        this._sendColor("background_color", $("tx-bg").value));
      this.shadowRoot.querySelectorAll(".mode-chip").forEach((c) =>
        c.addEventListener("click", () =>
          this._call("select", "select_option", {
            entity_id: this._eid("select", "mode"), option: c.dataset.v })));
      this.shadowRoot.querySelectorAll(".anim-chip").forEach((c) =>
        c.addEventListener("click", () =>
          applyNow("number", "set_value", {
            entity_id: this._eid("number", "text_animation"), value: c.dataset.v })));
      $("fsize").addEventListener("input", () => {
        const v = parseInt($("fsize").value, 10);
        $("fsize-v").textContent = v === 0 ? "Auto" : v;
      });
      $("fsize").addEventListener("change", () =>
        applyNow("number", "set_value", {
          entity_id: this._eid("number", "font_size"), value: $("fsize").value }));
      $("speed").addEventListener("change", () =>
        applyNow("number", "set_value", {
          entity_id: this._eid("number", "text_speed"), value: $("speed").value }));
      $("rainbow").addEventListener("change", () =>
        applyNow("number", "set_value", {
          entity_id: this._eid("number", "text_rainbow"), value: $("rainbow").value }));
      // "Invia questo testo" (card Avanzate) rimosso su richiesta.

      // La card "Scritta libera" è stata rimossa: Grandezza/Velocità/
      // Arcobaleno (globali della riga che scorre) vivono ora in "Stile
      // del testo"; il loro aggancio è qui sopra (fsize/speed/rainbow).
    }

    // --- Galleria
    this.shadowRoot.querySelectorAll(".gal-tile").forEach((t) =>
      t.addEventListener("click", () => this._sendGalleryGif(t.dataset.f)));

    // --- Ritmo: equalizzatore di bordo (demo mossa dal server)
    if ($("ry-start")) {
      this._ryStyle = this._ryStyle == null ? 0 : this._ryStyle;
      const ryChips = this.shadowRoot.querySelectorAll(".ry-style");
      const ryMark = () => ryChips.forEach((c) =>
        c.classList.toggle("active", parseInt(c.dataset.v, 10) === this._ryStyle));
      ryMark();
      ryChips.forEach((c) => c.addEventListener("click", () => {
        this._ryStyle = parseInt(c.dataset.v, 10); ryMark();
      }));
      $("ry-start").addEventListener("click", async () => {
        try {
          await this._hass.callWS({
            type: "ipixel_color/rhythm", address: this._dev.address,
            action: "start", style: this._ryStyle,
          });
          this._toast("Equalizzatore in moto ✓");
          this._refreshRotStatus();
        } catch (e) { this._toast("Errore: " + ((e && e.message) || e), true); }
      });
      $("ry-stop").addEventListener("click", async () => {
        try {
          await this._hass.callWS({
            type: "ipixel_color/rhythm", address: this._dev.address,
            action: "stop",
          });
        } catch (e) { /* comunque ripristina */ }
        this._restoreComposition();
        this._refreshRotStatus();
      });
    }

    // --- Rotazione automatica
    if ($("rot-start")) {
      // Scaletta: bottoni di aggiunta + resa iniziale
      this._plRender();
      const plAdd = (step) => {
        this._playlist = this._playlist || [];
        if (this._playlist.length >= 20)
          return this._toast("Massimo 20 passi", true);
        this._playlist.push(step);
        this._plRender(); this._plSave();
      };
      $("pl-add-s").addEventListener("click", () =>
        plAdd({ type: "sensors", seconds: 4, per_ent: true }));
      $("pl-add-i").addEventListener("click", () => {
        const first = ($("rot-anim") && $("rot-anim").value) || "sfondo_01.gif";
        plAdd({ type: "image", file: first, seconds: 15 });
      });
      $("pl-add-c").addEventListener("click", () =>
        plAdd({ type: "clock", seconds: 60 }));

      // Aggiunge alla tendina anche le immagini della libreria personale
      this._hass.callWS({ type: "ipixel_color/library", action: "list" })
        .then((res) => {
          const items = (res && res.items) || [];
          this._libItems = items;
          this._plRender();
          const sel = $("rot-anim");
          if (!sel) return;
          const grp = document.createElement("optgroup");
          grp.label = "Le mie immagini";
          if (items.length) {
            items.forEach((it, i) => {
              const o = document.createElement("option");
              o.value = it.file;
              o.textContent = `Immagine ${i + 1}`;
              grp.appendChild(o);
            });
          } else {
            const o = document.createElement("option");
            o.disabled = true;
            o.textContent = "(vuota: invia un'immagine e apparirà qui)";
            grp.appendChild(o);
          }
          sel.appendChild(grp);
        }).catch(() => {});
      const clamp = (v, lo, hi, def) => {
        const n = parseFloat(String(v).replace(",", "."));
        if (isNaN(n)) return def;
        return Math.min(hi, Math.max(lo, Math.round(n)));
      };
      const wsRot = async (action) => {
        if (action === "start" && !(this._playlist || []).length)
          return this._toast("Aggiungi almeno un passo alla scaletta", true);
        this._setBusy(true);
        try {
          await this._hass.callWS({
            type: "ipixel_color/timer", address: this._dev.address,
            action, mode: "rotation", seconds: 60,
          });
          this._toast(action === "start" ? "Rotazione avviata ✓" : "Rotazione fermata ✓");
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
        finally { this._setBusy(false); this._refreshRotStatus(); }
      };
      $("rot-start").addEventListener("click", () => wsRot("start"));
      $("rot-stop").addEventListener("click", () => wsRot("stop"));
      this._refreshRotStatus();
    }

    // --- Disegno
    if ($("paint")) {
      this._initPaintData();
      this._drawPaint();
      this._drawColor = this._drawColor || "#19e5ff";
      const cv = $("paint");
      const paintAt = (ev) => {
        const r = cv.getBoundingClientRect();
        const x = Math.floor((ev.clientX - r.left) / r.width * this._paintW);
        const y = Math.floor((ev.clientY - r.top) / r.height * this._paintH);
        if (x < 0 || y < 0 || x >= this._paintW || y >= this._paintH) return;
        this._paintData[y * this._paintW + x] =
          this._drawTool === "eraser" ? null : this._drawColor;
        this._drawPaint();
        // In diretta: la pennellata accende subito il LED (fun mode)
        if ($("dr-live") && $("dr-live").checked) {
          const col = this._drawTool === "eraser"
            ? "000000" : this._drawColor.slice(1);
          const key = x + "," + y + "," + col;
          if (this._lastLivePx !== key) {
            this._lastLivePx = key;
            this._hass.callWS({
              type: "ipixel_color/pixel", address: this._dev.address,
              x, y, color: col,
            }).catch(() => {});
          }
        }
      };
      let down = false;
      cv.addEventListener("pointerdown", (e) => {
        down = true; cv.setPointerCapture(e.pointerId); paintAt(e); e.preventDefault();
      });
      cv.addEventListener("pointermove", (e) => { if (down) paintAt(e); });
      const lift = () => { if (down) { down = false; this._savePaint(); } };
      cv.addEventListener("pointerup", lift);
      cv.addEventListener("pointercancel", lift);
      const setTool = (t) => {
        this._drawTool = t;
        $("dr-pen").classList.toggle("active", t === "pen");
        $("dr-eraser").classList.toggle("active", t === "eraser");
      };
      setTool("pen");
      $("dr-pen").addEventListener("click", () => setTool("pen"));
      $("dr-eraser").addEventListener("click", () => setTool("eraser"));
      $("dr-color").addEventListener("input", () => {
        this._drawColor = $("dr-color").value; setTool("pen");
      });
      this.shadowRoot.querySelectorAll(".dr-sw").forEach((s) =>
        s.addEventListener("click", () => {
          this._drawColor = s.dataset.c; $("dr-color").value = s.dataset.c; setTool("pen");
        }));
      $("dr-live").addEventListener("change", async () => {
        const on = $("dr-live").checked;
        try {
          await this._hass.callWS({
            type: "ipixel_color/fun_mode", address: this._dev.address,
            enable: on,
          });
          this._toast(on
            ? "In diretta: disegna e guardati il pannello ✓"
            : "Diretta spenta ✓");
        } catch (e) {
          $("dr-live").checked = false;
          this._toast("Errore: " + ((e && e.message) || e), true);
        }
      });
      $("dr-fill").addEventListener("click", () => {
        this._paintData.fill(this._drawColor); this._drawPaint(); this._savePaint();
      });
      $("dr-clear").addEventListener("click", () => {
        this._paintData.fill(null); this._drawPaint(); this._savePaint();
      });
      $("dr-send").addEventListener("click", async () => {
        const off = document.createElement("canvas");
        off.width = this._paintW; off.height = this._paintH;
        const c = off.getContext("2d");
        c.fillStyle = "#000"; c.fillRect(0, 0, off.width, off.height);
        for (let y = 0; y < this._paintH; y++) for (let x = 0; x < this._paintW; x++) {
          const col = this._paintData[y * this._paintW + x];
          if (col) { c.fillStyle = col; c.fillRect(x, y, 1, 1); }
        }
        const b64 = off.toDataURL("image/png").split(",")[1];
        await this._call("ipixel_color", "display_image", {
          address: this._dev.address, image: b64, resize_method: "crop",
        });
      });
    }

    // --- Timer / cronometro
    if ($("tm-start")) {
      let chosen = 300;
      const chooseChip = (v) => {
        chosen = v;
        this.shadowRoot.querySelectorAll(".tm-preset").forEach((c) =>
          c.classList.toggle("active", parseInt(c.dataset.v, 10) === v));
      };
      chooseChip(300);
      this.shadowRoot.querySelectorAll(".tm-preset").forEach((c) =>
        c.addEventListener("click", () => { $("tm-min").value = ""; chooseChip(parseInt(c.dataset.v, 10)); }));
      const wsTimer = async (action, mode, seconds) => {
        this._setBusy(true);
        try {
          await this._hass.callWS({
            type: "ipixel_color/timer", address: this._dev.address,
            action, mode, seconds: seconds || 60,
          });
          this._toast(action === "start" ? "Avviato ✓" : "Fermato ✓");
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
        finally { this._setBusy(false); }
      };
      $("tm-start").addEventListener("click", () => {
        const custom = parseFloat(($("tm-min").value || "").replace(",", "."));
        // Il server accetta al massimo 24 ore
        const secs = custom > 0 ? Math.min(86400, Math.round(custom * 60)) : chosen;
        wsTimer("start", "down", secs);
      });
      $("tm-stop").addEventListener("click", () => wsTimer("stop", "down"));
      // Cronometro NATIVO: conta dentro il display, zero BLE
      const nativeTimer = async (action) => {
        try {
          await this._hass.callWS({
            type: "ipixel_color/native_timer", address: this._dev.address,
            action,
          });
          this._toast(action === "start" ? "Cronometro avviato ✓"
            : action === "pause" ? "Pausa ✓" : "Fermato ✓");
        } catch (e) {
          if (action !== "pause") return wsTimer(action, "up");
          this._toast("Errore: " + ((e && e.message) || e), true);
        }
      };
      $("cr-start").addEventListener("click", () => nativeTimer("start"));
      $("cr-pause").addEventListener("click", () => nativeTimer("pause"));
      $("cr-stop").addEventListener("click", () => nativeTimer("stop"));
    }

    // --- Segnapunti
    if ($("sc-a")) {
      if (!this._score) this._score = { a: 0, b: 0 };
      const sendScore = async () => {
        $("sc-a").textContent = this._score.a;
        $("sc-b").textContent = this._score.b;
        try {
          // Tabellone NATIVO del firmware (lo disegna il display)
          await this._hass.callWS({
            type: "ipixel_color/scores", address: this._dev.address,
            p1: this._score.a, p2: this._score.b,
          });
        } catch (e) {
          // Server vecchio: testo semplice come prima
          try {
            await this._hass.callWS({
              type: "ipixel_color/show_text", address: this._dev.address,
              text: `${this._score.a}:${this._score.b}`, animation: 0,
            });
          } catch (e2) { this._toast("Errore: " + (e2.message || e2), true); }
        }
      };
      const bump = (k, d) => () => {
        this._score[k] = Math.min(99, Math.max(0, this._score[k] + d));
        sendScore();
      };
      $("sc-a-up").addEventListener("click", bump("a", 1));
      $("sc-a-dn").addEventListener("click", bump("a", -1));
      $("sc-b-up").addEventListener("click", bump("b", 1));
      $("sc-b-dn").addEventListener("click", bump("b", -1));
      $("sc-reset").addEventListener("click", () => {
        this._score = { a: 0, b: 0 }; sendScore();
      });
    }

    // --- Orologio
    if ($("clock-on")) {
      this.shadowRoot.querySelectorAll(".clock-chip").forEach((c) =>
        c.addEventListener("click", () =>
          this._call("select", "select_option", {
            entity_id: this._eid("select", "clock_style"), option: c.dataset.v })));
      $("c24").addEventListener("change", () =>
        this._call("switch", $("c24").checked ? "turn_on" : "turn_off", {
          entity_id: this._eid("switch", "clock_24h") }));
      $("cdate").addEventListener("change", () =>
        this._call("switch", $("cdate").checked ? "turn_on" : "turn_off", {
          entity_id: this._eid("switch", "clock_show_date") }));
      $("clock-on").addEventListener("click", () =>
        this._call("select", "select_option", {
          entity_id: this._eid("select", "mode"), option: "clock" }));
      $("sync-time").addEventListener("click", () =>
        this._call("button", "press", { entity_id: this._eid("button", "sync_time") }));
      const wsClock = async (action, quiet) => {
        this._setBusy(true);
        try {
          const payload = {
            type: "ipixel_color/timer", address: this._dev.address,
            action, mode: "clock_it", seconds: 60,
          };
          if (action === "start") payload.clock_style = $("clock-it-style").value;
          await this._hass.callWS(payload);
          if (!quiet)
            this._toast(action === "start" ? "Orologio italiano avviato ✓" : "Fermato ✓");
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
        finally { this._setBusy(false); }
      };
      $("clock-it-on").addEventListener("click", () => wsClock("start"));
      $("clock-it-off").addEventListener("click", () => wsClock("stop"));
      // La tendina ricorda l'ultimo stile scelto
      // Grandezza = altezza in pixel (5..16). Converte le vecchie
      // etichette s/m/g e dà un'etichetta a parole allo slider.
      const sizeToPx = (s) => {
        if (s == null) return 7;
        if (typeof s === "number") return s;
        const map = { s: 5, m: 7, g: 14 };
        return map[s] != null ? map[s] : (parseInt(s, 10) || 7);
      };
      const sizeWord = (v) =>
        v <= 6 ? "mini" : v <= 9 ? "piccola" : v <= 12 ? "media" : "grande";
      this._hass.callWS({
        type: "ipixel_color/status", address: this._dev.address,
      }).then((st) => {
        if (st.clock_style) $("clock-it-style").value = st.clock_style;
        if (st.clock_icon) $("clock-icon").value = st.clock_icon;
        if (st.clock_icon_color) $("clock-icon-color").value = "#" + st.clock_icon_color;
        this._clkSize = sizeToPx(st.clock_size);
        if ($("clk-size")) {
          $("clk-size").value = this._clkSize;
          $("clk-size-v").textContent = sizeWord(this._clkSize);
        }
        if ($("clk-gap")) {
          $("clk-gap").value = st.clock_gap == null ? 2 : st.clock_gap;
          $("clk-gap-v").textContent = $("clk-gap").value;
        }
      }).catch(() => {});
      const saveClockStyle = async () => {
        // Ricorda subito la scelta (vale anche per la fase orologio
        // della rotazione, che legge lo stile salvato)
        try {
          await this._hass.callWS({
            type: "ipixel_color/set_clock_style", address: this._dev.address,
            clock_style: $("clock-it-style").value,
            icon: ($("clock-icon").value || "heart").trim(),
            icon_color: $("clock-icon-color").value.slice(1),
            size: parseInt(this._clkSize, 10) || 7,
            gap: parseInt(($("clk-gap") && $("clk-gap").value) || "2", 10) || 0,
          });
        } catch (e) { /* server vecchio: lo salva comunque l'avvio */ }
        // Se l'orologio è in moto, riparte subito con le scelte nuove
        try {
          const st = await this._hass.callWS({
            type: "ipixel_color/status", address: this._dev.address,
          });
          if (st.running && st.mode === "clock_it") {
            await wsClock("start", true);
            this._toast("Orologio aggiornato ✓");
          } else if (st.running && st.mode === "rotation") {
            this._toast("Salvato: la rotazione lo usa dal prossimo giro ✓");
          } else {
            this._toast("Salvato: si userà all'avvio ✓");
          }
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
      };
      $("clock-it-style").addEventListener("change", saveClockStyle);
      // Toccare l'icona vuol dire volerla vedere: lo stile passa a O
      // da solo (è l'unico che la mostra)
      const saveWithStyleO = () => {
        if ($("clock-it-style").value !== "o") $("clock-it-style").value = "o";
        saveClockStyle();
      };
      $("clock-icon").addEventListener("change", saveWithStyleO);
      $("clock-icon-color").addEventListener("change", saveWithStyleO);
      if ($("clk-size")) {
        $("clk-size").addEventListener("input", () => {
          this._clkSize = parseInt($("clk-size").value, 10);
          $("clk-size-v").textContent = sizeWord(this._clkSize);
        });
        $("clk-size").addEventListener("change", saveClockStyle);
      }
      if ($("clk-gap")) {
        $("clk-gap").addEventListener("input", () => {
          $("clk-gap-v").textContent = $("clk-gap").value;
        });
        $("clk-gap").addEventListener("change", saveClockStyle);
      }
    }

    // --- Immagine: memoria del display (slot di bordo)
    if ($("slot-list")) {
      const renderSlots = (reg) => {
        const box = $("slot-list");
        if (!box) return;
        box.innerHTML = [1, 2, 3, 4].map((n) => `
          <div class="row">
            <b style="min-width:60px">Slot ${n}</b>
            <span style="color:var(--muted);font-size:12px;min-width:120px">
              ${reg[String(n)] || "vuoto"}</span>
            <button class="chip sl-save" data-n="${n}">💾 Salva qui</button>
            <button class="chip sl-show" data-n="${n}">▶ Mostra</button>
            <button class="chip sl-del" data-n="${n}"
              style="color:#ff4d6d;border-color:#ff4d6d">✕</button>
          </div>`).join("");
        const act = async (action, n, okMsg) => {
          if (this._busy) return this._toast("Attendi: invio in corso…", true);
          this._setBusy(true);
          try {
            await this._hass.callWS({
              type: "ipixel_color/slot", address: this._dev.address,
              action, slot: n,
            });
            this._toast(okMsg);
            const st = await this._hass.callWS({
              type: "ipixel_color/status", address: this._dev.address });
            renderSlots(st.device_slots || {});
          } catch (e) {
            this._toast("Errore: " + ((e && e.message) || e), true);
          } finally { this._setBusy(false); }
        };
        box.querySelectorAll(".sl-save").forEach((b) =>
          b.addEventListener("click", () => act("save",
            parseInt(b.dataset.n, 10), "Salvata nella memoria del display ✓")));
        box.querySelectorAll(".sl-show").forEach((b) =>
          b.addEventListener("click", () => act("show",
            parseInt(b.dataset.n, 10), "Slot sul display ✓")));
        box.querySelectorAll(".sl-del").forEach((b) =>
          b.addEventListener("click", () => act("delete",
            parseInt(b.dataset.n, 10), "Slot svuotato ✓")));
      };
      renderSlots({});
      this._hass.callWS({ type: "ipixel_color/status", address: this._dev.address })
        .then((st) => renderSlots(st.device_slots || {}))
        .catch(() => {});
    }

    // --- Immagine
    if ($("drop")) {
      this._loadLibrary();
      const drop = $("drop"), file = $("file");
      drop.addEventListener("click", () => file.click());
      drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("over"));
      drop.addEventListener("drop", (e) => {
        e.preventDefault(); drop.classList.remove("over");
        if (e.dataTransfer.files[0]) this._loadImage(e.dataTransfer.files[0]);
      });
      file.addEventListener("change", () => { if (file.files[0]) this._loadImage(file.files[0]); });
      this.shadowRoot.querySelectorAll(".rez-chip").forEach((c) =>
        c.addEventListener("click", () => {
          this._resizeMethod = c.dataset.v;
          this.shadowRoot.querySelectorAll(".rez-chip").forEach((x) =>
            x.classList.toggle("active", x === c));
          this._drawPreview();
        }));
      $("send-img").addEventListener("click", () => this._sendImage());
      $("send-url").addEventListener("click", async () => {
        const url = ($("url-img").value || "").trim();
        if (!url.startsWith("http")) return this._toast("Incolla un link valido", true);
        if (this._busy) return this._toast("Attendi: invio ancora in corso…", true);
        this._setBusy(true);
        this._toast("Scarico e invio… può volerci un po'");
        try {
          const res = await this._hass.callWS({
            type: "ipixel_color/display_url", address: this._dev.address,
            url, resize_method: this._resizeMethod,
          });
          if (res.preview) {
            const cvPrev = $("preview");
            if (cvPrev) cvPrev.hidden = true;
            let gp = $("gifprev");
            if (!gp && cvPrev) {
              gp = document.createElement("img");
              gp.id = "gifprev"; gp.className = "preview";
              gp.style.cssText = "width:512px;max-width:100%;image-rendering:pixelated";
              cvPrev.parentNode.insertBefore(gp, cvPrev);
            }
            if (gp) {
              gp.hidden = false;
              gp.src = `data:image/${res.preview_ext || "gif"};base64,${res.preview}`;
            }
          }
          this._toast(res.displayed ? "Inviata al display ✓" : "Scaricata, ma invio fallito", !res.displayed);
        } catch (e) {
          this._toast("Errore: " + (e.message || e), true);
        } finally { this._setBusy(false); }
      });
      // La card "Icona" a schermo intero è stata rimossa: ora le icone e
      // le emoji si aggiungono come caselle della griglia (scorrono col testo).
    }

    // --- Colori
    if ($("col-text")) {
      const sendColor = (suffix, hex) => this._sendColor(suffix, hex);
      $("col-text").addEventListener("change", () => sendColor("text_color", $("col-text").value));
      $("col-bg").addEventListener("change", () => sendColor("background_color", $("col-bg").value));
      this.shadowRoot.querySelectorAll("#sw-text .sw").forEach((s) =>
        s.addEventListener("click", () => sendColor("text_color", s.dataset.c)));
      this.shadowRoot.querySelectorAll("#sw-bg .sw").forEach((s) =>
        s.addEventListener("click", () => sendColor("background_color", s.dataset.c)));
      $("bright").addEventListener("input", () => { $("bright-v").textContent = $("bright").value; });
      $("bright").addEventListener("change", () =>
        this._call("number", "set_value", {
          entity_id: this._eid("number", "brightness"), value: $("bright").value }));
    }

    // --- Sistema: proporzioni anteprima
    if ($("stretch")) {
      const pct = Math.round(this._simStretch() * 100);
      $("stretch").value = pct;
      $("stretch-v").textContent = pct + "%";
      $("stretch").addEventListener("input", () => {
        const v = parseInt($("stretch").value, 10);
        this._stretch = v / 100;
        $("stretch-v").textContent = v + "%";
        try { localStorage.setItem("ipixel_sim_stretch", String(this._stretch)); } catch (e) {}
      });
      const spd = this._simSpeedPct();
      $("simspeed").value = spd;
      $("simspeed-v").textContent = spd + "%";
      $("simspeed").addEventListener("input", () => {
        const v = parseInt($("simspeed").value, 10);
        this._simSpd = v;
        $("simspeed-v").textContent = v + "%";
        try { localStorage.setItem("ipixel_sim_speedpct", String(v)); } catch (e) {}
      });
    }

    // --- Sistema: accensione/spegnimento programmati di bordo
    if ($("sch-set")) {
      const dayChips = this.shadowRoot.querySelectorAll(".sch-day");
      dayChips.forEach((c) => c.addEventListener("click", () =>
        c.classList.toggle("active")));
      const schStatus = (cfg) => {
        const el = $("sch-status");
        if (!el) return;
        if (!cfg || !cfg.enabled) { el.textContent = "Non impostata."; return; }
        const names = { mon: "Lun", tue: "Mar", wed: "Mer", thu: "Gio",
          fri: "Ven", sat: "Sab", sun: "Dom" };
        const dd = (cfg.days && cfg.days.length)
          ? cfg.days.map((d) => names[d] || d).join(" ") : "tutti i giorni";
        const hh = (h, m) => `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
        el.textContent = `Attiva: accende ${hh(cfg.on_hour, cfg.on_minute)}`
          + `, spegne ${hh(cfg.off_hour, cfg.off_minute)} — ${dd}`;
      };
      // Precompila dagli ultimi valori salvati
      this._hass.callWS({ type: "ipixel_color/status", address: this._dev.address })
        .then((st) => {
          const cfg = st.schedule_cfg || {};
          if (cfg.on_hour != null)
            $("sch-on").value = `${String(cfg.on_hour).padStart(2, "0")}:${String(cfg.on_minute).padStart(2, "0")}`;
          if (cfg.off_hour != null)
            $("sch-off").value = `${String(cfg.off_hour).padStart(2, "0")}:${String(cfg.off_minute).padStart(2, "0")}`;
          (cfg.days || []).forEach((d) => {
            dayChips.forEach((c) => {
              if (c.dataset.v === d) c.classList.add("active");
            });
          });
          schStatus(cfg);
        }).catch(() => {});
      const sendSchedule = async (enabled) => {
        const [onH, onM] = ($("sch-on").value || "08:00").split(":").map(Number);
        const [offH, offM] = ($("sch-off").value || "23:00").split(":").map(Number);
        const days = Array.from(dayChips)
          .filter((c) => c.classList.contains("active"))
          .map((c) => c.dataset.v);
        if (this._busy) return this._toast("Attendi: invio in corso…", true);
        this._setBusy(true);
        try {
          await this._hass.callWS({
            type: "ipixel_color/schedule", address: this._dev.address,
            enabled, on_hour: onH, on_minute: onM,
            off_hour: offH, off_minute: offM, days,
          });
          this._toast(enabled
            ? "Programmazione scritta nel display ✓"
            : "Programmazione disattivata ✓");
          schStatus(enabled
            ? { enabled, on_hour: onH, on_minute: onM,
                off_hour: offH, off_minute: offM, days }
            : null);
        } catch (e) {
          this._toast("Errore: " + ((e && e.message) || e), true);
        } finally { this._setBusy(false); }
      };
      $("sch-set").addEventListener("click", () => sendSchedule(true));
      $("sch-del").addEventListener("click", () => sendSchedule(false));
    }

    // --- Sistema: libera il BLE per l'app del telefono
    if ($("rel-app")) {
      const relStatus = (s) => {
        const el = $("rel-status");
        if (!el) return;
        if (s > 0) {
          el.textContent = `📱 Libero per l'app ancora ${Math.ceil(s / 60)} min`;
          el.style.color = "var(--neon)";
        } else {
          el.textContent = "Collegato a Home Assistant";
          el.style.color = "var(--muted)";
        }
      };
      const wsRelease = async (action) => {
        try {
          const res = await this._hass.callWS({
            type: "ipixel_color/release", address: this._dev.address, action,
          });
          relStatus(res.released_s || 0);
          this._toast(action === "release"
            ? "Collegamento liberato: apri pure l'app ✓"
            : "Mi ricollego al prossimo comando ✓");
        } catch (e) { this._toast("Errore: " + (e.message || e), true); }
      };
      $("rel-app").addEventListener("click", () => wsRelease("release"));
      $("rel-end").addEventListener("click", () => wsRelease("reconnect"));
      this._hass.callWS({
        type: "ipixel_color/status", address: this._dev.address,
      }).then((st) => relStatus(st.released_s || 0)).catch(() => {});
    }

    // --- Sistema
    if ($("power")) {
      $("power").addEventListener("change", () =>
        this._call("switch", $("power").checked ? "turn_on" : "turn_off", {
          entity_id: this._eid("switch", null) }));
      $("autoupd").addEventListener("change", () =>
        this._call("switch", $("autoupd").checked ? "turn_on" : "turn_off", {
          entity_id: this._eid("switch", "auto_update") }));
      $("force-update").addEventListener("click", () =>
        this._call("button", "press", { entity_id: this._eid("button", "update_display") }));
    }
  }

  /* =============================================== disegno */

  _initPaintData() {
    const { w, h } = this._panelSize();
    if (this._paintData && this._paintW === w && this._paintH === h) return;
    this._paintW = w; this._paintH = h;
    this._paintData = new Array(w * h).fill(null);
    try {
      const saved = JSON.parse(localStorage.getItem("ipixel_paint") || "null");
      if (saved && saved.w === w && saved.h === h && Array.isArray(saved.d))
        this._paintData = saved.d;
    } catch (e) { /* senza bozza */ }
  }

  _savePaint() {
    try {
      localStorage.setItem("ipixel_paint",
        JSON.stringify({ w: this._paintW, h: this._paintH, d: this._paintData }));
    } catch (e) { /* ignora */ }
  }

  _drawPaint() {
    const cv = this.shadowRoot.getElementById("paint");
    if (!cv) return;
    const w = this._paintW, h = this._paintH, s = 12;
    if (cv.width !== w * s) { cv.width = w * s; cv.height = h * s; }
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cv.width, cv.height);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const c = this._paintData[y * w + x];
      if (c) { ctx.fillStyle = c; ctx.fillRect(x * s + 1, y * s + 1, s - 2, s - 2); }
    }
    ctx.strokeStyle = "#14324a"; ctx.lineWidth = 1;
    for (let x = 0; x <= w; x++) { ctx.beginPath(); ctx.moveTo(x * s + 0.5, 0); ctx.lineTo(x * s + 0.5, cv.height); ctx.stroke(); }
    for (let y = 0; y <= h; y++) { ctx.beginPath(); ctx.moveTo(0, y * s + 0.5); ctx.lineTo(cv.width, y * s + 0.5); ctx.stroke(); }
  }

  /* =============================================== immagini */

  _panelSize() {
    if (this._sizeOverride) return this._sizeOverride;
    const w = parseInt(this._val("sensor", "display_width", "64"), 10) || 64;
    const h = parseInt(this._val("sensor", "display_height", "16"), 10) || 16;
    return { w, h };
  }

  _loadImage(fileObj) {
    if (fileObj.size > 15 * 1024 * 1024)
      return this._toast("File troppo grande (max 15 MB)", true);
    this._gifB64 = null;
    this._gifFile = null;
    const cvPrev = this.shadowRoot.getElementById("preview");
    const oldGif = this.shadowRoot.getElementById("gifprev");

    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result;
      const b64 = (dataUrl.split(",")[1]) || "";
      // Dal telefono la GIF arriva spesso senza ".gif" nel nome e senza
      // tipo "image/gif": la riconosciamo dai primi byte del file, perche'
      // ogni GIF comincia con "GIF8" (in base64: "R0lG").
      const isGif = b64.startsWith("R0lG")
        || /\.gif$/i.test(fileObj.name || "")
        || fileObj.type === "image/gif";
      if (isGif) {
        // GIF animata: niente canvas (la appiattirebbe), anteprima con <img>;
        // per l'invio tengo il File GREZZO (va via HTTP, non base64 sul WS)
        this._gifFile = fileObj;
        this._gifB64 = b64;
        if (cvPrev) cvPrev.hidden = true;
        let gp = oldGif;
        if (!gp && cvPrev) {
          gp = document.createElement("img");
          gp.id = "gifprev";
          gp.className = "preview";
          gp.style.cssText = "width:512px;max-width:100%;image-rendering:pixelated";
          cvPrev.parentNode.insertBefore(gp, cvPrev);
        }
        if (gp) { gp.hidden = false; gp.src = dataUrl; }
        const btn = this.shadowRoot.getElementById("send-img");
        if (btn) btn.disabled = false;
        return;
      }
      if (cvPrev) cvPrev.hidden = false;
      if (oldGif) oldGif.hidden = true;
      const img = new Image();
      img.onload = () => {
        this._imageObj = img;
        this._drawPreview();
        const btn = this.shadowRoot.getElementById("send-img");
        if (btn) btn.disabled = false;
      };
      img.src = dataUrl;
    };
    reader.readAsDataURL(fileObj);
  }

  _drawPreview() {
    const canvas = this.shadowRoot.getElementById("preview");
    if (!canvas || !this._imageObj) return;
    const { w, h } = this._panelSize();
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h);
    const img = this._imageObj;
    const scale = this._resizeMethod === "crop"
      ? Math.max(w / img.width, h / img.height)
      : Math.min(w / img.width, h / img.height);
    const dw = img.width * scale, dh = img.height * scale;
    ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
  }

  async _sendImage() {
    if (!this._dev) return;
    if (this._busy) return this._toast("Attendi: invio ancora in corso…", true);
    this._toast("Invio in corso…");
    if (this._gifFile || this._gifB64) {
      // GIF/animazione: byte GREZZI via HTTP. Il WebSocket di HA si ferma a
      // 4 MiB e il base64 gonfia il file di 4/3 → le GIF grandi non ci
      // passano; il body HTTP arriva a 16 MiB.
      this._setBusy(true);
      try {
        const body = this._gifFile
          ? this._gifFile
          : Uint8Array.from(atob(this._gifB64), (c) => c.charCodeAt(0));
        const tok = this._authToken();
        const qs = "address=" + encodeURIComponent(this._dev.address) +
          "&resize_method=" + encodeURIComponent(this._resizeMethod);
        const resp = await fetch("/api/ipixel_color/upload?" + qs, {
          method: "POST",
          headers: tok ? { Authorization: "Bearer " + tok } : {},
          body,
        });
        let j = {};
        try { j = await resp.json(); } catch (e) { /* no body */ }
        if (!resp.ok) {
          return this._toast(j.error || ("Invio fallito (" + resp.status + ")"), true);
        }
        this._toast(
          j.displayed ? "Fatto ✓"
            : "Inviata, ma il display non l'ha mostrata (collegamento liberato?)",
          !j.displayed);
      } catch (e) {
        this._toast("Invio fallito: " + (e.message || e), true);
      } finally { this._setBusy(false); }
      return;
    }
    const canvas = this.shadowRoot.getElementById("preview");
    if (!canvas || !this._imageObj) return;
    const dataUrl = canvas.toDataURL("image/png");
    const b64 = dataUrl.split(",")[1];
    await this._call("ipixel_color", "display_image", {
      address: this._dev.address, image: b64, resize_method: this._resizeMethod,
    });
  }

  /* ============================================ stato → UI */

  _refreshValues() {
    const $ = (id) => this.shadowRoot.getElementById(id);
    if (!this._hass || !this._dev || !this.shadowRoot.getElementById("main")) return;

    const setIf = (id, v) => {
      const el = $(id);
      // shadowRoot.activeElement: document.activeElement nello shadow DOM
      // restituisce l'host, mai l'input, e la guardia non scattava mai
      if (el && this.shadowRoot.activeElement !== el && el.value !== String(v)) el.value = v;
    };
    const chkIf = (id, on) => { const el = $(id); if (el) el.checked = on; };
    const chipSel = (cls, v) =>
      this.shadowRoot.querySelectorAll("." + cls).forEach((c) =>
        c.classList.toggle("active", String(c.dataset.v) === String(v)));

    if (this._page === "testo") {
      setIf("font", this._val("select", "font"));
      const toHexT = (st) => {
        const rgb = st && st.attributes && st.attributes.rgb_color;
        return rgb ? "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("") : null;
      };
      const tcx = toHexT(this._state("light", "text_color"));
      if (tcx) setIf("tx-color", tcx);
      const bgx = toHexT(this._state("light", "background_color"));
      if (bgx) setIf("tx-bg", bgx);
      chipSel("mode-chip", this._val("select", "mode"));
      chipSel("anim-chip", this._val("number", "text_animation", "0"));
      const fs = parseInt(parseFloat(this._val("number", "font_size", "0")), 10) || 0;
      setIf("fsize", fs);
      if ($("fsize-v")) $("fsize-v").textContent = fs === 0 ? "Auto" : fs;
      setIf("speed", this._val("number", "text_speed", "50"));
      if ($("speed-v")) $("speed-v").textContent = this._val("number", "text_speed", "50");
      setIf("rainbow", this._val("number", "text_rainbow", "0"));
      if ($("rainbow-v")) $("rainbow-v").textContent = this._val("number", "text_rainbow", "0");
      // aggiorna i valori sulle piastrelle senza ricostruirle
      this.shadowRoot.querySelectorAll("#tiles .tile[data-i]").forEach((t) => {
        const it = this._items && this._items[t.dataset.i];
        if (!it) return;
        const v = t.querySelector(".v");
        if (v) {
          const nv = this._tileValue(it);
          if (v.textContent !== nv) v.textContent = nv;
        }
      });
      this._updateSimFromState();
    }

    if (this._page === "orologio" || this._page === "testo") {
      chipSel("clock-chip", this._val("select", "clock_style", "1"));
      chkIf("c24", this._val("switch", "clock_24h") === "on");
      chkIf("cdate", this._val("switch", "clock_show_date") === "on");
      this._updateSimFromState();
    }

    if (this._page === "immagine" || this._page === "testo") chipSel("rez-chip", this._resizeMethod);

    if (this._page === "colori") {
      const toHex = (st) => {
        const rgb = st && st.attributes && st.attributes.rgb_color;
        return rgb ? "#" + rgb.map((x) => x.toString(16).padStart(2, "0")).join("") : null;
      };
      const tc = toHex(this._state("light", "text_color"));
      const bc = toHex(this._state("light", "background_color"));
      if (tc) setIf("col-text", tc);
      if (bc) setIf("col-bg", bc);
      setIf("bright", this._val("number", "brightness", "50"));
      if ($("bright-v")) $("bright-v").textContent = this._val("number", "brightness", "50");
    }

    if (this._page === "sistema") {
      chkIf("power", this._val("switch", null) === "on");
      chkIf("autoupd", this._val("switch", "auto_update") === "on");
      const info = $("devinfo");
      if (info) {
        const rows = [
          ["Nome", this._dev.name],
          ["Indirizzo", this._dev.address],
          ["Tipo", this._val("sensor", "device_type", "—")],
          ["Risoluzione", `${this._val("sensor", "display_width", "?")} × ${this._val("sensor", "display_height", "?")}`],
          ["Versione MCU", this._val("sensor", "mcu_version", "—")],
          ["Versione WiFi", this._val("sensor", "wifi_version", "—")],
        ];
        info.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
      }
    }
  }
}

customElements.define("ipixel-panel", IPixelPanel);
