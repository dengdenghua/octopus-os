export type InspectPayload = {
  selector: string;
  tagName: string;
  outerHTML: string;
  textContent: string;
  rect: { x: number; y: number; w: number; h: number };
};

export type InspectSelected = InspectPayload & {
  filepath: string;
  capturedAt: number;
  threadId?: string;
};

const EVT = "echo:inspect-selected";

export function dispatchInspectSelected(
  detail: Omit<InspectSelected, "capturedAt">,
) {
  const enriched: InspectSelected = { ...detail, capturedAt: Date.now() };
  window.dispatchEvent(
    new CustomEvent<InspectSelected>(EVT, { detail: enriched }),
  );
}

export function onInspectSelected(
  handler: (data: InspectSelected) => void,
  threadId?: string,
): () => void {
  const wrapped = (e: Event) => {
    const ce = e as CustomEvent<InspectSelected>;
    if (!ce.detail) return;
    if (threadId && ce.detail.threadId && ce.detail.threadId !== threadId)
      return;
    handler(ce.detail);
  };
  window.addEventListener(EVT, wrapped);
  return () => window.removeEventListener(EVT, wrapped);
}
