export class CoalescedWriter<T> {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private latest: T | undefined;
  private waiters: Array<{ resolve(): void; reject(error: unknown): void }> = [];
  private tail = Promise.resolve();

  constructor(private readonly write: (value: T) => void | Promise<void>, private readonly delayMs = 150) {}

  enqueue(value: T): Promise<void> {
    this.latest = value;
    if (this.timer) clearTimeout(this.timer);
    const result = new Promise<void>((resolve, reject) => this.waiters.push({ resolve, reject }));
    this.timer = setTimeout(() => this.flush(), this.delayMs);
    return result;
  }

  flushNow(): Promise<void> {
    if (this.timer) clearTimeout(this.timer);
    this.flush();
    return this.tail;
  }

  private flush(): void {
    this.timer = undefined;
    const value = this.latest;
    const waiters = this.waiters.splice(0);
    this.latest = undefined;
    if (value === undefined) return;
    const operation = this.tail.then(() => this.write(value));
    this.tail = operation.catch(() => undefined);
    void operation.then(
      () => waiters.forEach(({ resolve }) => resolve()),
      (error) => waiters.forEach(({ reject }) => reject(error)),
    );
  }
}
