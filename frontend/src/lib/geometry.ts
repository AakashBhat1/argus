/**
 * Map a pointer position to normalized (0..1) coordinates of content drawn
 * with `object-fit: contain` inside an element.
 *
 * `contain` letterboxes (or pillarboxes) the content whenever the element's
 * aspect ratio differs from the content's, so dividing by the element size
 * would be wrong by the width of the bars. Returns `null` when the pointer
 * is on a bar rather than on the content.
 */
export function containedPointToNormalized(
  rect: { left: number; top: number; width: number; height: number },
  contentWidth: number,
  contentHeight: number,
  clientX: number,
  clientY: number,
): { x: number; y: number } | null {
  if (rect.width <= 0 || rect.height <= 0 || contentWidth <= 0 || contentHeight <= 0) {
    return null;
  }
  const scale = Math.min(rect.width / contentWidth, rect.height / contentHeight);
  const drawnWidth = contentWidth * scale;
  const drawnHeight = contentHeight * scale;
  const offsetX = (rect.width - drawnWidth) / 2;
  const offsetY = (rect.height - drawnHeight) / 2;
  const x = (clientX - rect.left - offsetX) / drawnWidth;
  const y = (clientY - rect.top - offsetY) / drawnHeight;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { x, y };
}
