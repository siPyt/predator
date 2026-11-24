"""Minimal, monochrome dark-mode Predator Web Command Center served on :8099.

Matches the black/carbon-fiber + bone-white aesthetic of predator-wallpaper.png
and uses the Kali dragon predator-icon.png logo (/assets/predator-icon.png).
Zero external dependencies — works 100% offline.
"""

WEB_UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PREDATOR</title>
<link rel="icon" type="image/png" href="/assets/predator-icon.png">
<style>
  :root {
    --bg: #0a0b0e;
    --panel: #12141a;
    --panel2: #181b23;
    --border: #232732;
    --fg: #d8dce3;
    --bone: #eceae2;
    --muted: #7f8796;
    --accent: #39d353;
    --warn: #d8a641;
    --err: #e5534b;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    --sans: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font-family: var(--sans); font-size: 14px; line-height: 1.5;
  }
  header {
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 12px 24px; background: var(--panel); border-bottom: 1px solid var(--border);
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand img {
    width: 36px; height: 36px; border-radius: 6px;
    border: 1px solid var(--border); background: #000; object-fit: cover;
  }
  .brand-title {
    font-weight: 700; letter-spacing: 0.28em; font-size: 16px; color: var(--bone);
  }
  .hdr-right { display: flex; align-items: center; gap: 10px; }
  .status-pill {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 5px 11px; border-radius: 999px; background: var(--panel2);
    border: 1px solid var(--border); color: var(--muted); font-size: 12px;
    font-family: var(--mono); cursor: pointer;
  }
  .status-pill:hover { border-color: var(--muted); color: var(--fg); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); display: inline-block; }
  .dot.warn { background: var(--warn); }
  .dot.err { background: var(--err); }
  button.btn, a.btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--panel2); color: var(--bone); text-decoration: none;
    font-size: 13px; font-weight: 500; cursor: pointer;
  }
  button.btn:hover, a.btn:hover { border-color: #4a5162; background: #1e222c; }
  button.btn.primary, a.btn.primary {
    background: var(--bone); color: #0a0b0e; border-color: var(--bone); font-weight: 600;
  }
  button.btn.primary:hover, a.btn.primary:hover { background: #ffffff; }
  button.btn.danger { border-color: rgba(229,83,75,0.45); color: var(--err); }
  button.btn.sm { padding: 4px 9px; font-size: 12px; }
  button.help-btn {
    width: 26px; height: 26px; border-radius: 50%; padding: 0;
    display: inline-flex; align-items: center; justify-content: center;
    background: #232834; border: 1px solid #8c95a6; color: #ffffff;
    font-size: 13px; font-weight: 700; cursor: pointer; flex-shrink: 0;
  }
  button.help-btn:hover { background: #ffffff; border-color: #ffffff; color: #0a0b0e; }
  .help-body ul { margin: 8px 0 0; padding-left: 18px; }
  .help-body li { margin-bottom: 6px; color: var(--fg); }
  .help-body strong { color: var(--bone); }
  .help-body code { font-family: var(--mono); background: var(--panel2); padding: 1px 5px; border-radius: 4px; color: var(--bone); font-size: 12px; }
  .dropdown { position: relative; }
  .dropdown-menu {
    display: none; position: absolute; right: 0; top: calc(100% + 6px); min-width: 190px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 6px; z-index: 30; box-shadow: 0 10px 28px rgba(0,0,0,0.6);
  }
  .dropdown.open .dropdown-menu { display: block; }
  .dropdown-menu a {
    display: block; padding: 7px 10px; border-radius: 5px;
    color: var(--fg); text-decoration: none; font-size: 13px;
  }
  .dropdown-menu a:hover { background: var(--panel2); color: var(--bone); }

  nav.tabs {
    display: flex; gap: 6px; padding: 0 24px; background: var(--panel);
    border-bottom: 1px solid var(--border);
  }
  nav.tabs button {
    background: none; border: none; border-bottom: 2px solid transparent;
    color: var(--muted); padding: 11px 14px; font-size: 13px; font-weight: 600;
    cursor: pointer;
  }
  nav.tabs button:hover { color: var(--fg); }
  nav.tabs button.active { color: var(--bone); border-bottom-color: var(--bone); }

  main { padding: 20px 24px; max-width: 1360px; margin: 0 auto; }
  section.tab { display: none; }
  section.tab.active { display: block; }

  .bar {
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px;
  }
  input[type="text"], input[type="number"], select {
    background: var(--panel); color: var(--bone); border: 1px solid var(--border);
    border-radius: 6px; padding: 7px 11px; font-size: 13px; font-family: inherit;
  }
  input:focus, select:focus { outline: none; border-color: #5a6378; }

  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 18px; margin-bottom: 16px;
  }
  .table-wrap {
    overflow-x: auto; max-height: 68vh; overflow-y: auto;
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
  }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid #1b1f29; white-space: nowrap; }
  th {
    position: sticky; top: 0; background: var(--panel2); color: var(--muted);
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; cursor: pointer;
  }
  th:hover { color: var(--bone); }
  tbody tr.clickable { cursor: pointer; }
  tbody tr:hover { background: rgba(255,255,255,0.03); }
  .mono { font-family: var(--mono); }
  .muted { color: var(--muted); }
  .val { font-family: var(--mono); color: var(--accent); font-weight: 600; }
  .tag {
    display: inline-block; padding: 2px 6px; border-radius: 4px;
    font-size: 11px; font-family: var(--mono); background: var(--panel2); color: var(--muted);
  }
  .tag.rw { color: var(--bone); border: 1px solid #384052; }

  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .stat { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
  .stat-k { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  .stat-v { font-size: 20px; font-weight: 600; color: var(--bone); margin-top: 4px; font-family: var(--mono); }

  .subnav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
  .subnav button {
    padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--panel); color: var(--muted); font-size: 13px; cursor: pointer;
  }
  .subnav button.active { background: var(--panel2); color: var(--bone); border-color: #4a5162; }
  .subpane { display: none; }
  .subpane.active { display: block; }

  .logbox {
    background: #07080a; border: 1px solid var(--border); border-radius: 6px;
    padding: 12px; font-family: var(--mono); font-size: 12px; max-height: 260px;
    overflow-y: auto; white-space: pre-wrap; color: #b9c2d0;
  }
  details summary {
    cursor: pointer; color: var(--bone); font-weight: 600; font-size: 13px;
    user-select: none; padding: 4px 0;
  }

  .modal-bg {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.72);
    align-items: center; justify-content: center; z-index: 50;
  }
  .modal-bg.open { display: flex; }
  .modal {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px; width: min(440px, 92vw);
  }
  .modal h3 { margin: 0 0 8px; color: var(--bone); font-size: 16px; }
  .field { margin-bottom: 12px; }
  .field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  .diff-add { background: rgba(57,211,83,0.10); }
  .diff-rem { background: rgba(229,83,75,0.10); }
  .diff-chg { background: rgba(216,166,65,0.10); }
  #toast {
    position: fixed; bottom: 18px; right: 18px; background: var(--panel2);
    border: 1px solid #3c4354; color: var(--bone); padding: 9px 14px;
    border-radius: 8px; display: none; z-index: 60; font-size: 13px;
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <img src="/assets/predator-icon.png" alt="Predator">
    <div class="brand-title">PREDATOR</div>
  </div>
  <div class="hdr-right">
    <button class="status-pill" id="status-pill" onclick="openTool('health')" title="View system health">
      <span class="dot" id="status-dot"></span><span id="status-text">connecting...</span>
    </button>
    <button class="btn" id="demo-btn" onclick="toggleDemo()" title="Load sample chiller data across all tabs">Demo</button>
    <button class="btn" onclick="triggerRescan()">Rescan</button>
    <div class="dropdown" id="export-dd">
      <button class="btn primary" onclick="toggleExport(event)">Export &#9662;</button>
      <div class="dropdown-menu">
        <a href="/export.csv">Discovered Points (.csv)</a>
        <a href="/export.xlsx">Excel Point Map (.xlsx)</a>
        <a href="/export.icsproj">Mirius Project (.icsproj)</a>
        <a href="/recon/export.csv">Multi-Protocol Recon (.csv)</a>
      </div>
    </div>
    <button class="help-btn" onclick="openHelp('overview')" title="How Predator works">?</button>
  </div>
</header>

<nav class="tabs">
  <button class="active" data-tab="points">Points</button>
  <button data-tab="icc">Mirius / ICC</button>
  <button data-tab="tools">Tools</button>
</nav>

<main>
  <!-- TAB 1: POINTS (clean, calm default screen) -->
  <section id="tab-points" class="tab active">
    <div class="bar">
      <input type="text" id="filter-text" placeholder="Search points, devices, values..." style="min-width:280px" oninput="renderPoints()">
      <select id="filter-source" onchange="renderPoints()">
        <option value="">All Protocols</option>
        <option value="mstp">BACnet MS/TP</option>
        <option value="bacnet-ip">BACnet/IP</option>
        <option value="modbus-rtu">Modbus RTU</option>
        <option value="points-map">Points Map</option>
      </select>
      <span class="muted" id="points-count" style="margin-left:auto"></span>
      <button class="help-btn" onclick="openHelp('points')" title="About the Points tab">?</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th onclick="sortBy('address')">Device</th>
            <th onclick="sortBy('object_or_register')">Object</th>
            <th onclick="sortBy('description')">Name</th>
            <th onclick="sortBy('value')">Value</th>
            <th onclick="sortBy('writable')">R/W</th>
            <th onclick="sortBy('source')">Protocol</th>
          </tr>
        </thead>
        <tbody id="points-body"></tbody>
      </table>
    </div>
    <div class="muted" style="font-size:12px; margin-top:8px">Tip: click any BACnet point row to read or test-write live.</div>
  </section>

  <!-- TAB 2: MIRIUS / ICC -->
  <section id="tab-icc" class="tab">
    <div class="stats">
      <div class="stat"><div class="stat-k">Selected Points</div><div class="stat-v" id="icc-st-count">0</div></div>
      <div class="stat"><div class="stat-k">Port A (MS/TP)</div><div class="stat-v" id="icc-st-baud">-</div></div>
      <div class="stat"><div class="stat-k">Device MACs</div><div class="stat-v" id="icc-st-macs" style="font-size:15px">-</div></div>
      <div class="stat"><div class="stat-k">Port B (BMS)</div><div class="stat-v" id="icc-st-portb" style="font-size:16px">None</div></div>
    </div>

    <div class="card">
      <div class="bar">
        <label class="muted">Port A Baud <input type="number" id="icc-baud" style="width:92px; margin-left:4px"></label>
        <label class="muted">Default MAC <input type="number" id="icc-mac" min="0" max="127" style="width:68px; margin-left:4px" value="1"></label>
        <label class="muted">Device MACs <input type="text" id="icc-dev-macs" style="width:155px; margin-left:4px" placeholder="9050=1"></label>
        <label class="muted">Port B
          <select id="icc-portb-proto" onchange="togglePortBRows()" style="margin-left:4px">
            <option value="none">None (Port A Only)</option>
            <option value="bacnet-mstp">BACnet MS/TP Server</option>
            <option value="fln">Siemens FLN Slave</option>
            <option value="modbus-rtu">Modbus RTU Slave</option>
            <option value="metasys-n2">Metasys N2 Slave</option>
          </select>
        </label>
        <button class="help-btn" style="margin-left:auto" onclick="openHelp('icc')" title="How to fill out Mirius / ICC settings">?</button>
      </div>
      <div class="bar" id="icc-fln-row" style="display:none; padding-top:8px; border-top:1px solid var(--border)">
        <label class="muted">FLN Baud
          <select id="icc-fln-baud" style="margin-left:4px">
            <option value="4800">4800</option><option value="9600">9600</option>
            <option value="19200">19200</option><option value="38400">38400</option>
          </select>
        </label>
        <label class="muted">FLN Addr <input type="number" id="icc-fln-addr" min="0" max="98" value="1" style="width:66px; margin-left:4px"></label>
        <label class="muted">App # <input type="number" id="icc-fln-app" value="2766" style="width:82px; margin-left:4px"></label>
        <label class="muted">App Desc <input type="text" id="icc-fln-desc" value="PREDATOR" maxlength="12" style="width:120px; margin-left:4px"></label>
      </div>
      <div class="bar" id="icc-bacnet-row" style="display:none; padding-top:8px; border-top:1px solid var(--border)">
        <label class="muted">Port B Baud
          <select id="icc-pb-baud" style="margin-left:4px">
            <option value="9600">9600</option><option value="19200">19200</option>
            <option value="38400" selected>38400</option><option value="57600">57600</option>
            <option value="76800">76800</option><option value="115200">115200</option>
          </select>
        </label>
        <label class="muted">Mirius MAC <input type="number" id="icc-pb-mac" min="0" max="127" value="10" style="width:66px; margin-left:4px"></label>
        <label class="muted">Server Dev ID <input type="number" id="icc-pb-inst" min="1" max="4194302" value="10000" style="width:92px; margin-left:4px"></label>
        <label class="muted">Server Name <input type="text" id="icc-pb-name" value="Mirius_B" maxlength="16" style="width:130px; margin-left:4px"></label>
      </div>
      <div class="bar" id="icc-modbus-row" style="display:none; padding-top:8px; border-top:1px solid var(--border)">
        <label class="muted">Modbus Baud
          <select id="icc-mb-baud" style="margin-left:4px">
            <option value="2400">2400</option><option value="4800">4800</option>
            <option value="9600" selected>9600</option><option value="19200">19200</option>
            <option value="38400">38400</option><option value="57600">57600</option><option value="115200">115200</option>
          </select>
        </label>
        <label class="muted">Parity
          <select id="icc-mb-par" style="margin-left:4px">
            <option value="0">8-N-1</option><option value="1">8-O-1</option>
            <option value="2">8-E-1</option><option value="3">8-N-2</option>
          </select>
        </label>
        <label class="muted">Slave Unit ID <input type="number" id="icc-mb-addr" min="1" max="247" value="1" style="width:70px; margin-left:4px"></label>
        <span class="muted" style="font-size:12px">Auto-maps full Mirius DB to Holding/Input Registers &amp; Coils (DefMap=1)</span>
      </div>
      <div class="bar" id="icc-n2-row" style="display:none; padding-top:8px; border-top:1px solid var(--border)">
        <label class="muted">Metasys N2 Addr (1–255) <input type="number" id="icc-n2-addr" min="1" max="255" value="1" style="width:74px; margin-left:4px"></label>
        <span class="muted" style="font-size:12px">Fixed 9600 / 8-N-1 · Maps AI / AO / BI / BO to matching DB addresses</span>
      </div>
      <div class="bar" style="margin-bottom:0; margin-top:8px">
        <button class="btn primary" onclick="saveIccConfig()">Save &amp; Rebuild</button>
        <button class="btn" onclick="fillIccDemo()" title="Fill example Mirius Port A + Port B settings">Fill Demo</button>
        <a class="btn" href="/export.icsproj">Download .icsproj</a>
        <a class="btn" href="/export.xlsx">Download .xlsx</a>
      </div>
    </div>

    <div class="card">
      <details id="icc-details">
        <summary>Customize Individual Points (Include / Float32 / DB Addresses)</summary>
        <div class="table-wrap" style="margin-top:12px; max-height:50vh">
          <table>
            <thead>
              <tr><th>Use</th><th>Device</th><th>MAC</th><th>Object</th><th>Name</th><th>DB</th><th>Float32</th><th>FLN Pt</th><th>FLN Desc</th></tr>
            </thead>
            <tbody id="icc-body"></tbody>
          </table>
        </div>
      </details>
    </div>
  </section>

  <!-- TAB 3: TOOLS (one focused tool at a time) -->
  <section id="tab-tools" class="tab">
    <div class="subnav">
      <button class="active" data-tool="ports" onclick="showTool('ports')">Serial Ports</button>
      <button data-tool="doctor" onclick="showTool('doctor')">Bus Doctor</button>
      <button data-tool="recon" onclick="showTool('recon')">Network Recon</button>
      <button data-tool="snapshots" onclick="showTool('snapshots')">Snapshots &amp; Diff</button>
      <button data-tool="health" onclick="showTool('health')">Pi Health</button>
    </div>

    <!-- Tool: Serial Ports -->
    <div id="tool-ports" class="subpane active card">
      <div class="bar" style="justify-content:space-between">
        <div style="display:flex; align-items:center; gap:8px"><strong>USB-RS485 Serial Ports</strong><button class="help-btn" onclick="openHelp('ports')" title="How to use Serial Port overrides">?</button></div>
        <button class="btn sm" onclick="triggerRescan()">Rescan Ports</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Port</th><th>Status</th><th>Mode</th><th>Protocol</th><th>Baud</th><th>Parity</th><th></th></tr></thead>
          <tbody id="ports-body"></tbody>
        </table>
      </div>
    </div>

    <!-- Tool: Bus Doctor -->
    <div id="tool-doctor" class="subpane card">
      <div class="bar">
        <strong>RS-485 Bus Doctor</strong>
        <button class="help-btn" onclick="openHelp('doctor')" title="How to use Bus Doctor">?</button>
        <input type="text" id="bd-port" value="/dev/ttyUSB0" style="width:140px">
        <select id="bd-baud">
          <option value="0">Auto-Sweep</option>
          <option value="9600">9600</option><option value="19200">19200</option>
          <option value="38400" selected>38400</option><option value="76800">76800</option><option value="115200">115200</option>
        </select>
        <input type="number" id="bd-sec" value="5" min="2" max="30" style="width:65px"> <span class="muted">sec</span>
        <button class="btn primary" onclick="runBusDoctor()">Sniff Bus</button>
        <button class="btn" onclick="fillDoctorDemo()">Demo</button>
      </div>
      <div id="bd-output" class="logbox">Listen-only MS/TP frame &amp; wiring diagnostic. Never transmits.</div>
    </div>

    <!-- Tool: Multi-Protocol Recon -->
    <div id="tool-recon" class="subpane card">
      <div class="bar">
        <strong>Multi-Protocol Recon</strong>
        <button class="help-btn" onclick="openHelp('recon')" title="How to use Network Recon">?</button>
        <input type="text" id="recon-protocols" value="all" style="width:220px" placeholder="all or bacnet,modbus,mqtt,profinet,s7">
        <input type="text" id="recon-subnets" value="" style="width:180px" placeholder="Subnet (blank = auto)">
        <button class="btn primary" onclick="startRecon()">Run Recon</button>
        <button class="btn" onclick="fillReconDemo()">Demo</button>
        <a class="btn" href="/recon/export.csv">CSV</a>
      </div>
      <div class="muted" id="recon-meta" style="margin-bottom:8px">Idle.</div>
      <div class="logbox" id="recon-log" style="margin-bottom:12px">No recon run yet.</div>
      <div class="table-wrap" style="max-height:240px">
        <table>
          <thead><tr><th>Protocol</th><th>Source</th><th>Device</th><th>Point</th><th>Value</th></tr></thead>
          <tbody id="recon-body"></tbody>
        </table>
      </div>
    </div>

    <!-- Tool: Snapshots & Diff -->
    <div id="tool-snapshots" class="subpane card">
      <div class="bar">
        <button class="help-btn" onclick="openHelp('snapshots')" title="How Snapshots & Diff work">?</button>
        <input type="text" id="snap-name" placeholder="Name (e.g. Chiller_1)" style="width:220px">
        <button class="btn primary" onclick="saveSnapshot()">Save Snapshot</button>
        <button class="btn" onclick="fillSnapshotDemo()">Demo Diff</button>
        <span style="margin-left:12px" class="muted">Compare:</span>
        <select id="diff-before"></select>
        <span class="muted">vs</span>
        <select id="diff-after"></select>
        <button class="btn" onclick="runDiff()">Diff</button>
      </div>
      <div id="diff-summary" class="muted" style="margin-bottom:10px"></div>
      <div class="table-wrap" style="max-height:240px; margin-bottom:12px">
        <table>
          <thead><tr><th>Change</th><th>Device</th><th>Object</th><th>Details</th></tr></thead>
          <tbody id="diff-body"></tbody>
        </table>
      </div>
      <div class="table-wrap" style="max-height:180px">
        <table>
          <thead><tr><th>Saved Snapshot</th><th>Date</th><th>Rows</th><th></th></tr></thead>
          <tbody id="snap-body"></tbody>
        </table>
      </div>
    </div>

    <!-- Tool: Pi Health -->
    <div id="tool-health" class="subpane card">
      <div class="bar" style="justify-content:space-between">
        <strong>Pi System Health</strong>
        <button class="help-btn" onclick="openHelp('health')" title="What these health numbers mean">?</button>
      </div>
      <div class="stats" id="health-grid"></div>
      <div class="logbox" id="health-json">Loading...</div>
    </div>
  </section>
</main>

<!-- POINT READ / WRITE MODAL -->
<div class="modal-bg" id="rw-modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px">
      <h3 id="rw-title" style="margin:0">Point</h3>
      <button class="help-btn" onclick="openHelp('rw')" title="How Read, Write & Relinquish work">?</button>
    </div>
    <div class="muted mono" id="rw-target" style="font-size:12px; margin-bottom:12px"></div>
    <div class="bar" style="justify-content:space-between; background:var(--panel2); padding:10px 12px; border-radius:6px">
      <span class="val" id="rw-read-out" style="font-size:16px">-</span>
      <button class="btn sm" onclick="doLiveRead()">Read Live</button>
    </div>
    <div class="field" style="margin-top:14px">
      <label>Write Value</label>
      <div class="bar" style="margin-bottom:0">
        <input type="text" id="rw-val" style="flex:1" placeholder="e.g. 44.5 or active">
        <input type="number" id="rw-pri" min="1" max="16" value="8" style="width:68px" title="BACnet Priority">
        <button class="btn sm" onclick="fillRwDemo()" title="Fill an example value">Demo</button>
      </div>
    </div>
    <div class="bar" style="justify-content:flex-end; margin-bottom:0; margin-top:16px">
      <button class="btn sm" onclick="closeModal()">Close</button>
      <button class="btn sm danger" onclick="doLiveWrite(true)">Relinquish</button>
      <button class="btn sm primary" onclick="doLiveWrite(false)">Write</button>
    </div>
  </div>
</div>

<!-- CONTEXTUAL HELP MODAL -->
<div class="modal-bg" id="help-modal" onclick="if(event.target===this)closeHelp()">
  <div class="modal" style="width:min(540px, 94vw)">
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px">
      <h3 id="help-title" style="margin:0">Help</h3>
      <button class="btn sm" onclick="closeHelp()">Close</button>
    </div>
    <div id="help-body" class="help-body" style="font-size:13px; line-height:1.55"></div>
  </div>
</div>

<div id="toast"></div>

<script>
const HELP_TOPICS = {
  overview: {
    title: 'How Predator Works',
    html: `<p>Predator auto-detects whatever field bus is plugged into the Pi — <strong>BACnet/IP</strong> on Ethernet or <strong>BACnet MS/TP / Modbus RTU</strong> on USB-RS485 — and builds your point list, Excel map, and ICC Mirius project automatically.</p>
    <ul>
      <li><strong>Status pill</strong> (top right): shows live CPU temp (&deg;F), RTC battery voltage, and Ethernet speed. Click it for full Pi Health.</li>
      <li><strong>Demo / Clear Demo</strong>: loads sample chiller &amp; AHU data across every screen so you can practice without live equipment plugged in.</li>
      <li><strong>Export &blacktriangledown;</strong>: one-click downloads for CSV, Excel Point Map, Mirius <code>.icsproj</code>, and Recon CSV.</li>
      <li><strong>USB Stick Auto-Dump</strong>: plug any FAT32/exFAT USB thumb drive into the Pi and it automatically copies a timestamped folder with all exports and blinks the Pi green LED 5 times when safe to unplug.</li>
    </ul>`
  },
  points: {
    title: 'Points Tab',
    html: `<p>Shows every point Predator has discovered live across BACnet MS/TP, BACnet/IP, Modbus RTU, and your points map.</p>
    <ul>
      <li><strong>Search box</strong>: type any word, device ID, object (like <code>AI:1</code> or <code>Temp</code>), or value to filter instantly.</li>
      <li><strong>Click any column header</strong> (Device, Object, Name, Value, R/W, Protocol) to sort.</li>
      <li><strong>Click any BACnet row</strong> to open the live <strong>Read / Write / Relinquish</strong> tester for that point.</li>
    </ul>`
  },
  rw: {
    title: 'Live Point Read, Write & Relinquish',
    html: `<p>Test individual BACnet points live on a chiller or controller right from the browser.</p>
    <ul>
      <li><strong>Read Live</strong>: queries the controller's current <code>present-value</code> and engineering units right now.</li>
      <li><strong>Write Value</strong>: enter a number for analog (e.g. <code>44.0</code>), <code>active</code> / <code>inactive</code> for binary, or an integer (e.g. <code>2</code>) for multi-state. Click <strong>Demo</strong> to fill a sample value.</li>
      <li><strong>Priority (1–16)</strong>: BACnet command priority. <code>8</code> is standard manual operator override; <code>16</code> is lowest/BMS default.</li>
      <li><strong>Relinquish</strong>: sends BACnet <code>NULL</code> to that priority slot to release your test override so normal control takes back over.</li>
    </ul>`
  },
  icc: {
    title: 'Mirius / ICC Project Builder',
    html: `<p>Generates an <strong>ICC Configuration Studio (.icsproj)</strong> for a Mirius gateway plus a matching <strong>Excel Point Map (.xlsx)</strong>. Both also auto-save to the Pi Desktop.</p>
    <ul>
      <li><strong>Baud</strong>: MS/TP trunk speed of the chiller/field bus (e.g. <code>19200</code> or <code>38400</code>). Auto-filled from the live scan.</li>
      <li><strong>Default MAC</strong>: MS/TP MAC address (<code>0–127</code>) used if a device's MAC wasn't auto-detected from Who-Is.</li>
      <li><strong>Device MACs</strong>: per-device overrides formatted as <code>DeviceID=MAC</code> separated by commas, e.g. <code>9050=1,3200=6</code>. Auto-filled when Predator hears the device answer Who-Is.</li>
      <li><strong>Port B</strong>: choose what the Mirius speaks on Port B (the BMS side):
        <ul>
          <li><code>BACnet MS/TP Server</code>: re-exposes every point as a BACnet MS/TP server on Port B (supports 16-bit and 32-bit Float!).</li>
          <li><code>Siemens FLN Slave</code>: builds Siemens P1 FLN (set <code>FLN Baud</code>, <code>FLN Addr</code> <code>0–98</code>, <code>App #</code>, <code>App Desc</code>).</li>
          <li><code>Modbus RTU Slave</code>: exposes the entire Mirius DB as Holding/Input Registers &amp; Coils (set <code>Baud</code>, <code>Parity</code>, <code>Slave Unit ID</code> <code>1–247</code>).</li>
          <li><code>Metasys N2 Slave</code>: Johnson Controls N2 at <code>9600/8N1</code> (set <code>N2 Addr</code> <code>1–255</code>; maps AI/AO/BI/BO to the exact same DB addresses).</li>
          <li><code>None (Port A Only)</code>: leaves Port B empty if you need a specialized protocol (DMX-512, Generic Serial, etc.) added manually in ICC Studio.</li>
        </ul>
      </li>
      <li><strong>Customize Individual Points</strong>: expand to uncheck unwanted points or enable <strong>Float32</strong> on Port A analog points so decimals aren't rounded (note: Port B FLN requires 16-bit unsigned). Database addresses stay <strong>pinned</strong> across rescans so Port B never shifts.</li>
    </ul>`
  },
  ports: {
    title: 'Serial Ports & Manual Overrides',
    html: `<p>Predator normally auto-detects BACnet MS/TP vs. Modbus RTU and baud rate on every USB-RS485 adapter.</p>
    <ul>
      <li>Leave <strong>Protocol</strong> on <code>auto</code> for normal plug-and-play operation.</li>
      <li>Only force a <strong>Protocol / Baud / Parity</strong> and click <strong>Apply</strong> if a device doesn't answer Who-Is or runs a non-standard setting.</li>
      <li>Set Protocol back to <code>auto</code> and click <strong>Apply</strong> to return to automatic detection.</li>
    </ul>`
  },
  doctor: {
    title: 'RS-485 Bus Doctor (Passive Sniffer)',
    html: `<p>Passively listens to raw RS-485 traffic to diagnose wiring, polarity, baud rate, and token passing. <strong>Never transmits</strong> — 100% safe on live customer trunks.</p>
    <ul>
      <li><strong>Port</strong>: serial adapter path, usually <code>/dev/ttyUSB0</code>.</li>
      <li><strong>Baud</strong>: pick a known speed (e.g. <code>19200</code> or <code>38400</code>) or choose <code>Auto-Sweep</code> to check all standard speeds.</li>
      <li><strong>Seconds</strong>: how long to listen (<code>5</code> seconds is plenty for active trunks).</li>
      <li><strong>Verdict</strong>: tells you if the trunk is healthy (lists active MACs &amp; tokens/sec), silent (check power/wiring), or noisy (CRC errors, wrong baud, or swapped A/B polarity).</li>
    </ul>`
  },
  recon: {
    title: 'Multi-Protocol Network Recon',
    html: `<p>Runs a one-shot sweep of the Ethernet network for industrial protocols beyond the gateway's always-on BACnet/Modbus engine.</p>
    <ul>
      <li><strong>Protocols</strong>: leave as <code>all</code>, or enter a comma list from: <code>services, bacnet, modbus, mqtt, profinet, enip, s7, opcua, knx</code>.</li>
      <li><strong>Subnet</strong>: leave blank to auto-detect the Pi's local Ethernet subnet, or type a CIDR like <code>192.168.1.0/24</code>.</li>
      <li>Click <strong>CSV</strong> afterward to download every discovered device/register/topic.</li>
    </ul>`
  },
  snapshots: {
    title: 'Snapshots & Diff',
    html: `<p>Save named point lists per piece of equipment and compare before/after scans to spot what changed.</p>
    <ul>
      <li><strong>Name</strong>: enter an equipment or job label (e.g. <code>Chiller_1_Before</code>) and click <strong>Save Snapshot</strong>.</li>
      <li><strong>Compare / Diff</strong>: pick a saved snapshot in the first dropdown and another snapshot (or <code>Live Scan</code>) in the second, then click <strong>Diff</strong>.</li>
      <li>Highlights <span style="color:var(--accent)">ADDED</span>, <span style="color:var(--err)">REMOVED</span>, and <span style="color:var(--warn)">CHANGED</span> points (value, name, units, R/W).</li>
    </ul>`
  },
  health: {
    title: 'Pi System Health',
    html: `<p>Live diagnostics from the Raspberry Pi 5 hardware.</p>
    <ul>
      <li><strong>CPU Temp</strong>: <code>105–140&deg;F</code> is normal; throttling doesn't start until <code>176&deg;F</code>.</li>
      <li><strong>RTC Battery</strong>: should read ~<code>3.0–3.3V</code> with <code>chg 3.0V</code> trickle charging active so the clock stays accurate with power off.</li>
      <li><strong>Ethernet</strong>: locked to <code>100M / full</code> with EEE disabled for rock-solid direct PC link stability.</li>
      <li><strong>Throttled</strong>: <code>0x0</code> means zero under-voltage or thermal events.</li>
    </ul>`
  }
};

function openHelp(topic) {
  const h = HELP_TOPICS[topic] || HELP_TOPICS.overview;
  document.getElementById('help-title').textContent = h.title;
  document.getElementById('help-body').innerHTML = h.html;
  document.getElementById('help-modal').classList.add('open');
}
function closeHelp() { document.getElementById('help-modal').classList.remove('open'); }

let allRows = [], sortCol = 'address', sortAsc = true, activeRw = null, demoActive = false;

async function toggleDemo() {
  try {
    if (!demoActive) {
      await apiJson('/demo/load', {method: 'POST'});
      demoActive = true;
      document.getElementById('demo-btn').textContent = 'Clear Demo';
      toast('Loaded sample chiller + AHU demo data');
    } else {
      await apiJson('/demo/clear', {method: 'POST'});
      demoActive = false;
      document.getElementById('demo-btn').textContent = 'Demo';
      toast('Demo data cleared');
    }
    loadPoints(); loadIccPreview(); loadPorts(); loadSnapshots(); pollRecon(); pollBusDoctor();
  } catch (e) { toast('Demo error: ' + e.message); }
}

async function ensureDemoLoaded() {
  if (!demoActive) {
    await apiJson('/demo/load', {method: 'POST'});
    demoActive = true;
    document.getElementById('demo-btn').textContent = 'Clear Demo';
  }
}

async function fillIccDemo() {
  await ensureDemoLoaded();
  await loadIccPreview();
  document.getElementById('icc-baud').value = '19200';
  document.getElementById('icc-mac').value = '1';
  document.getElementById('icc-dev-macs').value = '9050=1';
  document.getElementById('icc-portb-proto').value = 'bacnet-mstp';
  document.getElementById('icc-pb-baud').value = '38400';
  document.getElementById('icc-pb-mac').value = '10';
  document.getElementById('icc-pb-inst').value = '10000';
  document.getElementById('icc-pb-name').value = 'Mirius_B';
  togglePortBRows();
  document.getElementById('icc-details').open = true;
  toast('Filled example Port A + Port B (BACnet MS/TP Server) settings');
}

async function fillDoctorDemo() {
  await ensureDemoLoaded();
  document.getElementById('bd-port').value = '/dev/ttyUSB0';
  document.getElementById('bd-baud').value = '19200';
  document.getElementById('bd-sec').value = '5';
  await pollBusDoctor();
  toast('Loaded sample Bus Doctor report');
}

async function fillReconDemo() {
  await ensureDemoLoaded();
  document.getElementById('recon-protocols').value = 'bacnet,modbus,profinet,s7,mqtt';
  document.getElementById('recon-subnets').value = '192.168.1.0/24';
  await pollRecon();
  toast('Loaded sample Network Recon results');
}

async function fillSnapshotDemo() {
  await ensureDemoLoaded();
  document.getElementById('snap-name').value = 'Chiller_1_After';
  await loadSnapshots();
  document.getElementById('diff-before').value = 'Demo_Chiller_Before';
  document.getElementById('diff-after').value = 'live';
  await runDiff();
  toast('Showing sample Before vs Live diff');
}

function fillRwDemo() {
  if (!activeRw) return;
  const obj = String(activeRw.object_or_register || '').toLowerCase();
  if (obj.startsWith('binary')) document.getElementById('rw-val').value = 'active';
  else if (obj.startsWith('multi')) document.getElementById('rw-val').value = '2';
  else document.getElementById('rw-val').value = '44.0';
  document.getElementById('rw-pri').value = '8';
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg; el.style.display = 'block';
  clearTimeout(window._tt); window._tt = setTimeout(() => el.style.display = 'none', 2800);
}
async function apiJson(url, opts) {
  const r = await fetch(url, opts);
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
function toggleExport(e) {
  e.stopPropagation();
  document.getElementById('export-dd').classList.toggle('open');
}
document.addEventListener('click', () => document.getElementById('export-dd').classList.remove('open'));

function switchTab(name) {
  document.querySelectorAll('nav.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('section.tab').forEach(s => s.classList.toggle('active', s.id === 'tab-' + name));
  if (name === 'icc') loadIccPreview();
  if (name === 'tools') { loadPorts(); loadSnapshots(); pollRecon(); }
}
document.querySelectorAll('nav.tabs button').forEach(b => b.addEventListener('click', () => switchTab(b.dataset.tab)));

function showTool(name) {
  document.querySelectorAll('.subnav button').forEach(b => b.classList.toggle('active', b.dataset.tool === name));
  document.querySelectorAll('.subpane').forEach(p => p.classList.toggle('active', p.id === 'tool-' + name));
  if (name === 'ports') loadPorts();
  if (name === 'snapshots') loadSnapshots();
  if (name === 'recon') pollRecon();
  if (name === 'health') loadTelemetry();
}
function openTool(name) { switchTab('tools'); showTool(name); }

async function loadTelemetry() {
  try {
    const t = await apiJson('/telemetry');
    const parts = [];
    if (t.cpu_temp_f !== null && t.cpu_temp_f !== undefined) parts.push(`${t.cpu_temp_f}°F`);
    if (t.rtc && t.rtc.battery_v !== null) parts.push(`RTC ${t.rtc.battery_v}V`);
    if (t.eth0 && t.eth0.speed_mbps) parts.push(`${t.eth0.speed_mbps}M`);
    document.getElementById('status-text').textContent = parts.join(' · ') || 'Online';
    const dot = document.getElementById('status-dot');
    dot.className = 'dot' + (t.throttled && !t.throttled.ok ? ' err' : (t.cpu_temp_f > 158 ? ' warn' : ''));
    document.getElementById('health-grid').innerHTML = [
      ['CPU Temp', t.cpu_temp_f !== null && t.cpu_temp_f !== undefined ? `${t.cpu_temp_f} °F` : '-'],
      ['RTC Battery', t.rtc && t.rtc.battery_v !== null ? `${t.rtc.battery_v} V` + (t.rtc.charging_v ? ` (chg ${t.rtc.charging_v}V)` : '') : '-'],
      ['Ethernet', t.eth0 && t.eth0.speed_mbps ? `${t.eth0.speed_mbps}M / ${t.eth0.duplex}` : '-'],
      ['Memory', t.memory && t.memory.used_pct !== null ? `${t.memory.used_pct}% used` : '-'],
    ].map(([k,v]) => `<div class="stat"><div class="stat-k">${esc(k)}</div><div class="stat-v" style="font-size:16px">${esc(v)}</div></div>`).join('');
    document.getElementById('health-json').textContent = JSON.stringify(t, null, 2);
  } catch (e) {
    document.getElementById('status-dot').className = 'dot err';
    document.getElementById('status-text').textContent = 'Offline';
  }
}

async function loadPoints() {
  try {
    allRows = (await apiJson('/rows')).rows || [];
    renderPoints();
  } catch (e) {}
}
function sortBy(col) { sortAsc = (sortCol === col) ? !sortAsc : true; sortCol = col; renderPoints(); }

function renderPoints() {
  const q = document.getElementById('filter-text').value.trim().toLowerCase();
  const src = document.getElementById('filter-source').value;
  let rows = allRows.filter(r => {
    if (src && !String(r.source || '').startsWith(src)) return false;
    if (!q) return true;
    return `${r.source} ${r.address} ${r.object_or_register} ${r.description} ${r.value} ${r.units}`.toLowerCase().includes(q);
  });
  rows.sort((a, b) => {
    const va = String(a[sortCol] ?? ''), vb = String(b[sortCol] ?? '');
    const na = Number(va), nb = Number(vb);
    const c = (!isNaN(na) && !isNaN(nb) && va !== '' && vb !== '') ? (na - nb) : va.localeCompare(vb);
    return sortAsc ? c : -c;
  });
  document.getElementById('points-count').textContent = `${rows.length} point${rows.length === 1 ? '' : 's'}`;
  const body = document.getElementById('points-body');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted" style="padding:24px; text-align:center">Waiting for devices — plug in Ethernet or USB-RS485, or click Rescan.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(r => {
    const isW = String(r.writable).toLowerCase() === 'true';
    const canRw = (r.source === 'mstp' || r.source === 'bacnet-ip') && String(r.object_or_register || '').includes(':');
    const valStr = r.value !== '' ? `${r.value}${r.units ? ' ' + r.units : ''}` : '';
    return `<tr class="${canRw ? 'clickable' : ''}" ${canRw ? `onclick='openModal(${JSON.stringify(r)})'` : ''}>
      <td class="mono">${esc(r.address)}</td>
      <td class="mono">${esc(r.object_or_register)}</td>
      <td>${esc(r.description)}</td>
      <td class="val">${esc(valStr)}</td>
      <td>${r.writable === '' ? '' : (isW ? '<span class="tag rw">R/W</span>' : '<span class="tag">R</span>')}</td>
      <td class="muted mono">${esc(r.source)}</td>
    </tr>`;
  }).join('');
}

function openModal(row) {
  activeRw = row;
  document.getElementById('rw-title').textContent = row.description || row.object_or_register;
  document.getElementById('rw-target').textContent = `${row.source} · Device ${row.address} · ${row.object_or_register}`;
  document.getElementById('rw-read-out').textContent = row.value !== '' ? `${row.value} ${row.units || ''}`.trim() : '—';
  document.getElementById('rw-val').value = '';
  document.getElementById('rw-modal').classList.add('open');
}
function closeModal() { document.getElementById('rw-modal').classList.remove('open'); }

async function doLiveRead() {
  if (!activeRw) return;
  document.getElementById('rw-read-out').textContent = 'reading...';
  try {
    const res = await apiJson('/point/read', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: activeRw.source, address: activeRw.address, object: activeRw.object_or_register, bus: activeRw.bus_or_network})
    });
    document.getElementById('rw-read-out').textContent = `${res.value} ${res.units || ''}`.trim();
  } catch (e) { document.getElementById('rw-read-out').textContent = 'Error: ' + e.message; }
}
async function doLiveWrite(relinquish) {
  if (!activeRw) return;
  const value = relinquish ? null : document.getElementById('rw-val').value;
  const priority = parseInt(document.getElementById('rw-pri').value || '8', 10);
  try {
    await apiJson('/point/write', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: activeRw.source, address: activeRw.address, object: activeRw.object_or_register, bus: activeRw.bus_or_network, value, priority})
    });
    toast(relinquish ? `Relinquished @ pri ${priority}` : `Wrote ${value}`);
    doLiveRead();
  } catch (e) { toast('Write error: ' + e.message); }
}

