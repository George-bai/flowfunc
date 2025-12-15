const fs = require('fs');
const path = require('path');

function patchContextMenuFilter(target) {
  if (!fs.existsSync(target)) {
    console.warn('[patch-flume] ContextMenu target not found at', target);
    return false;
  }

  const alreadyNeedle = '(opt.description || "").toLowerCase().includes(lowerFilter)';
  const src = fs.readFileSync(target, 'utf8');
  if (src.includes(alreadyNeedle)) {
    console.log('[patch-flume] ContextMenu filter already patched. Skipping:', target);
    return false;
  }

  const needleStandalone = 'return options.filter(opt => opt.label.toLowerCase().includes(lowerFilter));';
  const replacementStandalone =
    'return options.filter(opt => opt.label.toLowerCase().includes(lowerFilter) || (opt.description || "").toLowerCase().includes(lowerFilter));';

  const needleBundled = 'return opt.label.toLowerCase().includes(lowerFilter);';
  const replacementBundled =
    'return opt.label.toLowerCase().includes(lowerFilter) || (opt.description || "").toLowerCase().includes(lowerFilter);';

  let next = src;
  if (next.includes(needleStandalone)) {
    next = next.replace(needleStandalone, replacementStandalone);
  } else if (next.includes(needleBundled)) {
    next = next.replace(needleBundled, replacementBundled);
  } else {
    console.error('[patch-flume] Could not find ContextMenu filter needle in', target);
    return false;
  }

  fs.writeFileSync(target, next, 'utf8');
  console.log('[patch-flume] ContextMenu filter patched in', target);
  return true;
}

function patchFlume() {
  const distDir = path.join(process.cwd(), 'node_modules', 'flume', 'dist');
  const target = path.join(distDir, 'NodeEditor.js');
  if (!fs.existsSync(target)) {
    console.error('[patch-flume] NodeEditor.js not found at', target);
    process.exit(1);
  }

  let didPatch = false;
  const src = fs.readFileSync(target, 'utf8');
  if (src.includes('setStageTransform:')) {
    console.log('[patch-flume] NodeEditor already patched. Skipping:', target);
  } else {
    const needle = 'getComments: () => {';
    const idx = src.indexOf(needle);
    if (idx === -1) {
      console.error('[patch-flume] Could not find getComments block. Aborting.');
      process.exit(1);
    }

    // end of return comments function block
    const after = src.indexOf('}', idx);
    if (after === -1) {
      console.error('[patch-flume] Could not determine insertion point. Aborting.');
      process.exit(1);
    }

    const insertion = ",\n        // Expose stage setters for external control (e.g., fit-to-view)\n        setStageTransform: ({ scale, translate }) => {\n            const s = clamp(typeof scale === \"number\" ? scale : stageState.scale, 0.1, 7);\n            const t = {\n                x: typeof (translate?.x) === \"number\" ? translate.x : stageState.translate.x,\n                y: typeof (translate?.y) === \"number\" ? translate.y : stageState.translate.y\n            };\n            dispatchStageState({ type: \"SET_TRANSLATE_SCALE\", scale: s, translate: t });\n        },\n        setScale: (scale) => {\n            const s = clamp(typeof scale === \"number\" ? scale : stageState.scale, 0.1, 7);\n            dispatchStageState({ type: \"SET_SCALE\", scale: s });\n        },\n        setTranslate: (translate) => {\n            const t = {\n                x: typeof (translate?.x) === \"number\" ? translate.x : stageState.translate.x,\n                y: typeof (translate?.y) === \"number\" ? translate.y : stageState.translate.y\n            };\n            dispatchStageState({ type: \"SET_TRANSLATE\", translate: t });\n        }";

    const beforeText = src.slice(0, after + 1);
    const afterText = src.slice(after + 1);
    const next = beforeText + insertion + afterText;
    fs.writeFileSync(target, next, 'utf8');
    console.log('[patch-flume] Patch applied to', target);
    didPatch = true;
  }

  didPatch = patchContextMenuFilter(path.join(distDir, 'index.es.js')) || didPatch;
  didPatch = patchContextMenuFilter(path.join(distDir, 'index.js')) || didPatch;
  didPatch =
    patchContextMenuFilter(path.join(distDir, 'components', 'ContextMenu', 'ContextMenu.js')) ||
    didPatch;

  if (!didPatch) {
    console.log('[patch-flume] No changes required.');
  }
}

if (require.main === module) {
  patchFlume();
}

module.exports = { patchFlume };

