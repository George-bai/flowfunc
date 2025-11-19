import { useEffect, useRef } from 'react';

/**
 * Minimal port highlighting hook for FlowFunc
 */
export const usePortHighlighter = (config, nodes, typeSafety, editorRef) => {
  const isDraggingRef = useRef(false);
  const draggedPortRef = useRef(null);
  const highlightedPortRef = useRef(null);

  // Check if two ports are compatible
  const isCompatible = (sourceType, targetType, sourceIsInput, targetIsInput) => {
    if (sourceIsInput === targetIsInput) return false;
    if (!typeSafety) return true;
    if (sourceType === 'object' || targetType === 'object') return true;
    return sourceType === targetType;
  };

  // Apply/remove highlight
  const highlightPort = (element) => {
    element.classList.add('port-highlighting');
    highlightedPortRef.current = element;
  };
  
  const unhighlightPort = (element) => {
    element.classList.remove('port-highlighting');
    if (highlightedPortRef.current === element) {
      highlightedPortRef.current = null;
    }
  };

  // Find closest compatible port
  const findClosestPort = (mouseX, mouseY) => {
    if (!editorRef.current || !isDraggingRef.current || !draggedPortRef.current) return null;

    const ports = editorRef.current.querySelectorAll('[data-port-name][data-port-transput-type]');
    let closest = null;
    let minDistance = 72; // 3 * 24px port diameter

    ports.forEach(port => {
      // Avoid suggesting the exact same port we started dragging from
      if (draggedPortRef.current.element && port === draggedPortRef.current.element) {
        return;
      }

      const rect = port.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const distance = Math.sqrt((mouseX - centerX) ** 2 + (mouseY - centerY) ** 2);

      if (distance < minDistance) {
        const portType = port.getAttribute('data-port-type') || 'any';
        const isInput = port.getAttribute('data-port-transput-type') === 'input';

        if (isCompatible(draggedPortRef.current.type, portType, draggedPortRef.current.isInput, isInput)) {
          minDistance = distance;
          closest = port;
        }
      }
    });

    return closest;
  };

  useEffect(() => {
    if (!editorRef.current) return;

    const handleMouseDown = (e) => {
      const port = e.target.closest('[data-port-name][data-port-transput-type]');
      if (!port) return;

      draggedPortRef.current = {
        element: port,
        type: port.getAttribute('data-port-type') || 'any',
        isInput: port.getAttribute('data-port-transput-type') === 'input'
      };
      isDraggingRef.current = true;
    };

    const handleMouseMove = (e) => {
      if (!isDraggingRef.current) return;
      
      // Clear previous highlight
      if (highlightedPortRef.current) {
        unhighlightPort(highlightedPortRef.current);
      }
      
      // Find and highlight closest port
      const closest = findClosestPort(e.clientX, e.clientY);
      if (closest) {
        highlightPort(closest);
      }
    };

    const handleMouseUp = (e) => {
      // Prevent infinite recursion from auto-dispatched events
      if (e.isTrusted === false && e.type === 'mouseup') {
        // Reset state for auto-dispatched events
        isDraggingRef.current = false;
        draggedPortRef.current = null;
        if (highlightedPortRef.current) {
          unhighlightPort(highlightedPortRef.current);
        }
        return;
      }
      
      if (isDraggingRef.current && highlightedPortRef.current) {
        // Auto-connect by dispatching mouseup on highlighted port
        const rect = highlightedPortRef.current.getBoundingClientRect();
        const event = new MouseEvent('mouseup', {
          clientX: rect.left + rect.width / 2,
          clientY: rect.top + rect.height / 2,
          bubbles: true,
          // Mark as auto-dispatched to prevent recursion
          isTrusted: false
        });
        highlightedPortRef.current.dispatchEvent(event);
      }
      
      // Reset state
      isDraggingRef.current = false;
      draggedPortRef.current = null;
      if (highlightedPortRef.current) {
        unhighlightPort(highlightedPortRef.current);
      }
    };

    // Add event listeners
    editorRef.current.addEventListener('mousedown', handleMouseDown, true);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      if (editorRef.current) {
        editorRef.current.removeEventListener('mousedown', handleMouseDown, true);
      }
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [config, typeSafety]);
};