function togglePortBRows() {
  const pb = document.getElementById('icc-portb-proto').value;
  document.getElementById('icc-fln-row').style.display = pb === 'fln' ? 'flex' : 'none';
  document.getElementById('icc-bacnet-row').style.display = pb === 'bacnet-mstp' ? 'flex' : 'none';
  document.getElementById('icc-modbus-row').style.display = pb === 'modbus-rtu' ? 'flex' : 'none';
  document.getElementById('icc-n2-row').style.display = pb === 'metasys-n2' ? 'flex' : 'none';
}

async function loadIccPreview() {
  try {
    const p = await apiJson('/icc/preview');
    const pb = p.port_b_protocol || (p.include_port_b ? 'fln' : 'none');
    document.getElementById('icc-st-count').textContent = `${p.included_count} / ${p.total_count}`;
    document.getElementById('icc-st-baud').textContent = p.baud || '-';
    const macEntries = Object.entries(p.device_macs || {});
    document.getElementById('icc-st-macs').textContent = macEntries.length ? macEntries.map(([k,v]) => `${k}→${v}`).join(', ') : `default (${p.default_mac})`;
    document.getElementById('icc-st-portb').textContent =
      pb === 'fln' ? `FLN @ ${p.fln_baud}` :
      pb === 'bacnet-mstp' ? `MS/TP @ ${p.port_b_baud} (MAC ${p.port_b_mac})` :
      pb === 'modbus-rtu' ? `Modbus @ ${p.modbus_baud} (ID ${p.modbus_addr})` :
      pb === 'metasys-n2' ? `Metasys N2 (Addr ${p.n2_addr})` : 'None';

    document.getElementById('icc-baud').value = p.baud || 19200;
    document.getElementById('icc-mac').value = p.default_mac ?? 1;
    document.getElementById('icc-dev-macs').value = macEntries.map(([k,v]) => `${k}=${v}`).join(',');
    document.getElementById('icc-portb-proto').value = pb;
    document.getElementById('icc-fln-baud').value = String(p.fln_baud || 4800);
    document.getElementById('icc-fln-addr').value = p.fln_addr ?? 1;
    document.getElementById('icc-fln-app').value = p.fln_app_num ?? 2766;
    document.getElementById('icc-fln-desc').value = p.fln_app_desc || 'PREDATOR';
    document.getElementById('icc-pb-baud').value = String(p.port_b_baud || 38400);
    document.getElementById('icc-pb-mac').value = p.port_b_mac ?? 10;
    document.getElementById('icc-pb-inst').value = p.port_b_device_instance ?? 10000;
    document.getElementById('icc-pb-name').value = p.port_b_device_name || 'Mirius_B';
    document.getElementById('icc-mb-baud').value = String(p.modbus_baud || 9600);
    document.getElementById('icc-mb-par').value = String(p.modbus_parity ?? 0);
    document.getElementById('icc-mb-addr').value = p.modbus_addr ?? 1;
    document.getElementById('icc-n2-addr').value = p.n2_addr ?? 1;
    togglePortBRows();

    const body = document.getElementById('icc-body');
    body.innerHTML = (p.points || []).map(pt => `<tr>
      <td><input type="checkbox" class="icc-inc" data-key="${esc(pt.key)}" ${pt.included?'checked':''}></td>
      <td class="mono">${pt.device_instance}</td>
      <td class="mono">${pt.mac}</td>
      <td class="mono">${esc(pt.obj_type)}:${pt.instance}</td>
      <td>${esc(pt.name)}</td>
      <td class="mono">${pt.db_addr}</td>
      <td>${pt.kind === 'analog' ? `<input type="checkbox" class="icc-flt" data-key="${esc(pt.key)}" ${pt.use_float?'checked':''}>` : '<span class="muted">-</span>'}</td>
      <td class="mono">${pt.fln_point ?? '-'}</td>
      <td class="mono">${esc(pt.fln_desc)}</td>
    </tr>`).join('') || '<tr><td colspan="9" class="muted">No MS/TP points discovered yet.</td></tr>';
  } catch (e) {}
}

