import { describe, expect, it, vi } from "vitest";
import { CoalescedWriter } from "./coalescedWriter";

describe("CoalescedWriter", () => {
  it("coalesces rapid changes and resolves every caller after the latest write", async () => {
    vi.useFakeTimers();
    const writes: number[] = [];
    const writer = new CoalescedWriter<number>((value) => writes.push(value), 20);
    const first = writer.enqueue(1);
    const second = writer.enqueue(2);
    await vi.advanceTimersByTimeAsync(20);
    await Promise.all([first, second]);
    expect(writes).toEqual([2]);
    vi.useRealTimers();
  });

  it("surfaces write failures to every coalesced caller", async () => {
    vi.useFakeTimers();
    const writer = new CoalescedWriter<number>(() => { throw new Error("disk full"); }, 20);
    const pending = writer.enqueue(1);
    const assertion = expect(pending).rejects.toThrow("disk full");
    await vi.advanceTimersByTimeAsync(20);
    await assertion;
    vi.useRealTimers();
  });

  it("serializes batches so a slow older write cannot finish after a newer write", async () => {
    vi.useFakeTimers();
    let release!: () => void;
    const writes: number[] = [];
    const writer = new CoalescedWriter<number>(async (value) => {
      if (value === 1) await new Promise<void>((resolve) => { release = resolve; });
      writes.push(value);
    }, 10);
    const first = writer.enqueue(1);
    await vi.advanceTimersByTimeAsync(10);
    const second = writer.enqueue(2);
    await vi.advanceTimersByTimeAsync(10);
    expect(writes).toEqual([]);
    release();
    await Promise.all([first, second]);
    expect(writes).toEqual([1, 2]);
    vi.useRealTimers();
  });

  it("flushes the latest pending value for application shutdown", async () => {
    vi.useFakeTimers();
    const writes: number[] = [];
    const writer = new CoalescedWriter<number>((value) => writes.push(value), 10_000);
    const pending = writer.enqueue(3);
    await writer.flushNow();
    await pending;
    expect(writes).toEqual([3]);
    vi.useRealTimers();
  });
});
