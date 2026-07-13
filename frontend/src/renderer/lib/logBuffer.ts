export const DEFAULT_LOG_LIMIT = 512 * 1024;

export class LogBuffer {
  private chunks: string[] = [];
  private head = 0;
  private length = 0;

  constructor(private readonly limit = DEFAULT_LOG_LIMIT) {
    if (!Number.isFinite(limit) || limit < 1) throw new Error("Log limit must be positive");
  }

  append(text: string): void {
    if (!text) return;
    this.chunks.push(text);
    this.length += text.length;
    this.trim();
  }

  replace(text: string): void {
    this.chunks = text ? [text] : [];
    this.head = 0;
    this.length = text.length;
    this.trim();
  }

  clear(): void {
    this.chunks = [];
    this.head = 0;
    this.length = 0;
  }

  value(): string {
    return this.chunks.slice(this.head).join("");
  }

  get size(): number {
    return this.length;
  }

  private trim(): void {
    let excess = this.length - this.limit;
    while (excess > 0 && this.head < this.chunks.length) {
      const first = this.chunks[this.head];
      if (first.length <= excess) {
        this.head += 1;
        this.length -= first.length;
        excess -= first.length;
      } else {
        this.chunks[this.head] = first.slice(excess);
        this.length -= excess;
        excess = 0;
      }
    }
    if (this.head > 1024 && this.head * 2 > this.chunks.length) {
      this.chunks = this.chunks.slice(this.head);
      this.head = 0;
    }
  }
}
