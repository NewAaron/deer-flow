/** Radix's light-DOM candidate walker cannot see a packaged Shadow DOM UI. */
export function pickerTabStops(root: HTMLElement): HTMLElement[] {
  const stops: HTMLElement[] = [];
  function visit(element: Element) {
    if (!(element instanceof HTMLElement)) return;
    const style = getComputedStyle(element);
    if (
      element.hidden ||
      element.inert ||
      style.display === "none" ||
      style.visibility === "hidden"
    )
      return;
    if (
      element.tabIndex >= 0 &&
      !element.matches(":disabled") &&
      element.getClientRects().length > 0
    )
      stops.push(element);
    const children = element.shadowRoot?.children ?? element.children;
    for (const child of children) visit(child);
  }
  for (const child of root.children) visit(child);
  return stops;
}

export function deepActiveElement(): Element | null {
  let active = document.activeElement;
  while (active?.shadowRoot?.activeElement)
    active = active.shadowRoot.activeElement;
  return active;
}
