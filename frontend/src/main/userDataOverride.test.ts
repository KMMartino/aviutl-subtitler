import path from "node:path";
import { describe, expect, it } from "vitest";
import { userDataOverride } from "./userDataOverride";

describe("userDataOverride", () => {
  it("does not change normal production behavior when unset or blank", () => {
    expect(userDataOverride({}, [])).toBeUndefined();
    expect(userDataOverride({ SUBUTL_USER_DATA_DIR: "  " }, [])).toBeUndefined();
  });

  it("accepts and normalizes an absolute test profile path", () => {
    const value = path.resolve("isolated", "SubUtl", "..");
    expect(userDataOverride({ SUBUTL_USER_DATA_DIR: value }, [])).toBe(path.normalize(value));
  });

  it("rejects a relative environment path", () => {
    expect(() => userDataOverride({ SUBUTL_USER_DATA_DIR: "isolated/SubUtl" }, []))
      .toThrow("SUBUTL_USER_DATA_DIR must be an absolute path");
  });

  it("uses an absolute command-line path in preference to the environment", () => {
    const value = path.resolve("isolated", "SubUtl");
    expect(userDataOverride(
      { SUBUTL_USER_DATA_DIR: path.resolve("environment") },
      ["electron.exe", `--subutl-user-data-dir=${value}`],
    )).toBe(path.normalize(value));
  });

  it("rejects empty, relative, and duplicate command-line overrides", () => {
    expect(() => userDataOverride({}, ["--subutl-user-data-dir="]))
      .toThrow("--subutl-user-data-dir requires an absolute path");
    expect(() => userDataOverride({}, ["--subutl-user-data-dir=relative/SubUtl"]))
      .toThrow("--subutl-user-data-dir must be an absolute path");
    expect(() => userDataOverride({}, [
      `--subutl-user-data-dir=${path.resolve("one")}`,
      `--subutl-user-data-dir=${path.resolve("two")}`,
    ])).toThrow("--subutl-user-data-dir may only be specified once");
  });
});
