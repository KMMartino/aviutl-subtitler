export async function extractZipArchive(zipPath: string, destination: string): Promise<void> {
  const { default: extract } = await import("@electron-internal/extract-zip");
  await extract(zipPath, { dir: destination });
}
