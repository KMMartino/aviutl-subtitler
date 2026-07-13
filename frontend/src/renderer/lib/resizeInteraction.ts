export function clampResize(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function resizeFromKey(
  value: number,
  key: string,
  orientation: "vertical" | "horizontal",
  minimum: number,
  maximum: number,
  largeStep = false,
): number | null {
  const step = largeStep ? 5 : 1;
  if (key === "Home") return minimum;
  if (key === "End") return maximum;
  if (orientation === "vertical") {
    if (key === "ArrowLeft") return clampResize(value - step, minimum, maximum);
    if (key === "ArrowRight") return clampResize(value + step, minimum, maximum);
  } else {
    if (key === "ArrowUp") return clampResize(value + step, minimum, maximum);
    if (key === "ArrowDown") return clampResize(value - step, minimum, maximum);
  }
  return null;
}
