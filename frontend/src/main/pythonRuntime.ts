import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { runtimePaths, type RuntimePaths } from "./paths";

const requiredRuntimeImports = ["ctc_forced_aligner"];

export type PythonRuntimeStatus = {
  selectedPath: string;
  resolvedPath: string;
  source: "selected" | "managed" | "path" | "missing";
  ready: boolean;
  version: string;
  venvPath: string;
  requirementsInstalled: boolean;
  error: string;
};

export async function getPythonRuntimeStatus(settingsPythonPath = "", paths = runtimePaths()): Promise<PythonRuntimeStatus> {
  const venvPath = managedVenvPath(paths);
  const selected = settingsPythonPath.trim();
  if (selected) {
    return statusForPython(selected, "selected", selected, venvPath, true);
  }
  const managedPython = managedPythonPath(paths);
  if (fs.existsSync(managedPython)) {
    return statusForPython(managedPython, "managed", selected, venvPath, requirementsMarkerExists(paths));
  }
  return statusForPython("python", "path", selected, venvPath, true).catch((error) => ({
    selectedPath: selected,
    resolvedPath: "",
    source: "missing",
    ready: false,
    version: "",
    venvPath,
    requirementsInstalled: false,
    error: error instanceof Error ? error.message : String(error),
  }));
}

export async function createManagedPythonEnv(onLog: (text: string) => void = () => undefined, paths = runtimePaths()): Promise<PythonRuntimeStatus> {
  const venvPath = managedVenvPath(paths);
  fs.mkdirSync(path.dirname(venvPath), { recursive: true });
  onLog("$ python -m venv\n");
  await runCommand("python", ["-m", "venv", venvPath], onLog);
  return getPythonRuntimeStatus("", paths);
}

export async function installPythonRequirements(onLog: (text: string) => void = () => undefined, paths = runtimePaths()): Promise<PythonRuntimeStatus> {
  const python = managedPythonPath(paths);
  if (!fs.existsSync(python)) throw new Error("Managed Python environment does not exist. Create it first.");
  const requirements = fs.existsSync(path.join(paths.bundledBackendRoot, "requirements-lock-win.txt"))
    ? path.join(paths.bundledBackendRoot, "requirements-lock-win.txt")
    : path.join(paths.bundledBackendRoot, "requirements.txt");
  onLog("$ python -m pip install --upgrade pip\n");
  await runCommand(python, ["-m", "pip", "install", "--upgrade", "pip"], onLog);
  onLog(`$ python -m pip install -r ${requirements}\n`);
  await runCommand(python, ["-m", "pip", "install", "-r", requirements], onLog);
  const dependencyCheck = await checkRequiredRuntimeImports(python);
  if (!dependencyCheck.ready) throw new Error(dependencyCheck.error);
  fs.writeFileSync(requirementsMarkerPath(paths), new Date().toISOString(), "utf8");
  return getPythonRuntimeStatus("", paths);
}

function managedVenvPath(paths: RuntimePaths): string {
  return path.join(paths.managedPythonRoot, ".venv");
}

function managedPythonPath(paths: RuntimePaths): string {
  return path.join(managedVenvPath(paths), "Scripts", "python.exe");
}

function requirementsMarkerPath(paths: RuntimePaths): string {
  return path.join(managedVenvPath(paths), ".requirements-installed");
}

function requirementsMarkerExists(paths: RuntimePaths): boolean {
  return fs.existsSync(requirementsMarkerPath(paths));
}

async function statusForPython(
  pythonPath: string,
  source: PythonRuntimeStatus["source"],
  selectedPath: string,
  venvPath: string,
  requirementsInstalled: boolean,
): Promise<PythonRuntimeStatus> {
  try {
    const version = firstLine(await runCommand(pythonPath, ["--version"]));
    const dependencyCheck = await checkRequiredRuntimeImports(pythonPath);
    const dependenciesInstalled = dependencyCheck.ready && (source !== "managed" || requirementsInstalled);
    return {
      selectedPath,
      resolvedPath: pythonPath,
      source,
      ready: true,
      version,
      venvPath,
      requirementsInstalled: dependenciesInstalled,
      error: dependencyCheck.ready ? "" : dependencyCheck.error,
    };
  } catch (error) {
    return { selectedPath, resolvedPath: pythonPath, source: "missing", ready: false, version: "", venvPath, requirementsInstalled: false, error: error instanceof Error ? error.message : String(error) };
  }
}

async function checkRequiredRuntimeImports(pythonPath: string): Promise<{ ready: boolean; error: string }> {
  const importScript = [
    "import importlib.util",
    `missing = [name for name in ${JSON.stringify(requiredRuntimeImports)} if importlib.util.find_spec(name) is None]`,
    "raise SystemExit('Missing Python package: ' + ', '.join(missing) if missing else 0)",
  ].join("; ");
  try {
    await runCommand(pythonPath, ["-c", importScript]);
    return { ready: true, error: "" };
  } catch (error) {
    return {
      ready: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function runCommand(command: string, args: string[], onLog: (text: string) => void = () => undefined): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8");
      stdout += text;
      onLog(text);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8");
      stderr += text;
      onLog(text);
    });
    child.on("error", (error) => reject(error));
    child.on("exit", (code) => code === 0 ? resolve(stdout || stderr) : reject(new Error(stderr.trim() || `${command} failed`)));
  });
}

function firstLine(value: string): string {
  return value.split(/\r?\n/).find(Boolean) ?? "";
}
