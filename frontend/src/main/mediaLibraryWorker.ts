import { parentPort, workerData } from "node:worker_threads";
import type { MediaAnalysisDetail, MediaAnalysisScope, MediaAssetKind, MediaAssetListRequest, MediaLibraryRootKind, MediaLibraryRootPurpose } from "../renderer/lib/types";
import { MediaLibraryDatabase, type IndexedMediaFile } from "./mediaLibraryDatabase";

type Request = {
  id: number;
  method: string;
  args: unknown[];
};

type WorkerConfiguration = {
  databasePath: string;
};

const port = parentPort;
if (!port) throw new Error("Media library worker requires a parent port.");
const database = new MediaLibraryDatabase((workerData as WorkerConfiguration).databasePath);

port.on("message", (request: Request) => {
  try {
    const result = dispatch(request.method, request.args);
    port.postMessage({ id: request.id, result });
  } catch (error) {
    port.postMessage({
      id: request.id,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

port.on("close", () => database.close());

function dispatch(method: string, args: unknown[]): unknown {
  switch (method) {
    case "listRoots":
      return database.listRoots();
    case "addRoot":
      return database.addRoot(
        String(args[0]),
        args[1] as MediaLibraryRootKind,
        Boolean(args[2]),
        args[3] as MediaLibraryRootPurpose,
      );
    case "setRootEnabled":
      return database.setRootEnabled(String(args[0]), Boolean(args[1]));
    case "removeRoot":
      return database.removeRoot(String(args[0]));
    case "listDirectoryScopes":
      return database.listDirectoryScopes(String(args[0]));
    case "setDirectoryIncluded":
      return database.setDirectoryIncluded(String(args[0]), String(args[1]), Boolean(args[2]));
    case "directoryTrackedCounts":
      return database.directoryTrackedCounts(String(args[0]));
    case "directoryVisibility":
      return database.directoryVisibility(String(args[0]));
    case "directoryHiddenStates":
      return database.directoryHiddenStates(String(args[0]));
    case "setDirectoryVisible":
      return database.setDirectoryVisible(
        String(args[0]),
        String(args[1]),
        args[2] as "subtree" | "direct",
        Boolean(args[3]),
      );
    case "setDirectoryHidden":
      return database.setDirectoryHidden(String(args[0]), String(args[1]), Boolean(args[2]));
    case "removeDirectoryAssets":
      return database.removeDirectoryAssets(String(args[0]), String(args[1]));
    case "beginScan":
      return database.beginScan(String(args[0]));
    case "upsertIndexedFile":
      return database.upsertIndexedFile(String(args[0]), String(args[1]), args[2] as IndexedMediaFile);
    case "finishScan":
      return database.finishScan(
        String(args[0]),
        String(args[1]),
        args[2] as string[],
        String(args[3] ?? ""),
      );
    case "listAssets":
      return database.listAssets((args[0] ?? {}) as MediaAssetListRequest);
    case "listAnalysisCandidates":
      return database.listAnalysisCandidates(args[0] as MediaAssetKind | undefined);
    case "getAsset":
      return database.getAsset(String(args[0]));
    case "updateUserDescription":
      return database.updateUserDescription(String(args[0]), String(args[1]));
    case "addUserSegment":
      return database.addUserSegment(String(args[0]), args[1] as MediaAnalysisScope, String(args[2]));
    case "updateProvenance":
      return database.updateProvenance(
        String(args[0]),
        String(args[1]),
        String(args[2]),
        String(args[3]),
        String(args[4]),
        String(args[5]),
      );
    case "startAnalysis":
      return database.startAnalysis(
        String(args[0]),
        String(args[1]),
        String(args[2]),
        String(args[3]),
        Number(args[4]),
        args[5] as MediaAnalysisDetail,
        args[6] as MediaAnalysisScope | undefined,
      );
    case "completeAnalysis":
      return database.completeAnalysis(
        String(args[0]),
        String(args[1]),
        args[2] as never,
        args[3] as MediaAnalysisScope | undefined,
      );
    case "failAnalysis":
      return database.failAnalysis(String(args[0]), String(args[1]), String(args[2]));
    case "close":
      database.close();
      return null;
    default:
      throw new Error(`Unknown media library worker method: ${method}`);
  }
}
