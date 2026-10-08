/**
 * ReFit's reusable car-part viewer. No framework or build step required.
 * See PART_PREVIEW.md for HTML, local-file, URL, and Python integration examples.
 */
(function (root) {
  'use strict';

  const defaults = {
    maxTriangles: 24000,
    rotationSpeed: 0.009,
    initialPitch: -0.48,
    initialYaw: 0.62,
    maxPitch: 1.5,
    scale: 1.35,
    interactive: true,
    background: '#e9ede5',
    color: [49, 95, 75]
  };

  function settings(options = {}) {
    const result = { ...defaults, ...options };
    if (!Number.isInteger(result.maxTriangles) || result.maxTriangles < 1) {
      throw new Error('maxTriangles must be a positive integer.');
    }
    return result;
  }

  function validateMesh(positions) {
    if (!positions || !positions.length || positions.length % 9 !== 0) {
      throw new Error('A mesh must contain complete triangles: nine coordinates per triangle.');
    }
    for (const value of positions) {
      if (!Number.isFinite(value)) throw new Error('The mesh contains invalid coordinates.');
    }
    return positions instanceof Float32Array ? positions : new Float32Array(positions);
  }

  function decodeMesh(buffer) {
    if (buffer.byteLength % 36 !== 0) throw new Error('The server returned an invalid preview mesh.');
    const view = new DataView(buffer), positions = new Float32Array(buffer.byteLength / 4);
    for (let i = 0; i < positions.length; i++) positions[i] = view.getFloat32(i * 4, true);
    return validateMesh(positions);
  }

  function parseSTL(buffer) {
    const view = new DataView(buffer);
    const count = buffer.byteLength >= 84 ? view.getUint32(80, true) : 0;
    if (count > 0 && 84 + count * 50 <= buffer.byteLength) {
      const positions = new Float32Array(count * 9);
      for (let i = 0; i < count; i++) {
        const offset = 84 + i * 50 + 12;
        for (let j = 0; j < 9; j++) positions[i * 9 + j] = view.getFloat32(offset + j * 4, true);
      }
      return positions;
    }
    const text = new TextDecoder().decode(buffer), positions = [];
    const vertex = /vertex\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)/gi;
    let match;
    while ((match = vertex.exec(text))) positions.push(+match[1], +match[2], +match[3]);
    return new Float32Array(positions);
  }

  function parseOBJ(text) {
    const vertices = [], positions = [];
    for (let line of text.split(/\r?\n/)) {
      line = line.split('#')[0].trim();
      if (!line) continue;
      const fields = line.split(/\s+/);
      if (fields[0] === 'v' && fields.length >= 4) {
        vertices.push([+fields[1], +fields[2], +fields[3]]);
      } else if (fields[0] === 'f' && fields.length >= 4) {
        const face = fields.slice(1).map(value => {
          const index = parseInt(value.split('/')[0], 10);
          return vertices[index < 0 ? vertices.length + index : index - 1];
        });
        if (face.some(vertex => !vertex)) throw new Error('The OBJ file refers to a missing vertex.');
        for (let i = 1; i < face.length - 1; i++) positions.push(...face[0], ...face[i], ...face[i + 1]);
      }
    }
    return new Float32Array(positions);
  }

  function modelEntries(buffer) {
    const bytes = new Uint8Array(buffer), view = new DataView(buffer);
    let end = -1;
    for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
      if (view.getUint32(i, true) === 0x06054b50) { end = i; break; }
    }
    if (end < 0) throw new Error('Invalid 3MF ZIP container.');
    const count = view.getUint16(end + 10, true), entries = [];
    let offset = view.getUint32(end + 16, true);
    for (let i = 0; i < count; i++) {
      if (offset + 46 > bytes.length || view.getUint32(offset, true) !== 0x02014b50) {
        throw new Error('Invalid 3MF ZIP directory.');
      }
      const method = view.getUint16(offset + 10, true), size = view.getUint32(offset + 20, true);
      const nameLength = view.getUint16(offset + 28, true), extraLength = view.getUint16(offset + 30, true);
      const commentLength = view.getUint16(offset + 32, true), local = view.getUint32(offset + 42, true);
      const name = new TextDecoder().decode(bytes.slice(offset + 46, offset + 46 + nameLength));
      if (name.toLowerCase().endsWith('.model')) {
        if (local + 30 > bytes.length || view.getUint32(local, true) !== 0x04034b50) {
          throw new Error('Invalid 3MF ZIP entry.');
        }
        const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
        if (start + size > bytes.length) throw new Error('Incomplete 3MF model data.');
        entries.push({ name, method, bytes: bytes.slice(start, start + size) });
      }
      offset += 46 + nameLength + extraLength + commentLength;
    }
    if (!entries.length) throw new Error('3MF model data was not found.');
    return entries;
  }

  async function parse3MF(buffer, options = {}) {
    const { maxTriangles } = settings(options);
    const sample = new Float32Array(maxTriangles * 9);
    let count = 0, kept = 0, seed = 1;
    for (const entry of modelEntries(buffer)) {
      let xmlBytes;
      if (entry.method === 0) xmlBytes = entry.bytes;
      else if (entry.method === 8) {
        if (!root.DecompressionStream) throw new Error('Use the Python 3MF helper with this browser.');
        let decoder;
        try { decoder = new root.DecompressionStream('deflate-raw'); }
        catch { throw new Error('This browser needs the Python helper to decompress 3MF files.'); }
        const stream = new Blob([entry.bytes]).stream().pipeThrough(decoder);
        xmlBytes = new Uint8Array(await new Response(stream).arrayBuffer());
      } else throw new Error('Unsupported 3MF compression.');
      const xml = new DOMParser().parseFromString(new TextDecoder().decode(xmlBytes), 'application/xml');
      if (xml.querySelector('parsererror')) throw new Error('The 3MF model XML is invalid.');
      for (const mesh of xml.getElementsByTagNameNS('*', 'mesh')) {
        const vertices = Array.from(mesh.getElementsByTagNameNS('*', 'vertex'), vertex =>
          ['x', 'y', 'z'].map(axis => parseFloat(vertex.getAttribute(axis))));
        for (const triangle of mesh.getElementsByTagNameNS('*', 'triangle')) {
          const points = ['v1', 'v2', 'v3'].map(key => vertices[Number(triangle.getAttribute(key))]);
          if (points.some(point => !point)) throw new Error('The 3MF file refers to a missing vertex.');
          count++;
          let slot = kept;
          if (kept < maxTriangles) kept++;
          else {
            seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
            slot = Math.floor(seed / 4294967296 * count);
            if (slot >= maxTriangles) continue;
          }
          sample.set(points.flat(), slot * 9);
        }
      }
    }
    return sample.slice(0, kept * 9);
  }

  async function parse(fileName, buffer, options = {}) {
    const extension = String(fileName).split('.').pop().toLowerCase();
    let positions;
    if (extension === 'stl') positions = parseSTL(buffer);
    else if (extension === 'obj') positions = parseOBJ(new TextDecoder().decode(buffer));
    else if (extension === '3mf') positions = await parse3MF(buffer, options);
    else throw new Error('Supported previews are STL, OBJ, and 3MF.');
    return validateMesh(positions);
  }

  function renderMesh(canvas, positions, options) {
    positions = validateMesh(positions);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas rendering is unavailable in this browser.');
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < positions.length; i += 3) {
      for (let axis = 0; axis < 3; axis++) {
        min[axis] = Math.min(min[axis], positions[i + axis]);
        max[axis] = Math.max(max[axis], positions[i + axis]);
      }
    }
    const center = min.map((value, axis) => (value + max[axis]) / 2);
    const span = Math.max(...max.map((value, axis) => value - min[axis])) || 1;
    const step = Math.max(1, Math.ceil(positions.length / 9 / options.maxTriangles)), triangles = [];
    for (let i = 0; i < positions.length; i += step * 9) {
      const points = [];
      for (let vertex = 0; vertex < 9; vertex += 3) {
        points.push(center.map((value, axis) => (positions[i + vertex + axis] - value) / span));
      }
      triangles.push(points);
    }

    const quatX = angle => [Math.cos(angle / 2), Math.sin(angle / 2), 0, 0];
    const quatY = angle => [Math.cos(angle / 2), 0, Math.sin(angle / 2), 0];
    function multiply(a, b) {
      return [
        a[0]*b[0]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3],
        a[0]*b[1]+a[1]*b[0]+a[2]*b[3]-a[3]*b[2],
        a[0]*b[2]-a[1]*b[3]+a[2]*b[0]+a[3]*b[1],
        a[0]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[0]
      ];
    }
    const normalize = quaternion => {
      const length = Math.hypot(...quaternion) || 1;
      return quaternion.map(value => value / length);
    };
    let orientation = normalize(multiply(quatX(options.initialPitch), quatY(options.initialYaw)));
    let pitch = 0, dragging = false, lastX = 0, lastY = 0;
    const oldCursor = canvas.style.cursor, oldTouchAction = canvas.style.touchAction;
    canvas.style.touchAction = 'none';
    canvas.style.cursor = options.interactive ? 'grab' : 'default';

    function paint() {
      const rect = canvas.getBoundingClientRect(), ratio = Math.min(root.devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(rect.width * ratio)), height = Math.max(1, Math.round(rect.height * ratio));
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      context.clearRect(0, 0, width, height);
      context.fillStyle = options.background;
      context.fillRect(0, 0, width, height);
      const scale = Math.min(width, height) * options.scale, cx = width / 2, cy = height / 2;
      const [qw, qx, qy, qz] = orientation;
      function rotate(point) {
        const tx = 2*(qy*point[2]-qz*point[1]), ty = 2*(qz*point[0]-qx*point[2]), tz = 2*(qx*point[1]-qy*point[0]);
        return [point[0]+qw*tx+qy*tz-qz*ty, point[1]+qw*ty+qz*tx-qx*tz, point[2]+qw*tz+qx*ty-qy*tx];
      }
      const faces = triangles.map(points => {
        const q = points.map(rotate), [a, b, c] = q;
        const ux=b[0]-a[0], uy=b[1]-a[1], uz=b[2]-a[2], vx=c[0]-a[0], vy=c[1]-a[1], vz=c[2]-a[2];
        const nx=uy*vz-uz*vy, ny=uz*vx-ux*vz, nz=ux*vy-uy*vx, length=Math.hypot(nx,ny,nz)||1;
        return { q, depth:(a[2]+b[2]+c[2])/3, shade:0.36+0.64*Math.abs((-0.3*nx+0.55*ny+0.8*nz)/length) };
      }).sort((a, b) => a.depth - b.depth);
      for (const face of faces) {
        context.beginPath();
        context.moveTo(cx + face.q[0][0] * scale, cy - face.q[0][1] * scale);
        context.lineTo(cx + face.q[1][0] * scale, cy - face.q[1][1] * scale);
        context.lineTo(cx + face.q[2][0] * scale, cy - face.q[2][1] * scale);
        context.closePath();
        context.fillStyle = `rgb(${options.color.map(value => Math.round(value * face.shade)).join(',')})`;
        context.fill();
        context.strokeStyle = 'rgba(25,48,38,.13)';
        context.lineWidth = Math.max(0.35, ratio * 0.35);
        context.stroke();
      }
    }

    function pointerDown(event) {
      if (!options.interactive || (event.pointerType === 'mouse' && event.button !== 0)) return;
      dragging = true; lastX = event.clientX; lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
      canvas.style.cursor = 'grabbing';
    }
    function pointerMove(event) {
      if (!dragging) return;
      const yawDelta = (event.clientX - lastX) * options.rotationSpeed;
      const nextPitch = Math.max(-options.maxPitch, Math.min(options.maxPitch, pitch + (event.clientY-lastY)*options.rotationSpeed));
      const pitchDelta = nextPitch - pitch;
      pitch = nextPitch;
      // Pre-multiply camera axes: horizontal drag uses screen up; vertical drag uses screen right.
      orientation = normalize(multiply(multiply(quatY(yawDelta), quatX(pitchDelta)), orientation));
      lastX = event.clientX; lastY = event.clientY;
      paint();
    }
    function release(event) {
      dragging = false;
      canvas.style.cursor = options.interactive ? 'grab' : 'default';
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    }
    const handlers = { pointerdown:pointerDown, pointermove:pointerMove, pointerup:release, pointercancel:release, lostpointercapture:release };
    for (const [name, handler] of Object.entries(handlers)) canvas.addEventListener(name, handler);
    let observer;
    if (root.ResizeObserver) { observer = new root.ResizeObserver(paint); observer.observe(canvas); }
    else root.addEventListener('resize', paint, { passive: true });
    paint();
    return {
      resize: paint,
      destroy() {
        for (const [name, handler] of Object.entries(handlers)) canvas.removeEventListener(name, handler);
        if (observer) observer.disconnect(); else root.removeEventListener('resize', paint);
        dragging = false;
        canvas.style.cursor = oldCursor;
        canvas.style.touchAction = oldTouchAction;
      }
    };
  }

  function create(canvas, options = {}) {
    if (!canvas || typeof canvas.getContext !== 'function') throw new Error('Provide a canvas element.');
    const config = settings(options), status = options.statusElement;
    let renderer = null, request = null, version = 0, destroyed = false;
    const message = text => { if (status) status.textContent = text; };
    function stop() {
      if (request) request.abort();
      request = null;
      if (renderer) renderer.destroy();
      renderer = null;
    }
    function ensureOpen() { if (destroyed) throw new Error('This viewer has been destroyed.'); }
    function display(positions) {
      renderer = renderMesh(canvas, positions, config);
      message(config.interactive ? '3D preview · drag with the left mouse button to rotate' : '3D preview');
    }
    async function load(task) {
      ensureOpen(); stop();
      const token = ++version, controller = new AbortController();
      request = controller;
      message('Loading 3D preview…');
      try {
        const positions = await task(controller.signal);
        if (destroyed || token !== version) return null;
        display(positions);
        return positions;
      } catch (error) {
        if (destroyed || token !== version) return null;
        message('Could not render this 3D file: ' + error.message);
        throw error;
      } finally { if (token === version) request = null; }
    }
    return {
      loadUrl(url, { fileName = String(url).split(/[?#]/)[0], fetchOptions = {} } = {}) {
        return load(async signal => {
          const response = await fetch(url, { credentials: 'same-origin', ...fetchOptions, signal });
          if (!response.ok) {
            let error = `Request failed (${response.status})`;
            if ((response.headers.get('Content-Type') || '').includes('json')) {
              const body = await response.json(); error = body.error || error;
            }
            throw new Error(error);
          }
          const buffer = await response.arrayBuffer();
          return response.headers.get('X-ReFit-Mesh') === 'float32-le' ? decodeMesh(buffer) : parse(fileName, buffer, config);
        });
      },
      loadFile(file) { return load(async () => parse(file.name, await file.arrayBuffer(), config)); },
      loadBuffer(fileName, buffer) { return load(() => parse(fileName, buffer, config)); },
      setMesh(positions) {
        ensureOpen(); stop(); ++version;
        try { display(positions); } catch (error) { message(error.message); throw error; }
      },
      resize() { if (renderer) renderer.resize(); },
      destroy() { if (!destroyed) { destroyed = true; ++version; stop(); } }
    };
  }

  root.PartPreview = Object.freeze({ create, parse, decodeMesh });
})(globalThis);