async function saveIccConfig() {
  const excluded_keys = [], float_keys = [], devMacs = {};
  document.querySelectorAll('.icc-inc').forEach(cb => { if (!cb.checked) excluded_keys.push(cb.dataset.key); });
  document.querySelectorAll('.icc-flt').forEach(cb => { if (cb.checked) float_keys.push(cb.dataset.key); });
  document.getElementById('icc-dev-macs').value.split(',').forEach(s => {
    const [k, v] = s.split('=');
    if (k && v && !isNaN(Number(k)) && !isNaN(Number(v))) devMacs[String(Number(k))] = Number(v);
  });
  try {
    await apiJson('/icc/config', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        baud_override: parseInt(document.getElementById('icc-baud').value, 10) || null,
        default_mac: parseInt(document.getElementById('icc-mac').value, 10) || 1,
        device_macs: devMacs,
        port_b_protocol: document.getElementById('icc-portb-proto').value,
        fln_baud: parseInt(document.getElementById('icc-fln-baud').value, 10) || 4800,
        fln_addr: parseInt(document.getElementById('icc-fln-addr').value, 10) || 1,
        fln_app_num: parseInt(document.getElementById('icc-fln-app').value, 10) || 2766,
        fln_app_desc: document.getElementById('icc-fln-desc').value.trim() || 'PREDATOR',
        port_b_baud: parseInt(document.getElementById('icc-pb-baud').value, 10) || 38400,
        port_b_mac: parseInt(document.getElementById('icc-pb-mac').value, 10) || 10,
        port_b_device_instance: parseInt(document.getElementById('icc-pb-inst').value, 10) || 10000,
        port_b_device_name: document.getElementById('icc-pb-name').value.trim() || 'Mirius_B',
        modbus_baud: parseInt(document.getElementById('icc-mb-baud').value, 10) || 9600,
        modbus_parity: parseInt(document.getElementById('icc-mb-par').value, 10) || 0,
        modbus_addr: parseInt(document.getElementById('icc-mb-addr').value, 10) || 1,
        n2_addr: parseInt(document.getElementById('icc-n2-addr').value, 10) || 1,
        excluded_keys, float_keys
      })
    });
    toast('Mirius project rebuilt');
    loadIccPreview();
  } catch (e) { toast('Error: ' + e.message); }
}

