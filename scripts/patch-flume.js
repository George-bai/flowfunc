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

/**
 * Patch a flume bundle file to add getStageState getter and setStageTransform/setScale/setTranslate setters.
 * Works with both index.es.js (ES modules) and index.js (CommonJS).
 */
function patchFlumeBundle(target) {
  if (!fs.existsSync(target)) {
    console.warn('[patch-flume] Bundle not found at', target);
    return false;
  }

  let src = fs.readFileSync(target, 'utf8');

  const hasSetStageTransform = src.includes('setStageTransform');
  const hasGetStageState = src.includes('getStageState');

  if (hasSetStageTransform && hasGetStageState) {
    console.log('[patch-flume] Bundle already fully patched. Skipping:', target);
    return false;
  }

  if (hasSetStageTransform && !hasGetStageState) {
    // Upgrade case: old patch exists but missing getStageState getter
    console.log('[patch-flume] Upgrading bundle patch to add getStageState getter:', target);

    const getterInsertion = `getStageState: function getStageState() {
        return {
          scale: stageState.scale,
          translate: { x: stageState.translate.x, y: stageState.translate.y }
        };
      },
      `;

    const setStagePos = src.indexOf('setStageTransform');
    if (setStagePos === -1) {
      console.error('[patch-flume] Could not find setStageTransform for upgrade in', target);
      return false;
    }

    src = src.slice(0, setStagePos) + getterInsertion + src.slice(setStagePos);
    fs.writeFileSync(target, src, 'utf8');
    console.log('[patch-flume] Upgrade patch applied (added getStageState) to', target);
    return true;
  }

  // Fresh install: no patch exists
  // Look for getComments in the bundle (different format than source)
  const needleES = 'getComments: function getComments()';
  const needleArrow = 'getComments: () => {';

  let needle = null;
  let insertionStyle = 'function';

  if (src.includes(needleES)) {
    needle = needleES;
    insertionStyle = 'function';
  } else if (src.includes(needleArrow)) {
    needle = needleArrow;
    insertionStyle = 'arrow';
  } else {
    console.error('[patch-flume] Could not find getComments block in', target);
    return false;
  }

  const idx = src.indexOf(needle);

  // Find the closing brace of getComments function
  // For "function getComments() { return comments; }" we need to find the matching }
  let braceCount = 0;
  let foundOpen = false;
  let endIdx = idx;

  for (let i = idx; i < src.length; i++) {
    if (src[i] === '{') {
      braceCount++;
      foundOpen = true;
    } else if (src[i] === '}') {
      braceCount--;
      if (foundOpen && braceCount === 0) {
        endIdx = i;
        break;
      }
    }
  }

  if (endIdx === idx) {
    console.error('[patch-flume] Could not find end of getComments function in', target);
    return false;
  }

  // Build insertion based on style
  let insertion;
  if (insertionStyle === 'function') {
    insertion = `,
      // Expose stage getters and setters for external control (e.g., fit-to-view, view persistence)
      getStageState: function getStageState() {
        return {
          scale: stageState.scale,
          translate: { x: stageState.translate.x, y: stageState.translate.y }
        };
      },
      setStageTransform: function setStageTransform(_ref) {
        var scale = _ref.scale, translate = _ref.translate;
        var s = clamp(typeof scale === "number" ? scale : stageState.scale, 0.1, 7);
        var t = {
          x: typeof (translate === null || translate === void 0 ? void 0 : translate.x) === "number" ? translate.x : stageState.translate.x,
          y: typeof (translate === null || translate === void 0 ? void 0 : translate.y) === "number" ? translate.y : stageState.translate.y
        };
        dispatchStageState({ type: "SET_TRANSLATE_SCALE", scale: s, translate: t });
      },
      setScale: function setScale(scale) {
        var s = clamp(typeof scale === "number" ? scale : stageState.scale, 0.1, 7);
        dispatchStageState({ type: "SET_SCALE", scale: s });
      },
      setTranslate: function setTranslate(translate) {
        var t = {
          x: typeof (translate === null || translate === void 0 ? void 0 : translate.x) === "number" ? translate.x : stageState.translate.x,
          y: typeof (translate === null || translate === void 0 ? void 0 : translate.y) === "number" ? translate.y : stageState.translate.y
        };
        dispatchStageState({ type: "SET_TRANSLATE", translate: t });
      }`;
  } else {
    insertion = `,
        // Expose stage getters and setters for external control (e.g., fit-to-view, view persistence)
        getStageState: () => ({
            scale: stageState.scale,
            translate: { x: stageState.translate.x, y: stageState.translate.y }
        }),
        setStageTransform: ({ scale, translate }) => {
            const s = clamp(typeof scale === "number" ? scale : stageState.scale, 0.1, 7);
            const t = {
                x: typeof (translate?.x) === "number" ? translate.x : stageState.translate.x,
                y: typeof (translate?.y) === "number" ? translate.y : stageState.translate.y
            };
            dispatchStageState({ type: "SET_TRANSLATE_SCALE", scale: s, translate: t });
        },
        setScale: (scale) => {
            const s = clamp(typeof scale === "number" ? scale : stageState.scale, 0.1, 7);
            dispatchStageState({ type: "SET_SCALE", scale: s });
        },
        setTranslate: (translate) => {
            const t = {
                x: typeof (translate?.x) === "number" ? translate.x : stageState.translate.x,
                y: typeof (translate?.y) === "number" ? translate.y : stageState.translate.y
            };
            dispatchStageState({ type: "SET_TRANSLATE", translate: t });
        }`;
  }

  const beforeText = src.slice(0, endIdx + 1);
  const afterText = src.slice(endIdx + 1);
  const next = beforeText + insertion + afterText;
  fs.writeFileSync(target, next, 'utf8');
  console.log('[patch-flume] Full patch applied to', target);
  return true;
}

function patchFlume() {
  const distDir = path.join(process.cwd(), 'node_modules', 'flume', 'dist');

  if (!fs.existsSync(distDir)) {
    console.error('[patch-flume] flume dist directory not found at', distDir);
    process.exit(1);
  }

  let didPatch = false;

  // Patch the ES module bundle (used by webpack with module field)
  const indexEs = path.join(distDir, 'index.es.js');
  if (fs.existsSync(indexEs)) {
    didPatch = patchFlumeBundle(indexEs) || didPatch;
  }

  // Patch the CommonJS bundle (fallback)
  const indexJs = path.join(distDir, 'index.js');
  if (fs.existsSync(indexJs)) {
    didPatch = patchFlumeBundle(indexJs) || didPatch;
  }

  // Also patch NodeEditor.js for completeness (in case someone imports it directly)
  const nodeEditor = path.join(distDir, 'NodeEditor.js');
  if (fs.existsSync(nodeEditor)) {
    didPatch = patchFlumeBundle(nodeEditor) || didPatch;
  }

  // Patch context menu filter
  didPatch = patchContextMenuFilter(indexEs) || didPatch;
  didPatch = patchContextMenuFilter(indexJs) || didPatch;
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
