// PATTERN: renderer-hidpi-setup
// Creates a full-window Three.js WebGLRenderer with HiDPI support and mounts it.
// TUNABLE:
//   antialias  {boolean} — true for smooth edges; false if performance matters
//   clearColor {hex}     — e.g. 0x000000. Omit to let scene.background handle it.
//
// USAGE (copy into your <script type="module">):

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);

// Optional: set clear color directly on renderer (useful when scene has no background object)
// renderer.setClearColor(0x000000, 1);