async function loadPorts() {
  try {
    const data = await apiJson('/ports');
    const ports = data.ports || [];
    const pC = ['auto', ...(data.protocol_choices || [])];
    const bC = data.baud_choices || [9600, 19200, 38400, 76800, 115200];
    const parC = data.parity_choices || ['N', 'E', 'O'];
    document.getElementById('ports-body').innerHTML = ports.map((p, i) => {
      const st = p.state || {}, ov = p.override;
      const curP = ov ? ov.protocol : 'auto', curB = ov ? ov.baud : (st.baud || bC[0]), curPar = ov ? ov.parity : 'N';
      return `<tr>
        <td class="mono">${esc(p.port)}</td>
        <td>${esc(st.note || '-')}</td>
        <td><span class="tag">${ov ? 'FORCED' : 'AUTO'}</span></td>
        <td><select id="pp-${i}">${pC.map(x => `<option ${x===curP?'selected':''}>${esc(x)}</option>`).join('')}</select></td>
        <td><select id="pb-${i}">${bC.map(x => `<option ${Number(x)===Number(curB)?'selected':''}>${x}</option>`).join('')}</select></td>
        <td><select id="pr-${i}">${parC.map(x => `<option ${x===curPar?'selected':''}>${esc(x)}</option>`).join('')}</select></td>
        <td><button class="btn sm" onclick="applyPort(${i}, '${esc(p.port)}')">Apply</button></td>
      </tr>`;
    }).join('') || '<tr><td colspan="7" class="muted">No USB-RS485 adapters detected.</td></tr>';
  } catch (e) {}
}
async function applyPort(i, port) {
  const protocol = document.getElementById(`pp-${i}`).value;
  const baud = parseInt(document.getElementById(`pb-${i}`).value, 10);
  const parity = document.getElementById(`pr-${i}`).value;
  try {
    await apiJson(protocol === 'auto' ? '/ports/auto' : '/ports/override', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(protocol === 'auto' ? {port} : {port, protocol, baud, parity})
    });
    toast(`Updated ${port}`); loadPorts();
  } catch (e) { toast('Error: ' + e.message); }
}

