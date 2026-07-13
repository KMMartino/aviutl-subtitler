export function keyboardReorder<T>(items: readonly T[], index: number, key: string): T[] | null {
  if (index < 0 || index >= items.length) return null;
  const target = key === "ArrowLeft" || key === "ArrowUp"
    ? index - 1
    : key === "ArrowRight" || key === "ArrowDown"
      ? index + 1
      : key === "Home"
        ? 0
        : key === "End"
          ? items.length - 1
          : index;
  if (target === index || target < 0 || target >= items.length) return null;
  const next = [...items];
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  return next;
}
