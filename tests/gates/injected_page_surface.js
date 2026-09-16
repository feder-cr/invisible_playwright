// What the injected bundle touches on the PAGE's side, measured by running it.
//
// The bundle is 312 KB of vendored upstream code that lives inside the page's
// process. Reading it is how three separate tells were found one at a time and
// a fourth was missed, so this gate does not read it: it constructs the real
// InjectedScript against a window and a document that record every access, and
// prints what was touched, phase by phase.
//
// WHAT IT PROVES. The bundle reaches the page through `this.window`. In the
// utility world that object is the page's window seen through an Xray, so a
// CALL on it (addEventListener, dispatchEvent) lands on the page either way -
// which is why moving these installations into the utility world did not stop
// them. This harness models exactly that: whatever the bundle calls on the
// window we hand it is what a site would see.
//
// WHAT IT DOES NOT PROVE. It says nothing about Gecko's Xray semantics for an
// ASSIGNMENT (`this.window.x = 1`), which may or may not stay inside the
// sandbox. Only calls are modelled, and only calls are asserted.
//
// Usage: node injected_page_surface.js <path to injected.js>
'use strict';
const fs = require('fs');

const bundlePath = process.argv[2];
if (!bundlePath) {
  console.error('usage: node injected_page_surface.js <injected.js>');
  process.exit(2);
}
const source = fs.readFileSync(bundlePath, 'utf8');

const touches = [];
const record = (kind, detail) => touches.push(Object.assign({ kind }, detail));

