// Relais entre l'overlay (dans la page) et le moteur Python (ws://127.0.0.1:8765).
// La connexion part d'ici et non de la page : elle n'est pas bloquée par les règles du site.
const URL_MOTEUR = "ws://127.0.0.1:8765";
const ports = new Set();
let ws = null;
let retry = null;

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try { ws = new WebSocket(URL_MOTEUR); } catch (e) { schedule(); return; }
  ws.onopen = () => broadcast({ type: "engine", connected: true });
  ws.onmessage = (ev) => { try { broadcast(JSON.parse(ev.data)); } catch (e) {} };
  ws.onclose = () => { broadcast({ type: "engine", connected: false }); schedule(); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}
function schedule() {
  if (retry || ports.size === 0) return;
  retry = setTimeout(() => { retry = null; connect(); }, 2000);
}
function broadcast(msg) {
  for (const p of ports) { try { p.postMessage(msg); } catch (e) { ports.delete(p); } }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "overlay") return;
  ports.add(port);
  connect();
  port.postMessage({ type: "engine", connected: !!ws && ws.readyState === WebSocket.OPEN });
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "hello" }));
  port.onMessage.addListener((msg) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  });
  port.onDisconnect.addListener(() => ports.delete(port));
});
