import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type MouseEvent as ReactMouseEvent, type PointerEvent } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Download,
  FileBox,
  Film,
  Folder,
  FolderPlus,
  Globe,
  Image,
  Layers,
  Save,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import type {
  MediaAnalysisDetail,
  MediaAssetAnalysisEstimate,
  MediaAssetAvailability,
  MediaAssetDetail,
  MediaAssetKind,
  MediaAssetSummary,
  MediaLibraryRoot,
  MediaLibraryDirectory,
  WebAssetProbe,
} from "../lib/types";
import { useI18n } from "../i18n";

const PAGE_SIZE = 50;

type AnalysisDialog = {
  mode: "single" | "bulk";
  mediaKind: MediaAssetKind | "mixed";
  title: string;
  assetIds: string[];
  estimates: MediaAssetAnalysisEstimate[];
  bulkEstimates?: Array<{
    detail: MediaAnalysisDetail;
    recommendedAssetCount: number;
    assetCount: number;
    sampleCount: number;
    estimatedCostUsd: number;
  }>;
  privacyNotice: string;
};

export default function MediaLibraryScreen() {
  const { locale, t } = useI18n();
  const libraryView = useRef<HTMLElement>(null);
  const [leftPanelWidth, setLeftPanelWidth] = useState(25);
  const [rightPanelWidth, setRightPanelWidth] = useState(28);
  const [roots, setRoots] = useState<MediaLibraryRoot[]>([]);
  const [directoriesByRoot, setDirectoriesByRoot] = useState<Record<string, MediaLibraryDirectory[]>>({});
  const [expandedDirectories, setExpandedDirectories] = useState<Set<string>>(new Set());
  const [directoryMenu, setDirectoryMenu] = useState<{ directory: MediaLibraryDirectory; target: "directory" | "files"; x: number; y: number } | null>(null);
  const [showHiddenDirectories, setShowHiddenDirectories] = useState(false);
  const [directoryConfirm, setDirectoryConfirm] = useState<{ directory: MediaLibraryDirectory; action: "untrack" | "delete" } | null>(null);
  const [directoryFilter, setDirectoryFilter] = useState<{ rootId: string; relativeDirectory: string; label: string } | null>(null);
  const [assets, setAssets] = useState<MediaAssetSummary[]>([]);
  const [thumbnails, setThumbnails] = useState<Record<string, string>>({});
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [mediaKind, setMediaKind] = useState<"" | MediaAssetKind>("");
  const [availability, setAvailability] = useState<"" | MediaAssetAvailability>("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<MediaAssetDetail | null>(null);
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyRoot, setBusyRoot] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [removeTarget, setRemoveTarget] = useState<MediaLibraryRoot | null>(null);
  const [webOpen, setWebOpen] = useState(false);
  const [webUrl, setWebUrl] = useState("");
  const [webProbe, setWebProbe] = useState<WebAssetProbe | null>(null);
  const [webDescription, setWebDescription] = useState("");
  const [webCreator, setWebCreator] = useState("");
  const [webLicense, setWebLicense] = useState("");
  const [webWindowStart, setWebWindowStart] = useState(0);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [webBusy, setWebBusy] = useState(false);
  const [analysisDialog, setAnalysisDialog] = useState<AnalysisDialog | null>(null);
  const [analysisDetail, setAnalysisDetail] = useState<MediaAnalysisDetail>("simple");
  const [analysisRunning, setAnalysisRunning] = useState(false);
  const [analysisProgress, setAnalysisProgress] = useState({ processed: 0, succeeded: 0, total: 0, failures: 0, costUsd: 0 });
  const stopBulkAnalysis = useRef(false);

  useEffect(() => {
    void refreshRoots();
  }, []);

  useEffect(() => {
    void refreshAssets();
  }, [appliedQuery, mediaKind, availability, offset, directoryFilter]);

  useEffect(() => {
    const close = () => setDirectoryMenu(null);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, []);

  useEffect(() => {
    if (!assets.length) return;
    let current = true;
    const ids = assets.map((asset) => asset.id);
    void (async () => {
      for (let index = 0; index < ids.length && current; index += 8) {
        try {
          const result = await window.subtitler.getMediaAssetThumbnails(ids.slice(index, index + 8));
          if (current) setThumbnails((existing) => ({ ...existing, ...result }));
        } catch {
          return;
        }
      }
    })();
    return () => { current = false; };
  }, [assets]);

  async function refreshRoots() {
    try {
      const next = await window.subtitler.listMediaLibraryRoots();
      setRoots(next);
      const entries = await Promise.all(next.map(async (root) => {
        try {
          return [root.id, await window.subtitler.listMediaLibraryDirectories(root.id)] as const;
        } catch {
          return [root.id, []] as const;
        }
      }));
      setDirectoriesByRoot(Object.fromEntries(entries));
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function refreshDirectories(rootId: string) {
    try {
      const directories = await window.subtitler.listMediaLibraryDirectories(rootId);
      setDirectoriesByRoot((current) => ({ ...current, [rootId]: directories }));
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function toggleDirectoryVisible(
    directory: MediaLibraryDirectory,
    kind: "subtree" | "direct",
  ) {
    try {
      if (directory.depth === 0 && kind === "subtree") {
        const root = roots.find((item) => item.id === directory.rootId);
        if (root) await toggleRoot(root);
        return;
      }
      const currentlyEnabled = kind === "subtree" ? directory.subtreeEnabled : directory.directEnabled;
      const nextEnabled = !currentlyEnabled;
      setDirectoriesByRoot((current) => ({
        ...current,
        [directory.rootId]: recalculateDirectoryVisibility(
          (current[directory.rootId] ?? []).map((item) => (
            item.relativePath === directory.relativePath
              ? {
                ...item,
                subtreeEnabled: kind === "subtree" ? nextEnabled : item.subtreeEnabled,
                directEnabled: kind === "direct" ? nextEnabled : item.directEnabled,
              }
              : item
          )),
          roots.find((root) => root.id === directory.rootId)?.enabled ?? true,
        ),
      }));
      await window.subtitler.setMediaLibraryDirectoryVisible(
        directory.rootId,
        directory.relativePath,
        kind,
        nextEnabled,
      );
      setOffset(0);
      await refreshAssets();
    } catch (reason) {
      setError(errorMessage(reason));
      await refreshDirectories(directory.rootId);
    }
  }

  async function setDirectoryHidden(directory: MediaLibraryDirectory, hidden: boolean) {
    setDirectoryMenu(null);
    setDirectoriesByRoot((current) => ({
      ...current,
      [directory.rootId]: (current[directory.rootId] ?? []).map((item) => (
        item.relativePath === directory.relativePath ? { ...item, hidden } : item
      )),
    }));
    try {
      await window.subtitler.setMediaLibraryDirectoryHidden(directory.rootId, directory.relativePath, hidden);
    } catch (reason) {
      setError(errorMessage(reason));
      await refreshDirectories(directory.rootId);
    }
  }

  async function refreshAssets() {
    setLoading(true);
    try {
      const result = await window.subtitler.listMediaAssets({
        query: appliedQuery || undefined,
        mediaKind: mediaKind || undefined,
        availability: availability || undefined,
        rootId: directoryFilter?.rootId,
        relativeDirectory: directoryFilter?.relativeDirectory,
        limit: PAGE_SIZE,
        offset,
      });
      setAssets(result.assets);
      setTotal(result.total);
      if (offset > 0 && result.total <= offset) setOffset(Math.max(0, Math.floor((result.total - 1) / PAGE_SIZE) * PAGE_SIZE));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }

  async function includeDirectoryFiles(directory: MediaLibraryDirectory, showOnly = false) {
    setDirectoryMenu(null);
    setError("");
    try {
      const root = roots.find((item) => item.id === directory.rootId);
      if (root && !root.enabled) await window.subtitler.setMediaLibraryRootEnabled(root.id, true);
      const subtreePaths = [...new Set([...directoryAncestors(directory.relativePath), directory.relativePath])]
        .filter(Boolean);
      await Promise.all([
        ...subtreePaths.map((relativePath) => window.subtitler.setMediaLibraryDirectoryVisible(
          directory.rootId,
          relativePath,
          "subtree",
          true,
        )),
        window.subtitler.setMediaLibraryDirectoryVisible(
          directory.rootId,
          directory.relativePath,
          "direct",
          true,
        ),
      ]);
      setMessage(t("media.indexingDirectory", { name: directory.name }));
      await window.subtitler.setMediaLibraryDirectoryIncluded(
        directory.rootId,
        directory.relativePath,
        true,
      );
      if (showOnly) {
        setDirectoryFilter({
          rootId: directory.rootId,
          relativeDirectory: directory.relativePath,
          label: t("media.filesIn", { name: directory.name }),
        });
      }
      setOffset(0);
      await Promise.all([refreshRoots(), refreshAssets()]);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function removeDirectoryFiles() {
    if (!directoryConfirm) return;
    const { directory, action } = directoryConfirm;
    try {
      const result = await window.subtitler.removeMediaLibraryDirectoryAssets(
        directory.rootId,
        directory.relativePath,
        action === "delete",
      );
      if (directoryFilter?.rootId === directory.rootId && directoryFilter.relativeDirectory === directory.relativePath) {
        setDirectoryFilter(null);
      }
      setDirectoryConfirm(null);
      setSelected(null);
      setMessage(
        action === "delete"
          ? t("media.untrackedDeleted", {
            records: result.removedAssets.toLocaleString(locale),
            files: result.deletedFiles.toLocaleString(locale),
            failures: result.errors.length ? t("media.operationFailures", { count: result.errors.length.toLocaleString(locale) }) : "",
          })
          : t("media.untracked", { records: result.removedAssets.toLocaleString(locale) }),
      );
      await Promise.all([refreshDirectories(directory.rootId), refreshAssets()]);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function addRoot() {
    const directory = await window.subtitler.chooseDirectory();
    if (!directory) return;
    try {
      const root = await window.subtitler.addMediaLibraryRoot(directory);
      await refreshRoots();
      await scanRoot(root.id);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function scanRoot(id: string) {
    setBusyRoot(id);
    setMessage(t("media.scanning"));
    setError("");
    try {
      const result = await window.subtitler.scanMediaLibraryRoot(id);
      setMessage(t("media.indexed", {
        indexed: result.indexed.toLocaleString(locale),
        discovered: result.discovered.toLocaleString(locale),
        missing: result.missing ? t("media.missingSuffix", { count: result.missing.toLocaleString(locale) }) : "",
      }));
      await Promise.all([refreshRoots(), refreshAssets()]);
    } catch (reason) {
      setError(errorMessage(reason));
      await refreshRoots();
    } finally {
      setBusyRoot("");
    }
  }

  async function toggleRoot(root: MediaLibraryRoot) {
    const enabled = !root.enabled;
    setRoots((current) => current.map((item) => item.id === root.id ? { ...item, enabled } : item));
    setDirectoriesByRoot((current) => ({
      ...current,
      [root.id]: recalculateDirectoryVisibility(current[root.id] ?? [], enabled),
    }));
    try {
      await window.subtitler.setMediaLibraryRootEnabled(root.id, enabled);
      if (root.enabled && selected?.rootId === root.id) {
        setSelected(null);
        setDescription("");
      }
      setOffset(0);
      await refreshAssets();
    } catch (reason) {
      setError(errorMessage(reason));
      await refreshRoots();
    }
  }

  async function removeRoot() {
    if (!removeTarget) return;
    try {
      const result = await window.subtitler.removeMediaLibraryRoot(removeTarget.id);
      if (selected?.rootId === removeTarget.id) {
        setSelected(null);
        setDescription("");
      }
      setRemoveTarget(null);
      setOffset(0);
      setMessage(t("media.locationRemoved", { count: result.removedAssets.toLocaleString(locale), suffix: result.removedAssets === 1 ? "" : "s" }));
      await Promise.all([refreshRoots(), refreshAssets()]);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function openAsset(asset: MediaAssetSummary) {
    try {
      const detail = await window.subtitler.getMediaAsset(asset.id);
      setSelected(detail);
      setDescription(detail.userDescription);
      setError("");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function saveDescription() {
    if (!selected) return;
    setSaving(true);
    try {
      const detail = await window.subtitler.updateMediaAssetDescription(selected.id, description);
      setSelected(detail);
      setDescription(detail.userDescription);
      setMessage(t("media.descriptionSaved"));
      await refreshAssets();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  function applySearch(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setAppliedQuery(query.trim());
  }

  function changeFilter(action: () => void) {
    setOffset(0);
    action();
  }

  async function openSingleAnalysis() {
    if (!selected) return;
    setError("");
    try {
      const estimates = await window.subtitler.estimateMediaAssetAnalysis(selected.id);
      setAnalysisDetail(estimates.find((estimate) => estimate.recommended)?.detail ?? estimates[0]?.detail ?? "simple");
      setAnalysisDialog({
        mode: "single",
        mediaKind: selected.mediaKind,
        title: selected.mediaKind === "image" ? t("media.analyzeImageTitle", { name: fileName(selected.canonicalPath) }) : t("media.analyzeVideoTitle", { name: fileName(selected.canonicalPath) }),
        assetIds: [selected.id],
        estimates,
        privacyNotice: estimates[0]?.privacyNotice ?? "",
      });
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function openBulkAnalysis() {
    setError("");
    try {
      const plan = await window.subtitler.planBulkMediaAnalysis(mediaKind);
      if (!plan.assetIds.length) {
        setMessage(t("media.noAnalysisAssets"));
        return;
      }
      setAnalysisDetail(recommendedBulkDetail(plan.estimates));
      setAnalysisDialog({
        mode: "bulk",
        mediaKind: mediaKind || "mixed",
        title: t("media.bulkAnalyzeTitle", { count: plan.assetIds.length.toLocaleString(locale), suffix: plan.assetIds.length === 1 ? "" : "s" }),
        assetIds: plan.assetIds,
        estimates: [],
        bulkEstimates: plan.estimates,
        privacyNotice: plan.privacyNotice,
      });
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function runAnalysis() {
    if (!analysisDialog) return;
    setAnalysisRunning(true);
    stopBulkAnalysis.current = false;
    setAnalysisProgress({ processed: 0, succeeded: 0, total: analysisDialog.assetIds.length, failures: 0, costUsd: 0 });
    let processed = 0;
    let succeeded = 0;
    let failures = 0;
    let costUsd = 0;
    for (const assetId of analysisDialog.assetIds) {
      if (stopBulkAnalysis.current) break;
      try {
        const result = await window.subtitler.analyzeMediaAsset(assetId, analysisDetail);
        succeeded += 1;
        costUsd += result.costUsd;
        if (analysisDialog.mode === "single") {
          setSelected(result.asset);
          setDescription(result.asset.userDescription);
        }
      } catch (reason) {
        failures += 1;
        if (analysisDialog.mode === "single") setError(errorMessage(reason));
      }
      processed += 1;
      setAnalysisProgress({ processed, succeeded, total: analysisDialog.assetIds.length, failures, costUsd });
    }
    const stopped = stopBulkAnalysis.current;
    setAnalysisRunning(false);
    setAnalysisDialog(null);
    setMessage(
      `${stopped ? t("media.analysisStopped") : t("media.analysisComplete")} ${t("media.analysisResult", {
        succeeded: succeeded.toLocaleString(locale),
        failures: failures ? t("media.failedSuffix", { count: failures.toLocaleString(locale) }) : "",
        cost: costUsd.toFixed(4),
      })}`,
    );
    await refreshAssets();
  }

  async function inspectWebSource(event: FormEvent) {
    event.preventDefault();
    setWebBusy(true);
    setError("");
    try {
      const probe = await window.subtitler.probeWebAsset(webUrl.trim());
      setWebProbe(probe);
      setWebDescription(probe.title);
      setWebCreator(probe.creator);
      setWebLicense(probe.licenseText);
      setRightsConfirmed(false);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setWebBusy(false);
    }
  }

  async function acquireWebSource() {
    if (!webProbe) return;
    setWebBusy(true);
    setError("");
    try {
      const asset = await window.subtitler.acquireWebAsset({
        sourceUrl: webProbe.sourceUrl,
        description: webDescription,
        creator: webCreator,
        licenseText: webLicense,
        rightsConfirmed,
        windowStartSec: webWindowStart,
      });
      setMessage(t("media.downloadedIndexed", { name: fileName(asset.canonicalPath) }));
      setWebOpen(false);
      setWebProbe(null);
      setWebUrl("");
      await Promise.all([refreshRoots(), refreshAssets()]);
      await openAsset(asset);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setWebBusy(false);
    }
  }

  function startPanelResize(event: PointerEvent<HTMLDivElement>, divider: "left" | "right") {
    const container = libraryView.current;
    if (!container) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = container.getBoundingClientRect();
    const move = (moveEvent: globalThis.PointerEvent) => {
      if (divider === "left") {
        setLeftPanelWidth(Math.min(38, Math.max(17, (moveEvent.clientX - bounds.left) / bounds.width * 100)));
      } else {
        setRightPanelWidth(Math.min(38, Math.max(18, (bounds.right - moveEvent.clientX) / bounds.width * 100)));
      }
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
  }

  const selectedEstimate = analysisDialog?.mode === "bulk"
    ? analysisDialog.bulkEstimates?.find((estimate) => estimate.detail === analysisDetail)
    : analysisDialog?.estimates.find((estimate) => estimate.detail === analysisDetail);
  const hiddenDirectoryCount = Object.values(directoriesByRoot)
    .reduce((totalCount, directories) => totalCount + directories.filter((directory) => directory.hidden).length, 0);
  return (
    <section
      ref={libraryView}
      className="library-view"
      aria-label={t("media.aria")}
      style={{
        "--library-left-width": `${leftPanelWidth}%`,
        "--library-right-width": `${rightPanelWidth}%`,
      } as CSSProperties}
    >
      <div className="library-roots panel">
        <div className="panel-title">
          <span>{t("media.locations")}</span>
          <span className="panel-actions">
            <button onClick={() => setWebOpen(true)}><Globe size={16} /> {t("media.web")}</button>
            <button onClick={() => void addRoot()}><FolderPlus size={16} /> {t("media.folder")}</button>
          </span>
        </div>
        <p className="library-help">{t("media.locationsHelp")}</p>
        <div className="library-directory-tree">
          {roots.map((root, rootIndex) => {
            const loadedDirectories = directoriesByRoot[root.id] ?? [];
            const directories = loadedDirectories.length ? loadedDirectories : [rootDirectoryPlaceholder(root)];
            const directoryMap = new Map(directories.map((directory) => [directory.relativePath, directory]));
            const visibleDirectories = directories.filter((directory) => (
              (showHiddenDirectories || ![...directoryAncestors(directory.relativePath), directory.relativePath]
                .some((ancestor) => directoryMap.get(ancestor)?.hidden))
              && (
                directory.depth === 0
                || directoryAncestors(directory.relativePath).every((ancestor) => (
                  expandedDirectories.has(directoryKey(root.id, ancestor))
                ))
              )
            ));
            return visibleDirectories.map((directory) => {
              const key = directoryKey(root.id, directory.relativePath);
              const expanded = expandedDirectories.has(key);
              const isRoot = directory.depth === 0;
              const ancestorVisible = isRoot || (
                root.enabled
                && directoryAncestors(directory.relativePath)
                  .slice(0, -1)
                  .every((ancestor) => directoryMap.get(ancestor)?.visible !== false)
              );
              const openMenu = (event: ReactMouseEvent, target: "directory" | "files") => {
                event.preventDefault();
                setDirectoryMenu({
                  directory,
                  target,
                  x: Math.min(event.clientX, window.innerWidth - 220),
                  y: Math.min(event.clientY, window.innerHeight - 230),
                });
              };
              return (
                <div className={`library-directory-group ${isRoot ? `root ${rootIndex > 0 ? "subsequent" : ""}` : ""}`} key={key}>
                  <div
                    className={`library-directory ${directory.visible ? "" : "muted"} ${directory.subtreeTrackedFileCount ? "" : "untracked"} ${directory.hidden ? "hidden-preview" : ""}`}
                    style={{ paddingLeft: `${8 + directory.depth * 15}px` }}
                    onContextMenu={(event) => openMenu(event, "directory")}
                  >
                    <button
                      className="library-directory-label"
                      title={isRoot ? root.canonicalPath : directory.relativePath}
                      onClick={() => setExpandedDirectories((current) => {
                        const next = new Set(current);
                        if (expanded) next.delete(key);
                        else next.add(key);
                        return next;
                      })}
                    >
                      {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <Folder size={14} />
                      {isRoot && root.kind === "managed" ? (
                        <span className="library-managed-root-copy">
                          <strong>{managedRootLabel(root, t)}</strong>
                          <small>{root.canonicalPath}</small>
                        </span>
                      ) : <span>{isRoot ? root.canonicalPath : directory.name}</span>}
                    </button>
                    <input
                      aria-label={t(directory.visible ? "media.hideDirectoryAria" : "media.showDirectoryAria", { name: isRoot ? root.canonicalPath : directory.name })}
                      type="checkbox"
                      checked={directory.visible && directory.subtreeTrackedFileCount > 0}
                      disabled={busyRoot === root.id || directory.subtreeTrackedFileCount === 0 || !ancestorVisible}
                      onChange={() => void toggleDirectoryVisible(directory, "subtree")}
                    />
                  </div>
                  {expanded && (directory.directFileCount > 0 || directory.trackedFileCount > 0) && (
                    <div
                      className={`library-files-group ${directory.included ? "included" : ""} ${directory.directFilesVisible ? "" : "muted"}`}
                      style={{ paddingLeft: `${27 + directory.depth * 15}px` }}
                      onContextMenu={(event) => openMenu(event, "files")}
                    >
                      <FileBox size={14} />
                      <span>{t("media.filesIn", { name: directory.name })}</span>
                      <small>{directory.trackedFileCount.toLocaleString(locale)} / {directory.directFileCount.toLocaleString(locale)}</small>
                      <input
                        aria-label={t(directory.directFilesVisible ? "media.hideFilesAria" : "media.showFilesAria", { name: directory.name })}
                        type="checkbox"
                        checked={directory.directFilesVisible && directory.trackedFileCount > 0}
                        disabled={!directory.visible || directory.trackedFileCount === 0}
                        onChange={() => void toggleDirectoryVisible(directory, "direct")}
                      />
                    </div>
                  )}
                </div>
              );
            });
          })}
        </div>
        <button
          className="library-show-hidden"
          disabled={hiddenDirectoryCount === 0}
          onClick={() => setShowHiddenDirectories((current) => !current)}
        >
          {showHiddenDirectories ? t("media.stopShowingHidden") : t("media.showHidden", { count: hiddenDirectoryCount ? ` (${hiddenDirectoryCount.toLocaleString(locale)})` : "" })}
        </button>
      </div>

      <div className="library-divider library-divider-left" role="separator" aria-orientation="vertical" onPointerDown={(event) => startPanelResize(event, "left")} />

      <div className="library-catalog panel">
        <div className="panel-title">
          <span>{t("media.catalog")} <small>{t("media.assets", { count: total.toLocaleString(locale) })}</small></span>
          <button onClick={() => void openBulkAnalysis()}><Layers size={16} /> {t("media.analyzeUnanalyzed")}</button>
        </div>
        {directoryFilter && (
          <div className="library-directory-filter">
            <span>{t("media.showingOnly", { label: directoryFilter.label })}</span>
            <button className="icon-button" aria-label={t("media.clearFilter")} onClick={() => { setDirectoryFilter(null); setOffset(0); }}><X size={14} /></button>
          </div>
        )}
        <form className="library-filters" onSubmit={applySearch}>
          <div className="row library-search">
            <input aria-label={t("media.searchAria")} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("media.searchPlaceholder")} />
            <button type="submit"><Search size={15} /> {t("common.search")}</button>
          </div>
          <select aria-label={t("media.kindAria")} value={mediaKind} onChange={(event) => changeFilter(() => setMediaKind(event.target.value as "" | MediaAssetKind))}>
            <option value="video">{t("media.videos")}</option>
            <option value="image">{t("media.images")}</option>
            <option value="audio" disabled>{t("media.audioKind")}</option>
            <option value="">{t("media.all")}</option>
          </select>
          <select aria-label={t("media.availabilityAria")} value={availability} onChange={(event) => changeFilter(() => setAvailability(event.target.value as "" | MediaAssetAvailability))}>
            <option value="">{t("media.anyAvailability")}</option>
            <option value="active">{t("media.available")}</option>
            <option value="missing">{t("media.missing")}</option>
            <option value="incompatible">{t("media.incompatible")}</option>
          </select>
        </form>
        {error && !webOpen && !analysisDialog && <div className="library-alert error" role="alert"><AlertTriangle size={15} /> {error}</div>}
        {message && <div className="library-alert" role="status">{message}</div>}
        <div className="library-assets" aria-busy={loading}>
          {!loading && assets.length === 0 && <div className="library-empty">{t("media.noMatches")}</div>}
          {assets.map((asset) => (
            <button className={`library-asset ${selected?.id === asset.id ? "selected" : ""}`} key={asset.id} onClick={() => void openAsset(asset)}>
              <span className="library-asset-thumbnail">
                {thumbnails[asset.id]
                  ? <img src={thumbnails[asset.id]} alt="" />
                  : asset.mediaKind === "video" ? <Film size={18} /> : <Image size={18} />}
              </span>
              <strong className="library-asset-name" title={asset.canonicalPath}>{fileName(asset.canonicalPath)}</strong>
              <span className="library-asset-kind">{mediaKindLabel(asset.mediaKind, t)}</span>
              <span className="library-asset-dimensions">{asset.width && asset.height ? `${asset.width}×${asset.height}` : "—"}</span>
              <span className="library-asset-duration">{asset.mediaKind === "video" && asset.durationMs ? formatDuration(asset.durationMs) : "—"}</span>
              <span className={`status library-availability ${asset.availability}`}>{availabilityLabel(asset.availability, t)}</span>
            </button>
          ))}
        </div>
        <div className="library-pagination">
          <button disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>{t("common.previous")}</button>
          <span>{total ? t("media.page", { start: (offset + 1).toLocaleString(locale), end: Math.min(total, offset + PAGE_SIZE).toLocaleString(locale), total: total.toLocaleString(locale) }) : t("media.zeroAssets")}</span>
          <button disabled={offset + PAGE_SIZE >= total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>{t("common.next")}</button>
        </div>
      </div>

      <div className="library-divider library-divider-right" role="separator" aria-orientation="vertical" onPointerDown={(event) => startPanelResize(event, "right")} />

      <aside className="library-detail panel">
        <div className="panel-title">{t("media.assetDetails")}</div>
        {!selected ? (
          <div className="library-empty">{t("media.selectAsset")}</div>
        ) : (
          <>
            <div className="library-detail-heading">
              <span className="library-detail-thumbnail">
                {thumbnails[selected.id]
                  ? <img src={thumbnails[selected.id]} alt="" />
                  : selected.mediaKind === "video" ? <Film size={20} /> : <Image size={20} />}
              </span>
              <span><strong>{fileName(selected.canonicalPath)}</strong><small>{selected.canonicalPath}</small></span>
            </div>
            <dl className="library-facts">
              <dt>{t("media.status")}</dt><dd>{availabilityLabel(selected.availability, t)} · {analysisStateLabel(selected.analysisState, t)}</dd>
              <dt>{t("media.size")}</dt><dd>{formatBytes(selected.sizeBytes)}</dd>
              <dt>{mediaKindLabel(selected.mediaKind, t)}</dt>
              <dd>{selected.width && selected.height ? `${selected.width}×${selected.height}` : t("media.unknownDimensions")}{selected.durationMs ? ` · ${formatDuration(selected.durationMs)}` : ""}</dd>
              {selected.mediaKind === "video" && <><dt>{t("media.audio")}</dt><dd>{selected.hasAudio ? selected.audioCodec || t("media.present") : t("common.none")}</dd></>}
              {selected.mediaKind === "image" && <><dt>{t("media.transparency")}</dt><dd>{transparencyLabel(selected.transparency, t)}</dd></>}
            </dl>
            <label>
              {t("media.yourDescription")}
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder={t("media.descriptionPlaceholder", { kind: mediaKindLabel(selected.mediaKind, t) })} />
            </label>
            {(selected.aiDescription || selected.inferredDescription) && (
              <div className="library-generated-description">
                <strong>{selected.aiDescription ? t("media.aiDescription") : t("media.inferredDescription")}</strong>
                <span>{selected.aiDescription || selected.inferredDescription}</span>
              </div>
            )}
            <div className="button-row">
              <button disabled={analysisRunning || selected.availability !== "active"} onClick={() => void openSingleAnalysis()}>
                <Sparkles size={16} /> {selected.analysisState === "ready" ? t("media.analyzeAgain", { kind: mediaKindLabel(selected.mediaKind, t) }) : t("media.analyzeKind", { kind: mediaKindLabel(selected.mediaKind, t) })}
              </button>
              <button className="primary" disabled={saving || description === selected.userDescription} onClick={() => void saveDescription()}>
                <Save size={16} /> {saving ? t("common.saving") : t("media.saveDescription")}
              </button>
            </div>
          </>
        )}
      </aside>

      {directoryMenu && (
        <div
          className="library-context-menu"
          role="menu"
          style={{ left: directoryMenu.x, top: directoryMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          {directoryMenu.target === "directory" && directoryMenu.directory.depth === 0 && (
            <button role="menuitem" disabled={Boolean(busyRoot)} onClick={() => { setDirectoryMenu(null); void scanRoot(directoryMenu.directory.rootId); }}>{t("media.rescanTracked")}</button>
          )}
          <button role="menuitem" onClick={() => void includeDirectoryFiles(directoryMenu.directory)}>{t("media.addCatalog")}</button>
          <button role="menuitem" onClick={() => void includeDirectoryFiles(directoryMenu.directory, true)}>{t("media.showOnlyCatalog")}</button>
          {directoryMenu.target === "directory" && (
            <button role="menuitem" onClick={() => void setDirectoryHidden(directoryMenu.directory, !directoryMenu.directory.hidden)}>
              {directoryMenu.directory.hidden ? t("media.unhideDirectory") : t("media.hideDirectory")}
            </button>
          )}
          {directoryMenu.target === "files" && directoryMenu.directory.trackedFileCount > 0 && (
            <button role="menuitem" onClick={() => { setDirectoryConfirm({ directory: directoryMenu.directory, action: "untrack" }); setDirectoryMenu(null); }}>{t("media.untrackCatalog")}</button>
          )}
          {directoryMenu.target === "files" && directoryMenu.directory.managed && directoryMenu.directory.trackedFileCount > 0 && (
            <button className="danger" role="menuitem" onClick={() => { setDirectoryConfirm({ directory: directoryMenu.directory, action: "delete" }); setDirectoryMenu(null); }}>{t("media.deleteManaged")}</button>
          )}
          {directoryMenu.target === "directory" && directoryMenu.directory.depth === 0 && !directoryMenu.directory.managed && (
            <button
              className="danger"
              role="menuitem"
              onClick={() => {
                setRemoveTarget(roots.find((root) => root.id === directoryMenu.directory.rootId) ?? null);
                setDirectoryMenu(null);
              }}
            >
              {t("media.removeLocation")}
            </button>
          )}
        </div>
      )}

      {directoryConfirm && (
        <div className="library-modal-backdrop" role="presentation">
          <section className="library-confirm-modal panel" role="dialog" aria-modal="true" aria-labelledby="remove-directory-title">
            <div className="panel-title"><span id="remove-directory-title"><Trash2 size={17} /> {directoryConfirm.action === "delete" ? t("media.deleteManagedQuestion") : t("media.untrackQuestion")}</span></div>
            <p>{t("media.affectsRecords", { count: directoryConfirm.directory.trackedFileCount.toLocaleString(locale), suffix: directoryConfirm.directory.trackedFileCount === 1 ? "" : "s" })} <strong>{t("media.filesIn", { name: directoryConfirm.directory.name })}</strong>.</p>
            {directoryConfirm.action === "delete" ? (
              <p className="library-alert error"><AlertTriangle size={15} /> {t("media.deleteWarning")}</p>
            ) : (
              <p className="library-help">{t("media.untrackHelp")}</p>
            )}
            <div className="button-row">
              <button onClick={() => setDirectoryConfirm(null)}>{t("common.cancel")}</button>
              <button className="danger" onClick={() => void removeDirectoryFiles()}><Trash2 size={16} /> {directoryConfirm.action === "delete" ? t("media.deleteFiles") : t("media.untrackFiles")}</button>
            </div>
          </section>
        </div>
      )}

      {removeTarget && (
        <div className="library-modal-backdrop" role="presentation">
          <section className="library-confirm-modal panel" role="dialog" aria-modal="true" aria-labelledby="remove-location-title">
            <div className="panel-title"><span id="remove-location-title"><Trash2 size={17} /> {t("media.removeLocationQuestion")}</span></div>
            <p>{t("media.removeEveryRecord")}</p>
            <code>{removeTarget.canonicalPath}</code>
            <p className="library-help">{t("media.folderNotDeleted")}</p>
            <div className="button-row">
              <button onClick={() => setRemoveTarget(null)}>{t("common.cancel")}</button>
              <button className="danger" onClick={() => void removeRoot()}><Trash2 size={16} /> {t("media.removeLocationAction")}</button>
            </div>
          </section>
        </div>
      )}

      {analysisDialog?.mode === "single" && analysisDialog.mediaKind === "image" && (
        <div className="library-modal-backdrop" role="presentation">
          <section className="library-image-analysis-modal panel" role="dialog" aria-modal="true" aria-labelledby="image-analysis-title">
            <div className="panel-title">
              <span id="image-analysis-title"><Image size={18} /> {t("media.describeImageAi")}</span>
              {!analysisRunning && <button className="icon-button" aria-label={t("common.close")} onClick={() => setAnalysisDialog(null)}><X size={17} /></button>}
            </div>
            <div className="library-image-analysis-hero">
              <span>
                {selected && thumbnails[selected.id]
                  ? <img src={thumbnails[selected.id]} alt="" />
                  : <Image size={32} />}
              </span>
              <div>
                <strong>{selected ? fileName(selected.canonicalPath) : t("media.selectedImage")}</strong>
                <p>{t("media.imageAnalysisSummary")}</p>
              </div>
            </div>
            <dl className="library-image-analysis-facts">
              <dt>{t("media.upload")}</dt><dd>{t("media.reducedImage")}</dd>
              <dt>{t("media.model")}</dt><dd>{selectedEstimate && "model" in selectedEstimate ? selectedEstimate.model : t("media.openaiVision")}</dd>
              <dt>{t("media.estimatedCost")}</dt><dd>${selectedEstimate?.estimatedCostUsd.toFixed(4) ?? "—"}</dd>
            </dl>
            <div className="library-image-analysis-note">
              <Sparkles size={17} />
              <p>{t("media.imageAnalysisNote")}</p>
            </div>
            <p className="library-help">{t("media.imagePrivacy")}</p>
            <div className="button-row">
              <button disabled={analysisRunning} onClick={() => setAnalysisDialog(null)}>{t("common.cancel")}</button>
              <button className="primary" disabled={analysisRunning || !selectedEstimate} onClick={() => void runAnalysis()}>
                <Sparkles size={16} /> {analysisRunning ? t("media.analyzingImage") : t("media.analyzeImage")}
              </button>
            </div>
          </section>
        </div>
      )}

      {analysisDialog && !(analysisDialog.mode === "single" && analysisDialog.mediaKind === "image") && (
        <div className="library-modal-backdrop" role="presentation">
          <section className="library-analysis-modal panel" role="dialog" aria-modal="true" aria-labelledby="analysis-title">
            <div className="panel-title">
              <span id="analysis-title"><Sparkles size={17} /> {analysisDialog.title}</span>
              {!analysisRunning && <button className="icon-button" aria-label={t("common.close")} onClick={() => setAnalysisDialog(null)}><X size={17} /></button>}
            </div>
            <p className="library-help">
              {selectedEstimate && "privacyNotice" in selectedEstimate
                ? analysisPrivacyNotice(selectedEstimate, t)
                : t("media.privacyBulk")}
            </p>
            <div className="analysis-detail-options">
              {(analysisDialog.mode === "bulk" ? analysisDialog.bulkEstimates ?? [] : analysisDialog.estimates).map((estimate) => {
                const recommended = "recommended" in estimate
                  ? estimate.recommended
                  : estimate.detail === recommendedBulkDetail(analysisDialog.bulkEstimates ?? []);
                return (
                  <button
                    key={estimate.detail}
                    className={analysisDetail === estimate.detail ? "selected" : ""}
                    disabled={analysisRunning}
                    onClick={() => setAnalysisDetail(estimate.detail)}
                  >
                    <strong>{detailLabel(estimate.detail, t)}</strong>
                    {recommended && <small className="analysis-recommended">{t("media.recommended")}</small>}
                    <span>{t("media.frames", { prefix: "adaptive" in estimate && estimate.adaptive ? t("media.expectedPrefix") : "", count: estimate.sampleCount.toLocaleString(locale), suffix: estimate.sampleCount === 1 ? "" : "s" })}</span>
                    {"adaptive" in estimate && estimate.adaptive && (
                      <small>{t("media.probeDetails", { coarse: estimate.coarseSampleCount.toLocaleString(locale), maximum: estimate.maximumSampleCount.toLocaleString(locale), transitions: estimate.maximumTransitionCount.toLocaleString(locale), precision: formatPrecision(estimate.breakpointPrecisionSec) })}</small>
                    )}
                    <small>{t("media.estimated", { cost: estimate.estimatedCostUsd.toFixed(4) })}</small>
                  </button>
                );
              })}
            </div>
            {analysisDialog.mode === "bulk" && <p className="library-help">{t("media.bulkHelp")}</p>}
            {analysisRunning && (
              <div className="analysis-progress">
                <div><span style={{ width: `${analysisProgress.total ? analysisProgress.processed / analysisProgress.total * 100 : 0}%` }} /></div>
                <strong>{t("media.processed", { processed: analysisProgress.processed.toLocaleString(locale), total: analysisProgress.total.toLocaleString(locale) })}</strong>
                <small>{t("media.progress", { succeeded: analysisProgress.succeeded.toLocaleString(locale), failures: analysisProgress.failures ? t("media.failedSuffix", { count: analysisProgress.failures.toLocaleString(locale) }) : "", cost: analysisProgress.costUsd.toFixed(4) })}</small>
              </div>
            )}
            <div className="button-row">
              {analysisRunning
                ? <button onClick={() => { stopBulkAnalysis.current = true; }}>{t("media.stopAfterCurrent")}</button>
                : <button onClick={() => setAnalysisDialog(null)}>{t("common.cancel")}</button>}
              <button className="primary" disabled={analysisRunning || !selectedEstimate} onClick={() => void runAnalysis()}>
                <Sparkles size={16} /> {analysisDialog.mode === "bulk" ? t("media.startBulk") : t("media.analyze")}
              </button>
            </div>
          </section>
        </div>
      )}

      {webOpen && (
        <div className="library-modal-backdrop" role="presentation">
          <section className="library-web-modal panel" role="dialog" aria-modal="true" aria-labelledby="web-import-title">
            <div className="panel-title">
              <span id="web-import-title"><Globe size={17} /> {t("media.addWeb")}</span>
              <button className="icon-button" aria-label={t("common.close")} disabled={webBusy} onClick={() => setWebOpen(false)}><X size={17} /></button>
            </div>
            {error && <div className="library-alert error" role="alert"><AlertTriangle size={15} /> {error}</div>}
            {!webProbe ? (
              <form className="stack" onSubmit={inspectWebSource}>
                <p className="library-help">{t("media.webHelp")}</p>
                <label>{t("media.sourceUrl")}<input type="url" required value={webUrl} onChange={(event) => setWebUrl(event.target.value)} placeholder="https://…" /></label>
                <button className="primary" disabled={webBusy || !webUrl.trim()} type="submit">{webBusy ? t("media.inspecting") : t("media.inspectSource")}</button>
              </form>
            ) : (
              <div className="stack">
                <div className="library-web-summary">
                  <strong>{webProbe.title}</strong>
                  <span>{webProbe.creator || t("media.unknownCreator")}{webProbe.durationSec !== null ? ` · ${formatDuration(webProbe.durationSec * 1000)}` : ""}</span>
                  <small>{webProbe.sourcePageUrl}</small>
                </div>
                <label>{t("media.finalDescription")}<textarea value={webDescription} onChange={(event) => setWebDescription(event.target.value)} /></label>
                <div className="two-col">
                  <label>{t("media.creator")}<input value={webCreator} onChange={(event) => setWebCreator(event.target.value)} /></label>
                  <label>{t("media.licenseNote")}<input value={webLicense} onChange={(event) => setWebLicense(event.target.value)} placeholder={t("media.licensePlaceholder")} /></label>
                </div>
                {webProbe.durationSec !== null && webProbe.durationSec > 1200 && (
                  <label>{t("media.windowStart")}<input type="number" min={0} max={Math.max(0, webProbe.durationSec - 1)} value={webWindowStart} onChange={(event) => setWebWindowStart(Number(event.target.value))} /></label>
                )}
                <label className="check library-rights-confirmation">
                  <input type="checkbox" checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)} />
                  {t("media.rightsConfirm")}
                </label>
                <p className="library-help">{t("media.webDownloadHelp")}</p>
                <div className="button-row">
                  <button disabled={webBusy} onClick={() => { setWebProbe(null); setRightsConfirmed(false); }}>{t("common.back")}</button>
                  <button className="primary" disabled={webBusy || !rightsConfirmed || !webDescription.trim()} onClick={() => void acquireWebSource()}><Download size={16} /> {webBusy ? t("media.downloading") : t("media.approveDownload")}</button>
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function fileName(filePath: string): string {
  return filePath.split(/[\\/]/).at(-1) || filePath;
}

function detailLabel(detail: MediaAnalysisDetail, t: ReturnType<typeof useI18n>["t"]): string {
  return t(`media.detail.${detail}`);
}

function recommendedBulkDetail(estimates: NonNullable<AnalysisDialog["bulkEstimates"]>): MediaAnalysisDetail {
  return estimates.reduce(
    (best, estimate) => estimate.recommendedAssetCount > best.recommendedAssetCount ? estimate : best,
    estimates[0] ?? { detail: "simple", recommendedAssetCount: 0, assetCount: 0, sampleCount: 0, estimatedCostUsd: 0 },
  ).detail;
}

function formatPrecision(value: number | null): string {
  if (value === null) return "—";
  return value < 1 ? value.toFixed(2).replace(/0+$/u, "").replace(/\.$/u, "") : value.toFixed(0);
}

function directoryAncestors(relativePath: string): string[] {
  const parts = relativePath.split(/[\\/]/u).filter(Boolean);
  const separator = relativePath.includes("\\") ? "\\" : "/";
  return ["", ...parts.slice(0, -1).map((_part, index) => parts.slice(0, index + 1).join(separator))];
}

function directoryKey(rootId: string, relativePath: string): string {
  return `${rootId}:${relativePath}`;
}

function recalculateDirectoryVisibility(
  directories: MediaLibraryDirectory[],
  rootEnabled: boolean,
): MediaLibraryDirectory[] {
  const subtreeSettings = new Map(directories.map((directory) => [directory.relativePath, directory.subtreeEnabled]));
  return directories.map((directory) => {
    const visible = rootEnabled && [...directoryAncestors(directory.relativePath), directory.relativePath]
      .every((ancestor) => subtreeSettings.get(ancestor) !== false);
    return {
      ...directory,
      visible,
      directFilesVisible: visible && directory.directEnabled,
    };
  });
}

function managedRootLabel(root: MediaLibraryRoot, t: ReturnType<typeof useI18n>["t"]): string {
  if (root.purpose === "web") return t("media.managedWeb");
  if (root.purpose === "generated") return t("media.managedGenerated");
  return t("media.managed");
}

function transparencyLabel(transparency: MediaAssetDetail["transparency"], t: ReturnType<typeof useI18n>["t"]): string {
  if (transparency === "present") return t("media.alphaPresent");
  if (transparency === "absent") return t("media.alphaAbsent");
  if (transparency === "unsupported") return t("media.alphaUnsupported");
  return t("media.alphaUnknown");
}

function mediaKindLabel(kind: MediaAssetKind, t: ReturnType<typeof useI18n>["t"]): string {
  return kind === "video" ? t("media.video") : t("media.image");
}

function availabilityLabel(value: MediaAssetAvailability, t: ReturnType<typeof useI18n>["t"]): string {
  if (value === "active") return t("media.available");
  if (value === "missing") return t("media.missing");
  return t("media.incompatible");
}

function analysisStateLabel(value: MediaAssetDetail["analysisState"], t: ReturnType<typeof useI18n>["t"]): string {
  return t(`media.state.${value}`);
}

function analysisPrivacyNotice(estimate: MediaAssetAnalysisEstimate, t: ReturnType<typeof useI18n>["t"]): string {
  return estimate.adaptive
    ? t("media.privacyAdaptive", { count: estimate.sampleCount.toLocaleString(), coarse: estimate.coarseSampleCount.toLocaleString() })
    : t("media.privacySampled", { count: estimate.sampleCount.toLocaleString(), suffix: estimate.sampleCount === 1 ? "" : "s" });
}

function rootDirectoryPlaceholder(root: MediaLibraryRoot): MediaLibraryDirectory {
  return {
    rootId: root.id,
    relativePath: "",
    name: fileName(root.canonicalPath),
    depth: 0,
    directFileCount: 0,
    trackedFileCount: 0,
    subtreeTrackedFileCount: 0,
    included: true,
    subtreeEnabled: true,
    directEnabled: true,
    visible: root.enabled,
    directFilesVisible: root.enabled,
    hidden: false,
    managed: root.kind === "managed",
    purpose: root.purpose,
  };
}

function formatDuration(milliseconds: number): string {
  const seconds = Math.round(milliseconds / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}` : `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`;
}