async function triggerRescan() {
  toast('Rescanning...');
  try { await apiJson('/rescan', {method: 'POST'}); loadPoints(); loadPorts(); toast('Scan complete'); }
  catch (e) { toast('Rescan error: ' + e.message); }
}

async function runBusDoctor() {
  const port = document.getElementById('bd-port').value.trim();
  const baud = parseInt(document.getElementById('bd-baud').value, 10);
  const duration_s = parseFloat(document.getElementById('bd-sec').value || '5');
  document.getElementById('bd-output').textContent = `Listening passively on ${port}...`;
  try {
    await apiJson('/bus-doctor', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({port, baud, duration_s})});
    pollBusDoctor();
  } catch (e) { document.getElementById('bd-output').textContent = 'Error: ' + e.message; }
}
async function pollBusDoctor() {
  try {
    const r = await apiJson('/bus-doctor');
    if (r.running) { setTimeout(pollBusDoctor, 1000); return; }
    if (r.error) { document.getElementById('bd-output').textContent = 'Error: ' + r.error; return; }
    if (r.report) {
      const rep = r.report;
      document.getElementById('bd-output').textContent = [
        `${rep.port} @ ${rep.baud} baud — ${(rep.health || '').toUpperCase()}`,
        `Bytes: ${rep.total_bytes} | Valid frames: ${rep.valid_frames} | CRC errors: ${(rep.header_crc_errors||0)+(rep.data_crc_errors||0)} | Tokens/s: ${rep.tokens_per_sec}`,
        '',
        ...(rep.verdicts || []),
        '',
        'Active MACs: ' + ((rep.active_masters || []).map(m => `${m.mac} (${m.hex})`).join(', ') || 'none'),
      ].join('\n');
    }
  } catch (e) {}
}

