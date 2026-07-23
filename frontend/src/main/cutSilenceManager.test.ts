import { describe, expect, it } from "vitest";
import { CUT_SILENCE_PRESETS } from "./cutSilenceManager";

describe("Cut silence encoder presets", () => {
  it("ships explicit AMD, NVIDIA, Intel, and CPU quality mappings", () => {
    expect(CUT_SILENCE_PRESETS.map((preset) => preset.id)).toEqual([
      "hevc-amf-cqp21", "hevc-nvenc-qp21", "hevc-qsv-q21", "libx265-crf21",
    ]);
    expect(CUT_SILENCE_PRESETS[0].args).toEqual(expect.arrayContaining(["-c:v", "hevc_amf", "-rc", "cqp", "-qp_i", "21"]));
    expect(CUT_SILENCE_PRESETS[1].args).toEqual(expect.arrayContaining(["-c:v", "hevc_nvenc", "-preset", "p7", "-qp", "21"]));
    expect(CUT_SILENCE_PRESETS[2].args).toEqual(expect.arrayContaining(["-c:v", "hevc_qsv", "-global_quality", "21"]));
    expect(CUT_SILENCE_PRESETS[3].args).toEqual(expect.arrayContaining(["-c:v", "libx265", "-crf", "21"]));
  });
});
