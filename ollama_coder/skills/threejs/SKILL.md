---
name: threejs
description: Working with three.js and React Three Fiber against the version actually installed, instead of a remembered one.
---

# three.js

three.js ships a release roughly monthly and removes APIs without a deprecation
period. Whatever you remember about it is probably wrong for the version in
front of you. **Check first, then write.**

## Establish the version before writing any code

```bash
cat node_modules/three/package.json | grep '"version"'
# React Three Fiber projects pin these separately:
cat node_modules/@react-three/fiber/package.json 2>/dev/null | grep '"version"'
```

If there is no `node_modules`, read `package.json` and say which version you are
targeting. Do not proceed on an assumption.

## Verify an API instead of recalling it

The installed package is the authority, and it is right there on disk:

```bash
# does this class/method exist in this version?
grep -rn "class Mesh\b" node_modules/three/src/objects/Mesh.js
grep -rn "outputColorSpace" node_modules/three/src/renderers/WebGLRenderer.js

# the bundled typings are the fastest complete answer
grep -n "colorSpace\|outputColorSpace" node_modules/three/build/three.d.ts | head
```

For anything the source does not settle, fetch the docs for that exact version
rather than searching generically. These URLs work as-is with `fetch_url`:

- `https://api.github.com/repos/mrdoob/three.js/releases` — release notes as
  JSON, newest first. The most reliable source for "what changed in rNNN".
- `https://threejs.org/docs/` — current API reference.
- `https://github.com/mrdoob/three.js/wiki/Migration-Guide` — removals by
  revision. Fetch **this** URL; the wiki is not in the git repo, so any
  `raw.githubusercontent.com/.../Migration-Guide.md` guess returns 404.

Search with the revision in the query (`three.js r185 <thing>`), because
untagged results are usually years old.

## Long-standing removals worth knowing

These have been true for many releases, but still confirm against the installed
source before relying on them:

- **`THREE.Geometry` no longer exists** (removed r125). Everything is
  `BufferGeometry`. Code using `.vertices`, `.faces` or `new THREE.Geometry()`
  is pre-r125 and will throw.
- **`examples/js/` was removed** (r148). Import addons as ES modules from
  `three/examples/jsm/…` — `OrbitControls`, `GLTFLoader`, `EffectComposer` all
  live there, not on the `THREE` namespace.
- **Colour management changed** (r152). `outputEncoding` / `sRGBEncoding` became
  `outputColorSpace` / `SRGBColorSpace`, and colour management is on by default.
- **`WebGLRenderer` is not the only renderer any more.** Newer revisions ship a
  WebGPU renderer and a node-based material system; if the project imports from
  `three/webgpu`, the material API is different from the classic one.

## House rules

- Dispose what you create: geometries, materials and textures are not GC'd for
  you. Every `new` in a component needs a matching `.dispose()` on teardown.
- Never allocate inside the render loop. Hoist `Vector3`/`Quaternion` scratch
  objects; a `new THREE.Vector3()` per frame is a per-frame allocation.
- Prefer `useFrame` over a manual `requestAnimationFrame` in R3F, and never call
  `setState` inside it — mutate the ref directly.
- In R3F, do not remember the prop API either. Its `<mesh>` props map onto the
  installed three.js version, so the same version check applies.