async function startRecon() {
  const protocols = (document.getElementById('recon-protocols').value.trim() || 'all').split(',').map(s => s.trim()).filter(Boolean);
  const sub = document.getElementById('recon-subnets').value.trim();
  try {
    await apiJson('/recon/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({protocols, subnets: sub ? sub.split(',').map(s => s.trim()) : []})});
    toast('Recon started'); pollRecon();
  } catch (e) { toast('Error: ' + e.message); }
}
async function pollRecon() {
  try {
    const s = await apiJson('/recon/status');
    document.getElementById('recon-meta').textContent = s.running ? `Running: ${s.current} (${s.record_count} found)` : `Idle — ${s.record_count} record(s)`;
    document.getElementById('recon-log').textContent = (s.logs || []).join('\n') || 'Ready.';
    document.getElementById('recon-body').innerHTML = (s.records || []).slice(0, 150).map(r => `<tr>
      <td class="mono">${esc(r.protocol)}</td><td class="mono">${esc(r.source)}</td>
      <td class="mono">${esc(r.device)}</td><td class="mono">${esc(r.point)}</td><td class="val">${esc(r.value)}</td>
    </tr>`).join('') || '<tr><td colspan="5" class="muted">No records yet.</td></tr>';
    if (s.running) setTimeout(pollRecon, 1500);
  } catch (e) {}
}

async function loadSnapshots() {
  try {
    const snaps = (await apiJson('/snapshots')).snapshots || [];
    document.getElementById('snap-body').innerHTML = snaps.map(s => `<tr>
      <td>${esc(s.name)}</td><td class="mono muted">${esc(s.saved_at)}</td><td class="mono">${s.row_count}</td>
      <td><button class="btn sm danger" onclick="deleteSnapshot('${esc(s.id)}')">Delete</button></td>
    </tr>`).join('') || '<tr><td colspan="4" class="muted">No snapshots saved.</td></tr>';
    const opts = snaps.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
    document.getElementById('diff-before').innerHTML = opts || '<option value="">(none)</option>';
    document.getElementById('diff-after').innerHTML = '<option value="live">Live Scan</option>' + opts;
  } catch (e) {}
}
async function saveSnapshot() {
  try {
    const res = await apiJson('/snapshots', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: document.getElementById('snap-name').value.trim()})});
    document.getElementById('snap-name').value = '';
    toast(`Saved "${res.name}"`); loadSnapshots();
  } catch (e) { toast('Error: ' + e.message); }
}
async function deleteSnapshot(id) {
  try {
    await apiJson('/snapshots/delete', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id})});
    loadSnapshots();
  } catch (e) { toast('Error: ' + e.message); }
}
async function runDiff() {
  const b = document.getElementById('diff-before').value, a = document.getElementById('diff-after').value;
  if (!b) return toast('Save a snapshot first');
  try {
    const d = await apiJson(`/snapshots/diff?before=${encodeURIComponent(b)}&after=${encodeURIComponent(a)}`);
    document.getElementById('diff-summary').textContent = `+${d.added.length} added · -${d.removed.length} removed · ~${d.changed.length} changed · ${d.unchanged_count} unchanged`;
    const rows = [];
    d.added.forEach(r => rows.push(`<tr class="diff-add"><td class="mono">ADDED</td><td class="mono">${esc(r.address)}</td><td class="mono">${esc(r.object_or_register)}</td><td>${esc(r.description)}</td></tr>`));
    d.removed.forEach(r => rows.push(`<tr class="diff-rem"><td class="mono">REMOVED</td><td class="mono">${esc(r.address)}</td><td class="mono">${esc(r.object_or_register)}</td><td>${esc(r.description)}</td></tr>`));
    d.changed.forEach(r => {
      const ch = Object.entries(r.changes).map(([k,v]) => `${k}: ${v.before} → ${v.after}`).join('; ');
      rows.push(`<tr class="diff-chg"><td class="mono">CHANGED</td><td class="mono">${esc(r.address)}</td><td class="mono">${esc(r.object_or_register)}</td><td class="mono">${esc(ch)}</td></tr>`);
    });
    document.getElementById('diff-body').innerHTML = rows.join('') || '<tr><td colspan="4" class="muted">No differences.</td></tr>';
  } catch (e) { toast('Error: ' + e.message); }
}

loadTelemetry(); loadPoints();
setInterval(loadTelemetry, 6000);
setInterval(loadPoints, 4000);
</script>
</body>
</html>
"""