function makeNode(name) {
  const node = {
    nodeName: name.toUpperCase(),
    nodeType: 1,
    isConnected: true,
    ownerDocument: null,
    childNodes: [],
    style: {},
    classList: { add() {}, remove() {}, contains() { return false; } },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    hasAttribute() { return false; },
    matches() { return false; }, closest() { return null; },
    isContentEditable: false, parentElement: null,
    appendChild(c) { node.childNodes.push(c); return c; },
    removeChild(c) { return c; },
    remove() {},
    addEventListener(type) { record('node.addEventListener', { name, type }); },
    removeEventListener(type) { record('node.removeEventListener', { name, type }); },
    dispatchEvent(e) { record('node.dispatchEvent', { name, type: e && e.type }); return true; },
    getBoundingClientRect() {
      return { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 };
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    attachShadow() { return makeNode('shadow-root'); },
    get shadowRoot() { return null; },
    // The hit test walks up to the enclosing root and asks it what is at the
    // point. Without these the phase raises, which the gate reports rather than
    // reading as a clean run.
    getRootNode() { return rawDoc; },
    assignedSlot: null,
    attributes: [],
  };
  return node;
}

const rawDoc = {
  documentElement: makeNode('html'),
  body: makeNode('body'),
  head: makeNode('head'),
  nodeType: 9,
  readyState: 'complete',
  compatMode: 'CSS1Compat',
  createElement: (t) => makeNode(t),
  createTextNode: () => makeNode('#text'),
  createEvent: () => ({ initEvent() {} }),
  querySelector: () => null,
  querySelectorAll: () => [],
  elementFromPoint: () => null,
  elementsFromPoint: () => [],
  getRootNode() { return rawDoc; },
  addEventListener: (type) => record('document.addEventListener', { type }),
  removeEventListener: (type) => record('document.removeEventListener', { type }),
  dispatchEvent: (e) => { record('document.dispatchEvent', { type: e && e.type }); return true; },
};

// Reads of the standard constructors are how the bundle captures its own
// builtins; recording them would bury the signal under noise. Writes and calls
// are never quiet, and those are the only thing this gate asserts on.
const QUIET_READS = new Set([
  'document', 'Object', 'Array', 'Map', 'Set', 'Date', 'Promise', 'RegExp',
  'Error', 'Function', 'JSON', 'Math', 'Symbol', 'String', 'Number', 'Boolean',
  'WeakMap', 'WeakSet', 'Proxy', 'Reflect', 'setTimeout', 'clearTimeout',
  'setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame',
  'requestIdleCallback', 'cancelIdleCallback', 'AbortSignal', 'AbortController',
  'Node', 'Element', 'HTMLElement', 'SVGElement', 'ShadowRoot', 'DocumentFragment',
  'Event', 'CustomEvent', 'MouseEvent', 'KeyboardEvent', 'TouchEvent',
  'PointerEvent', 'MutationObserver', 'IntersectionObserver', 'getComputedStyle',
  'NodeFilter', 'XMLSerializer', 'DOMParser', 'Range', 'Text', 'Comment', 'CSS',
  'performance', 'navigator', 'location', 'Intl', 'eval', 'parseFloat',
  'parseInt', 'isNaN', 'Infinity', 'NaN', 'undefined', 'globalThis',
  'addEventListener', 'removeEventListener', 'dispatchEvent',
  'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement',
  'HTMLOptionElement', 'HTMLFormElement', 'HTMLLabelElement', 'HTMLAnchorElement',
]);

function recordingProxy(target, label, quietReads) {
  return new Proxy(target, {
    get(t, prop, recv) {
      if (typeof prop === 'string' && !quietReads.has(prop))
        record(label + '.get', { prop });
      return Reflect.get(t, prop, recv);
    },
    set(t, prop, value) {
      record(label + '.set', { prop: String(prop) });
      return Reflect.set(t, prop, value);
    },
    defineProperty(t, prop, desc) {
      record(label + '.defineProperty', { prop: String(prop) });
      return Reflect.defineProperty(t, prop, desc);
    },
    deleteProperty(t, prop) {
      record(label + '.delete', { prop: String(prop) });
      return Reflect.deleteProperty(t, prop);
    },
  });
}

class RecordingMutationObserver {
  constructor(cb) { this.cb = cb; RecordingMutationObserver.instances.push(this); }
  observe(target, opts) {
    record('MutationObserver.observe', {
      target: target === rawDoc ? 'document' : 'other',
      childList: !!(opts && opts.childList),
    });
  }
  disconnect() { record('MutationObserver.disconnect', {}); }
  takeRecords() { return []; }
}
RecordingMutationObserver.instances = [];

// What a page does when it rewrites itself. It is the condition the removed
// detector was waiting for, and therefore the only moment its probe fired.
function replaceDocumentElement() {
  const fresh = makeNode('html');
  rawDoc.documentElement = fresh;
  for (const mo of RecordingMutationObserver.instances)
    mo.cb([{ addedNodes: [fresh] }], mo);
}

const rawWindow = {
  document: null,
  navigator: { userAgent: 'gate', platform: 'gate', maxTouchPoints: 0 },
  location: { href: 'http://127.0.0.1/gate' },
  performance: { now: () => 0 },
  MutationObserver: RecordingMutationObserver,
  CustomEvent: class CustomEvent {
    constructor(type, init) { this.type = type; Object.assign(this, init || {}); }
  },
  Event: class Event {
    constructor(type, init) { this.type = type; Object.assign(this, init || {}); }
  },
  // The constants are load bearing: `retarget` compares `node.nodeType` with
  // `Node.ELEMENT_NODE`, and without them every element reads as disconnected.
  Node: Object.assign(class Node {}, {
    ELEMENT_NODE: 1, TEXT_NODE: 3, COMMENT_NODE: 8, DOCUMENT_NODE: 9,
    DOCUMENT_FRAGMENT_NODE: 11,
  }),
  Element: class Element {}, HTMLElement: class HTMLElement {},
  ShadowRoot: class ShadowRoot {}, DocumentFragment: class DocumentFragment {},
  TouchEvent: undefined,
  getComputedStyle: () => ({
    getPropertyValue: () => '', display: 'block', visibility: 'visible',
  }),
  requestAnimationFrame: (cb) => { setTimeout(() => cb(0), 0); return 1; },
  cancelAnimationFrame: () => {},
  requestIdleCallback: (cb) => {
    setTimeout(() => cb({ timeRemaining: () => 0 }), 0); return 1;
  },
  cancelIdleCallback: () => {},
  AbortSignal: globalThis.AbortSignal,
  AbortController: globalThis.AbortController,
  setTimeout, clearTimeout, setInterval, clearInterval,
  eval: (s) => { record('window.eval', { len: String(s).length }); return undefined; },
  addEventListener: (type, fn, opts) => record('window.addEventListener', {
    type, capture: !!(opts && opts.capture),
  }),
  removeEventListener: (type, fn, opts) => record('window.removeEventListener', {
    type, capture: !!(opts && opts.capture),
  }),
  dispatchEvent: (e) => { record('window.dispatchEvent', { type: e && e.type }); return true; },
};

const doc = recordingProxy(rawDoc, 'document', new Set(['documentElement', 'nodeType']));
rawWindow.document = doc;
const win = recordingProxy(rawWindow, 'window', QUIET_READS);

// The same options injected.py passes in production. If these drift, the gate
// measures a configuration that is not the one we ship.
const OPTIONS = {
  isUnderTest: false,
  sdkLanguage: 'python',
  testIdAttributeName: 'data-testid',
  stableRafCount: 1,
  browserName: 'firefox',
  shouldPrependErrorPrefix: false,
  isUtilityWorld: true,
  customEngines: [],
};

// The DOM globals are parameters so a bare `new MutationObserver(...)` inside
// the bundle resolves onto OUR objects rather than node's. In the browser it is
// the same arrangement: the injected script's global is a sandbox built over
// the page's window.
const DOM_GLOBALS = {
  MutationObserver: RecordingMutationObserver,
  CustomEvent: rawWindow.CustomEvent,
  Event: rawWindow.Event,
  Node: rawWindow.Node,
  Element: rawWindow.Element,
  HTMLElement: rawWindow.HTMLElement,
  ShadowRoot: rawWindow.ShadowRoot,
  DocumentFragment: rawWindow.DocumentFragment,
  getComputedStyle: rawWindow.getComputedStyle,
  requestAnimationFrame: rawWindow.requestAnimationFrame,
  cancelAnimationFrame: rawWindow.cancelAnimationFrame,
  requestIdleCallback: rawWindow.requestIdleCallback,
  cancelIdleCallback: rawWindow.cancelIdleCallback,
  navigator: rawWindow.navigator,
  location: rawWindow.location,
  performance: rawWindow.performance,
};

const moduleObject = { exports: {} };
const names = ['module', 'globalThis', 'window', 'document', ...Object.keys(DOM_GLOBALS)];
const values = [moduleObject, win, win, doc, ...Object.values(DOM_GLOBALS)];
const exported = new Function(...names, source + '\nreturn module.exports;')(...values);

const phases = [];
function phase(name, fn) {
  const from = touches.length;
  let error = null;
  let value;
  try { value = fn(); } catch (e) { error = (e && e.message) || String(e); }
  phases.push({ name, error, touches: touches.slice(from) });
  return value;
}

let injected = null;
phase('construction', () => {
  injected = new (exported.InjectedScript())(win, OPTIONS);
});

// The hit-target check is the operation that used to install the listeners.
// It is a pure read now, so it must touch the page exactly as much as the
// constructor does: not at all.
phase('check_hit_target', () => {
  if (!injected) throw new Error('no injected script to check with');
  const verdict = injected.checkHitTarget(makeNode('button'), { x: 10, y: 10 });
  if (typeof verdict !== 'string')
    throw new Error('the check did not answer with a verdict: ' + verdict);
});

phase('page_replaces_documentElement', () => {
  replaceDocumentElement();
});

const out = { phases: [] };
for (const p of phases) {
  const counts = {};
  for (const t of p.touches) {
    const key = t.kind + (t.type ? ':' + t.type : '') + (t.prop ? ':' + t.prop : '');
    counts[key] = (counts[key] || 0) + 1;
  }
  // A touch that WRITES to the page, as opposed to reading a constructor off
  // it. This is the set the class assertions are about.
  const mutating = p.touches.filter((t) => /\.(addEventListener|removeEventListener|dispatchEvent|set|defineProperty|delete)$/.test(t.kind)
    || t.kind === 'MutationObserver.observe');
  out.phases.push({
    name: p.name,
    error: p.error,
    total: p.touches.length,
    mutating: mutating.length,
    added: p.touches.filter((t) => t.kind === 'window.addEventListener').map((t) => t.type).sort(),
    removed: p.touches.filter((t) => t.kind === 'window.removeEventListener').map((t) => t.type).sort(),
    dispatched: p.touches.filter((t) => t.kind === 'window.dispatchEvent' || t.kind === 'document.dispatchEvent').map((t) => t.type),
    counts,
  });
}
console.log(JSON.stringify(out, null, 1));
