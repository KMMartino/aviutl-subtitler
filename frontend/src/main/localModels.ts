import fs from "node:fs";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import type { LocalModelProfile, LocalModelStatus } from "../renderer/lib/types";

type ModelFile = { repo: string; filename: string; sourcePath?: string; folder: string; bytes: number };
type ProfileDefinition = LocalModelProfile & {
  files: {
    transcription: ModelFile;
    projector: ModelFile;
    cleanup: ModelFile;
    transcriptionDraft?: ModelFile;
    cleanupDraft?: ModelFile;
  };
};

const e2bRepo = "unsloth/gemma-4-E2B-it-GGUF";
const e4bRepo = "unsloth/gemma-4-E4B-it-GGUF";
const b12Repo = "unsloth/gemma-4-12b-it-GGUF";

export const LOCAL_PROFILES: ProfileDefinition[] = [
  {
    id: "8gb-gpu-gemma",
    label: "8 GB GPU Profile (Gemma)",
    vramGb: 8,
    summary: "E2B Q5 transcription and E2B Q6 cleanup",
    downloadBytes: 3356035200 + 985654080 + 4501719168,
    cleanupWindowSubtitles: 10,
    experimental: false,
    files: {
      transcription: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q5_K_M.gguf", folder: "gemma-4-e2b", bytes: 3356035200 },
      projector: { repo: e2bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e2b", bytes: 985654080 },
      cleanup: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q6_K.gguf", folder: "gemma-4-e2b", bytes: 4501719168 }
    }
  },
  {
    id: "12gb-gpu-gemma",
    label: "12 GB GPU Profile (Gemma)",
    vramGb: 12,
    summary: "E4B Q6 transcription and 12B Q5 cleanup",
    downloadBytes: 7074927776 + 990372672 + 8413574560,
    cleanupWindowSubtitles: 20,
    experimental: false,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-Q5_K_M.gguf", folder: "gemma-4-12b", bytes: 8413574560 }
    }
  },
  {
    id: "16gb-gpu-gemma",
    label: "16 GB GPU Profile (Gemma)",
    vramGb: 16,
    summary: "E4B Q6 transcription and 12B Q6 cleanup",
    downloadBytes: 7074927776 + 990372672 + 10685011360,
    cleanupWindowSubtitles: 32,
    experimental: false,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-UD-Q6_K_XL.gguf", folder: "gemma-4-12b", bytes: 10685011360 }
    }
  },
  {
    id: "8gb-gpu-gemma-mtp",
    label: "8 GB GPU Profile (Gemma MTP)",
    vramGb: 8,
    summary: "Experimental E2B profile with multi-token prediction",
    downloadBytes: 3356035200 + 985654080 + 4501719168 + 97817664,
    cleanupWindowSubtitles: 10,
    experimental: true,
    files: {
      transcription: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q5_K_M.gguf", folder: "gemma-4-e2b", bytes: 3356035200 },
      projector: { repo: e2bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e2b", bytes: 985654080 },
      cleanup: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q6_K.gguf", folder: "gemma-4-e2b", bytes: 4501719168 },
      transcriptionDraft: { repo: e2bRepo, sourcePath: "MTP/gemma-4-E2B-it-Q8_0-MTP.gguf", filename: "gemma-4-E2B-it-Q8_0-MTP.gguf", folder: "gemma-4-e2b/mtp", bytes: 97817664 },
      cleanupDraft: { repo: e2bRepo, sourcePath: "MTP/gemma-4-E2B-it-Q8_0-MTP.gguf", filename: "gemma-4-E2B-it-Q8_0-MTP.gguf", folder: "gemma-4-e2b/mtp", bytes: 97817664 }
    }
  },
  {
    id: "12gb-gpu-gemma-mtp",
    label: "12 GB GPU Profile (Gemma MTP)",
    vramGb: 12,
    summary: "Experimental E4B/12B profile with multi-token prediction",
    downloadBytes: 7074927776 + 990372672 + 8413574560 + 98653248 + 465109248,
    cleanupWindowSubtitles: 20,
    experimental: true,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-Q5_K_M.gguf", folder: "gemma-4-12b", bytes: 8413574560 },
      transcriptionDraft: { repo: e4bRepo, sourcePath: "MTP/gemma-4-E4B-it-Q8_0-MTP.gguf", filename: "gemma-4-E4B-it-Q8_0-MTP.gguf", folder: "gemma-4-e4b/mtp", bytes: 98653248 },
      cleanupDraft: { repo: b12Repo, sourcePath: "MTP/gemma-4-12b-it-Q8_0-MTP.gguf", filename: "gemma-4-12b-it-Q8_0-MTP.gguf", folder: "gemma-4-12b/mtp", bytes: 465109248 }
    }
  },
  {
    id: "16gb-gpu-gemma-mtp",
    label: "16 GB GPU Profile (Gemma MTP)",
    vramGb: 16,
    summary: "Experimental E4B/12B profile with multi-token prediction",
    downloadBytes: 7074927776 + 990372672 + 10685011360 + 98653248 + 465109248,
    cleanupWindowSubtitles: 32,
    experimental: true,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-UD-Q6_K_XL.gguf", folder: "gemma-4-12b", bytes: 10685011360 },
      transcriptionDraft: { repo: e4bRepo, sourcePath: "MTP/gemma-4-E4B-it-Q8_0-MTP.gguf", filename: "gemma-4-E4B-it-Q8_0-MTP.gguf", folder: "gemma-4-e4b/mtp", bytes: 98653248 },
      cleanupDraft: { repo: b12Repo, sourcePath: "MTP/gemma-4-12b-it-Q8_0-MTP.gguf", filename: "gemma-4-12b-it-Q8_0-MTP.gguf", folder: "gemma-4-12b/mtp", bytes: 465109248 }
    }
  }
];

