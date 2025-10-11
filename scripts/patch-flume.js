const fs = require('fs');
const path = require('path');

function patchFlume() {
  const target = path.join(process.cwd(), 'node_modules', 'flume', 'dist', 'NodeEditor.js');
  if (!fs.existsSync(target)) {
    console.error('[patch-flume] NodeEditor.js not found at', target);
    process.exit(1);
  }
  let src = fs.readFileSync(target, 'utf8');
  if (src.includes('setStageTransform:')) {
    console.log('[patch-flume] Already patched. Skipping.');
    return;
  }

  const needle = 'getComments: () => {';
  const idx = src.indexOf(needle);
  if (idx === -1) {
    console.error('[patch-flume] Could not find getComments block. Aborting.');
    process.exit(1);
  }

  const after = src.indexOf('}', idx); // end of return comments function block
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
}

if (require.main === module) {
  patchFlume();
}

module.exports = { patchFlume };

