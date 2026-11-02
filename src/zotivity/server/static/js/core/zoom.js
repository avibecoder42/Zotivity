// Zoom der ganzen Oberfläche im Programmfenster: Regler in der Statusleiste, Strg + Mausrad, Pinch auf dem Touchpad (kommt
// als Strg + Mausrad an) und Strg +/−/0. Der Wert bleibt über den Neustart. Im Browser zoomt der Browser selbst.
import { update } from './store.js';

export const ZOOMS = [70, 80, 90, 100, 110, 125, 150, 175, 200];
const KEY = 'zllm.zoom';
const GESTURE_GAP = 250;  // ms ohne Mausrad-Ereignis: Die nächste Bewegung ist eine neue Geste
let zoom = 100;
try { zoom = Number(localStorage.getItem(KEY)) || 100; } catch { /* privat */ }

const desktop = () => (window.pywebview && window.pywebview.api) || null;
export const canZoom = () => !!(desktop() && desktop().set_zoom);
export const currentZoom = () => zoom;

export function applyZoom() {
  if (canZoom() && zoom !== 100) Promise.resolve(desktop().set_zoom(zoom / 100)).catch(() => {});
}

export function setZoom(value) {
  zoom = value;
  try { localStorage.setItem(KEY, String(zoom)); } catch { /* privat */ }
  if (canZoom()) Promise.resolve(desktop().set_zoom(zoom / 100)).catch(() => {});
  update();
}

export function stepZoom(dir) {
  const next = dir > 0 ? ZOOMS.find(z => z > zoom) : [...ZOOMS].reverse().find(z => z < zoom);
  if (next) setZoom(next);
}

export function installZoom() {
  let sum = 0;
  let scrolling = false;
  let lastWheel = 0;
  let lastStep = 0;
  window.addEventListener('wheel', e => {
    const now = performance.now();
    if (now - lastWheel > GESTURE_GAP) { sum = 0; scrolling = false; }
    lastWheel = now;
    if (!e.ctrlKey) { scrolling = true; return; }
    if (!canZoom()) return;
    e.preventDefault();
    // Beim Scrollen mit zwei Fingern meldet Windows auch kleine Skalierungen, ebenfalls als Strg + Mausrad: Sie zählen nicht,
    // und was eine Geste nicht erreicht, verfällt mit ihr. Pinch liefert viele kleine Schritte: erst ab einer spürbaren
    // Bewegung eine Stufe, höchstens alle 120 ms.
    if (scrolling) return;
    sum += e.deltaY;
    if (Math.abs(sum) < 40 || now - lastStep < 120) return;
    stepZoom(sum < 0 ? 1 : -1);
    sum = 0;
    lastStep = now;
  }, { passive: false });
  window.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey) || !canZoom()) return;
    if (e.key === '+' || e.key === '=') stepZoom(1);
    else if (e.key === '-') stepZoom(-1);
    else if (e.key === '0') setZoom(100);
    else return;
    e.preventDefault();
  });
}
