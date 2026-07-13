import path from "node:path";

export const userDataOverrideEnvironmentVariable = "SUBUTL_USER_DATA_DIR";
export const userDataOverrideArgument = "--subutl-user-data-dir=";

export function userDataOverride(environment = process.env, argv = process.argv): string | undefined {
  const commandLineValues = argv
    .filter((argument) => argument.startsWith(userDataOverrideArgument))
    .map((argument) => argument.slice(userDataOverrideArgument.length));
  if (commandLineValues.length > 1) {
    throw new Error(`${userDataOverrideArgument.slice(0, -1)} may only be specified once.`);
  }
  const fromCommandLine = commandLineValues[0];
  if (fromCommandLine !== undefined && fromCommandLine.trim() === "") {
    throw new Error(`${userDataOverrideArgument.slice(0, -1)} requires an absolute path.`);
  }
  const value = fromCommandLine ?? environment[userDataOverrideEnvironmentVariable];
  if (value === undefined || value.trim() === "") return undefined;
  if (!path.isAbsolute(value)) {
    const source = fromCommandLine === undefined ? userDataOverrideEnvironmentVariable : userDataOverrideArgument.slice(0, -1);
    throw new Error(`${source} must be an absolute path.`);
  }
  return path.normalize(value);
}
