// A small, local WebGL STL viewer; no external scripts or image uploads.
export class MeshViewer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext("webgl", { antialias: true, alpha: true });
    if (!gl) throw new Error("3D preview needs WebGL. Artwork preview and STL downloads are still available.");
    this.gl = gl;
    this.yaw = 0.3;
    this.pitch = 0.85;
    this.zoom = 1;
    const vertex = `
      attribute vec3 position;
      attribute vec3 normal;
      uniform mat3 rotation;
      uniform vec2 scale;
      uniform float radius;
      varying vec3 lightNormal;
      void main() {
        vec3 p = rotation * position;
        gl_Position = vec4(p.xy * scale / radius, -p.z / (radius * 8.0), 1.0);
        lightNormal = rotation * normal;
      }`;
    const fragment = `
      precision mediump float;
      varying vec3 lightNormal;
      uniform vec3 color;
      void main() {
        float light = .48 + .52 * max(dot(normalize(lightNormal), normalize(vec3(-.4,.8,1.0))),0.0);
        gl_FragColor = vec4(color * light, 1.0);
      }`;
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        throw new Error(`Could not initialize 3D preview: ${gl.getShaderInfoLog(shader)}`);
      }
      return shader;
    };
    this.program = gl.createProgram();
    gl.attachShader(this.program, compile(gl.VERTEX_SHADER, vertex));
    gl.attachShader(this.program, compile(gl.FRAGMENT_SHADER, fragment));
    gl.linkProgram(this.program);
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error("Could not link 3D preview shaders.");
    this.buffer = gl.createBuffer();
    gl.useProgram(this.program);
    gl.enable(gl.DEPTH_TEST);
    const location = (name) => gl.getUniformLocation(this.program, name);
    this.uniforms = Object.fromEntries(["rotation", "scale", "radius", "color"].map((name) => [name, location(name)]));
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    for (const [name, offset] of [["position", 0], ["normal", 12]]) {
      const attribute = gl.getAttribLocation(this.program, name);
      gl.enableVertexAttribArray(attribute);
      gl.vertexAttribPointer(attribute, 3, gl.FLOAT, false, 24, offset);
    }
    let previous = null;
    canvas.addEventListener("pointerdown", (event) => {
      canvas.setPointerCapture(event.pointerId);
      previous = [event.clientX, event.clientY];
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!previous) return;
      this.yaw += (event.clientX - previous[0]) * 0.01;
      this.pitch = Math.max(0.15, Math.min(2.9, this.pitch + (event.clientY - previous[1]) * 0.01));
      previous = [event.clientX, event.clientY];
      this.draw();
    });
    canvas.addEventListener("lostpointercapture", () => { previous = null; });
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      this.zoom = Math.max(0.45, Math.min(3, this.zoom * Math.exp(-event.deltaY * 0.001)));
      this.draw();
    }, { passive: false });
    new ResizeObserver(() => this.draw()).observe(canvas);
  }

  load(data, kind) {
    const view = new DataView(data);
    if (data.byteLength < 84) throw new Error("The STL preview is incomplete.");
    const triangles = view.getUint32(80, true);
    if (data.byteLength !== 84 + triangles * 50) throw new Error("The STL preview is invalid.");
    const vertices = new Float32Array(triangles * 18);
    const low = [Infinity, Infinity, Infinity];
    const high = [-Infinity, -Infinity, -Infinity];
    for (let face = 0; face < triangles; face++) {
      const start = 84 + face * 50;
      for (let corner = 0; corner < 3; corner++) {
        const target = face * 18 + corner * 6;
        for (let axis = 0; axis < 3; axis++) {
          const value = view.getFloat32(start + 12 + corner * 12 + axis * 4, true);
          vertices[target + axis] = value;
          vertices[target + 3 + axis] = view.getFloat32(start + axis * 4, true);
          low[axis] = Math.min(low[axis], value);
          high[axis] = Math.max(high[axis], value);
        }
      }
    }
    for (let offset = 0; offset < vertices.length; offset += 6) {
      for (let axis = 0; axis < 3; axis++) vertices[offset + axis] -= (high[axis] + low[axis]) / 2;
    }
    this.radius = Math.hypot(...high.map((value, axis) => value - low[axis])) * 0.59;
    this.kind = kind;
    this.count = triangles * 3;
    this.zoom = 1;
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.STATIC_DRAW);
    this.draw();
  }

  draw() {
    if (!this.count || this.canvas.hidden) return;
    const { canvas, gl } = this;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const rotate = ([x, y, z]) => {
      const horizontal = x * cy - y * sy;
      const depth = -x * sy - y * cy;
      return [horizontal, z * cp - depth * sp, z * sp + depth * cp];
    };
    gl.uniformMatrix3fv(this.uniforms.rotation, false, new Float32Array([
      ...rotate([1, 0, 0]), ...rotate([0, 1, 0]), ...rotate([0, 0, 1]),
    ]));
    const aspect = width / height;
    gl.uniform2f(this.uniforms.scale, this.zoom / Math.max(aspect, 1), this.zoom * Math.min(aspect, 1));
    gl.uniform1f(this.uniforms.radius, this.radius);
    gl.uniform3fv(this.uniforms.color, this.kind === "cutter" ? [0.65, 0.44, 0.29] : [0.56, 0.20, 0.29]);
    gl.drawArrays(gl.TRIANGLES, 0, this.count);
  }
}
