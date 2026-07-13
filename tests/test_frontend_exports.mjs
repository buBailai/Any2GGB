import assert from "node:assert/strict";

const coordCalls = [];
const colorCalls = [];
const thicknessCalls = [];
const renameCalls = [];
let objectNames = [];
const objectTypes = new Map();
const fakeApi = {
  enable3D() {}, setPerspective() {}, setCoordSystem(...args) { coordCalls.push(args); },
  newConstruction() { objectNames = []; objectTypes.clear(); },
  getAllObjectNames() { return [...objectNames]; }, getBase64() { return "QUJD"; },
  getObjectType(name) { return objectTypes.get(name) || ""; },
  evalCommand() { return true; },
  evalCommandGetLabels(command) {
    if (command.startsWith("sq=Polygon(")) {
      objectNames.push("sq", "edge1", "edge2", "edge3", "edge4");
      objectTypes.set("sq", "polygon");
      for (const edge of ["edge1", "edge2", "edge3", "edge4"]) objectTypes.set(edge, "segment");
      return "sq,edge1,edge2,edge3,edge4";
    }
    return "";
  },
  exists(name) { return objectNames.includes(name); },
  renameObject(oldName, newName) {
    const index = objectNames.indexOf(oldName);
    if (index < 0) return false;
    objectNames[index] = newName;
    objectTypes.set(newName, objectTypes.get(oldName));
    objectTypes.delete(oldName);
    renameCalls.push([oldName, newName]);
    return true;
  },
  setColor(...args) { colorCalls.push(args); },
  setLineThickness(...args) { thicknessCalls.push(args); },
  setSize() {},
  getViewProperties() { return JSON.stringify({ width: 900, height: 450 }); },
};

globalThis.window = globalThis;
globalThis.window.addEventListener = () => {};
globalThis.document = {
  getElementById() { return { clientWidth: 800, clientHeight: 600 }; },
};
globalThis.GGBApplet = class {
  constructor(options) { this.options = options; }
  setHTML5Codebase() {}
  inject() { this.options.appletOnLoad(fakeApi); }
};

await import("../frontend/ggb_host.js");
window.GGBHost.init("ggb");
window.GGBHost.execute("# perspective: 3d\n");
const html = window.GGBHost.exportInteractiveHTML('圆锥 <截面> "互动"');

assert.match(html, /ggbBase64:\s*"QUJD"/);
assert.match(html, /appName:\s*"3d"/);
assert.ok(html.includes("圆锥 &lt;截面&gt; &quot;互动&quot;"));
assert.ok(!html.includes('<title>圆锥 <截面>'));

coordCalls.length = 0;
window.GGBHost.execute("# perspective: 2d\n# view: -5 -5 5 5\n");
const fitted = coordCalls.at(-1);
assert.deepEqual(fitted, [-10, 10, -5, 5]);
assert.equal((fitted[1] - fitted[0]) / 900, (fitted[3] - fitted[2]) / 450);

colorCalls.length = 0;
thicknessCalls.length = 0;
renameCalls.length = 0;
window.GGBHost.execute("# perspective: 2d\nsq=Polygon(A,B,C,D)\nSetColor(sq,255,255,255)\nSetLineThickness(sq,4)\n");
for (const edge of ["a2g_sq_edge1", "a2g_sq_edge2", "a2g_sq_edge3", "a2g_sq_edge4"]) {
  assert.ok(colorCalls.some(call => JSON.stringify(call) === JSON.stringify([edge, 35, 35, 35])));
  assert.ok(thicknessCalls.some(call => JSON.stringify(call) === JSON.stringify([edge, 4])));
}
assert.equal(renameCalls.length, 4);
assert.ok(!colorCalls.some(call => call.slice(1).every(value => value === 255)));

console.log("interactive export, equal-axis fitting, and polygon outline styling ok");
