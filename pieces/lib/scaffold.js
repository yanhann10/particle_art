/**
 * scaffold.js — shared setup for particle_art pieces.
 *
 * Composes: renderer-hidpi-setup + window-resize-three + piece-control-panel.
 * Import once at the top of any piece's <script type="module">.
 *
 * USAGE
 * ─────
 *   import { createRenderer, mountControlPanel } from '../../lib/scaffold.js';
 *
 *   const { renderer, scene, camera } = createRenderer({ clearColor: 0x000000, fov: 62 });
 *   mountControlPanel();
 *   // ... your generative kernel here ...
 *   function loop() { renderer.render(scene, camera); requestAnimationFrame(loop); }
 *   loop();
 *
 * API
 * ───
 *   createRenderer(opts?) → { renderer, scene, camera }
 *     opts.clearColor  {number}  hex clear color  (default 0x000000)
 *     opts.fov         {number}  camera field-of-view (default 62)
 *     opts.near        {number}  camera near plane (default 0.1)
 *     opts.far         {number}  camera far plane  (default 200)
 *     opts.antialias   {boolean} WebGL antialias   (default true)
 *     opts.fog         {object|null} { color, density } for FogExp2; null = no fog
 *
 *   mountControlPanel() — injects the piece HUD (♥ ✕ → ×) and wires up all handlers.
 *     Call once after DOM is ready (i.e., at the module's top level, not in a callback).
 */

import * as THREE from 'three';

export function createRenderer(opts = {}) {
  const {
    clearColor = 0x000000,
    fov        = 62,
    near       = 0.1,
    far        = 200,
    antialias  = true,
    fog        = null,
  } = opts;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(clearColor);
  if (fog) scene.fog = new THREE.FogExp2(fog.color, fog.density);

  const camera = new THREE.PerspectiveCamera(fov, innerWidth / innerHeight, near, far);

  const renderer = new THREE.WebGLRenderer({ antialias });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  document.body.appendChild(renderer.domElement);

  addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });

  return { renderer, scene, camera };
}

export function mountControlPanel() {
  const pid = (() => {
    const el = document.querySelector('.id');
    return (el ? el.textContent.trim() : '') ||
           location.pathname.split('/').filter(Boolean).pop();
  })();

  const FKEY = 'particle_art_favorites';
  const DKEY = 'particle_art_dismissed';
  function addToSet(key, id) {
    try {
      const s = new Set(JSON.parse(localStorage.getItem(key) || '[]'));
      s.add(id);
      localStorage.setItem(key, JSON.stringify([...s]));
      return true;
    } catch { return false; }
  }

  const panel = document.createElement('div');
  panel.id = 'piece-ctrl';
  panel.style.cssText = "position:fixed;top:10px;right:10px;z-index:999;display:flex;align-items:center;gap:6px;font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:11px;";
  panel.innerHTML = `
    <button id="pc-like"    title="keep this direction" style="background:rgba(0,0,0,0.55);border:1px solid rgba(255,255,255,0.13);border-radius:3px;padding:4px 8px;color:rgba(201,168,106,0.7);cursor:pointer;font-size:13px;line-height:1">♥</button>
    <button id="pc-dismiss" title="drop from pool"      style="background:rgba(0,0,0,0.55);border:1px solid rgba(255,255,255,0.13);border-radius:3px;padding:4px 8px;color:rgba(215,107,90,0.7);cursor:pointer;font-size:13px;line-height:1">✕</button>
    <input  id="pc-input"   type="text" maxlength="200" placeholder="What to change…" style="width:160px;background:rgba(0,0,0,0.6);color:rgba(255,255,255,0.8);border:1px solid rgba(255,255,255,0.13);border-radius:3px;padding:4px 8px;font-family:inherit;font-size:11px;outline:none">
    <button id="pc-submit"  title="Generate iteration"  style="background:rgba(0,0,0,0.55);border:1px solid rgba(201,168,106,0.4);border-radius:3px;padding:4px 10px;color:rgba(201,168,106,0.9);cursor:pointer;font-size:11px;line-height:1">→</button>
    <button id="pc-close"   title="close / go back"     style="background:rgba(0,0,0,0.55);border:1px solid rgba(255,255,255,0.13);border-radius:3px;padding:4px 10px;color:rgba(255,255,255,0.45);cursor:pointer;font-size:11px;line-height:1">×</button>`;
  document.body.appendChild(panel);

  panel.querySelector('#pc-like').onclick    = function() { addToSet(FKEY, pid); this.style.color = '#c9a86a'; };
  panel.querySelector('#pc-dismiss').onclick = function() { addToSet(DKEY, pid); this.style.color = '#d76b5a'; };
  panel.querySelector('#pc-close').onclick   = () => window.parent !== window
    ? window.parent.postMessage({ type: 'dismiss-and-next' }, '*')
    : history.back();

  const input  = panel.querySelector('#pc-input');
  const submit = panel.querySelector('#pc-submit');
  input.addEventListener('focus', () => { input.style.borderColor = 'rgba(201,168,106,0.6)'; });
  input.addEventListener('blur',  () => { input.style.borderColor = 'rgba(255,255,255,0.13)'; });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') submit.click(); });
  submit.onclick = () => {
    const txt = input.value.trim();
    if (!txt) return;
    submit.textContent = '…'; submit.disabled = true;
    fetch('/api/mutate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_id: pid, directive: txt }),
    })
      .then(r => r.json())
      .then(d => {
        const qs = `?a=${pid}${d.job_id ? `&job=${d.job_id}` : ''}&directive=${encodeURIComponent(txt)}`;
        location.href = '/compare' + qs;
      })
      .catch(() => {
        location.href = `/compare?a=${pid}&directive=${encodeURIComponent(txt)}`;
      });
  };
}
