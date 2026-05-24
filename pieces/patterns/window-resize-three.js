// PATTERN: window-resize-three
// Keeps a Three.js PerspectiveCamera + renderer in sync with window resizes.
// Identical across all pieces — no tunable params.
//
// USAGE (copy into your <script type="module">, after camera + renderer are defined):

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