let downloading = false;

export function listLocalProfiles(): LocalModelProfile[] {
  return LOCAL_PROFILES.map(({ files: _files, ...profile }) => profile);
}

export function localModelStatus(modelsDirectory: string, profileId: string): LocalModelStatus {
  const profile = getProfile(profileId);
  const paths = localModelPaths(modelsDirectory, profileId);
  return {
    profile: profile.id,
    installed: Object.values(paths).filter(Boolean).every((file) => fs.existsSync(file)),
    downloading,
    files: {
      transcription: { path: paths.transcription, exists: fs.existsSync(paths.transcription) },
      projector: { path: paths.projector, exists: fs.existsSync(paths.projector) },
      cleanup: { path: paths.cleanup, exists: fs.existsSync(paths.cleanup) }
      ,
      ...(paths.transcriptionDraft ? { transcriptionDraft: { path: paths.transcriptionDraft, exists: fs.existsSync(paths.transcriptionDraft) } } : {}),
      ...(paths.cleanupDraft ? { cleanupDraft: { path: paths.cleanupDraft, exists: fs.existsSync(paths.cleanupDraft) } } : {})
    }
  };
}

export async function downloadLocalProfile(modelsDirectory: string, profileId: string, onLog: (text: string) => void = () => undefined): Promise<LocalModelStatus> {
  if (downloading) throw new Error("A local model download is already running");
  downloading = true;
  try {
    const profile = getProfile(profileId);
    for (const file of Object.values(profile.files)) {
      const target = path.join(modelsDirectory, file.folder, file.filename);
      if (fs.existsSync(target)) {
        onLog(`[huggingface] exists, skipping ${file.filename}\n`);
        continue;
      }
      fs.mkdirSync(path.dirname(target), { recursive: true });
      const temporary = `${target}.part`;
      const sourcePath = (file.sourcePath ?? file.filename).split("/").map(encodeURIComponent).join("/");
      const url = `https://huggingface.co/${file.repo}/resolve/main/${sourcePath}?download=true`;
      onLog(`[huggingface] downloading ${file.repo}/${sourcePath}\n`);
      const response = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(30 * 60 * 1000) });
      if (!response.ok || !response.body) throw new Error(`Download failed for ${file.filename}: HTTP ${response.status}`);
      await pipeline(Readable.fromWeb(response.body as never), fs.createWriteStream(temporary));
      fs.renameSync(temporary, target);
      onLog(`[huggingface] saved ${target}\n`);
    }
    onLog("[huggingface] model profile download complete\n");
    return localModelStatus(modelsDirectory, profileId);
  } finally {
    downloading = false;
  }
}

export function localModelPaths(modelsDirectory: string, profileId: string) {
  const profile = getProfile(profileId);
  const entry = (key: keyof ProfileDefinition["files"]) => {
    const file = profile.files[key];
    if (!file) return "";
    return path.join(modelsDirectory, file.folder, file.filename);
  };
  return {
    transcription: entry("transcription"),
    projector: entry("projector"),
    cleanup: entry("cleanup"),
    transcriptionDraft: profile.files.transcriptionDraft ? entry("transcriptionDraft") : "",
    cleanupDraft: profile.files.cleanupDraft ? entry("cleanupDraft") : ""
  };
}

function getProfile(profileId: string): ProfileDefinition {
  const profile = LOCAL_PROFILES.find((item) => item.id === profileId);
  if (!profile) throw new Error(`Unknown local model profile: ${profileId}`);
  return profile;
}
