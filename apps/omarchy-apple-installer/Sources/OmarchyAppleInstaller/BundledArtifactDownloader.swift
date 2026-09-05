import Darwin
import Foundation

/// Supplies included release assets to the same digest-verifying stager used
/// for HTTPS downloads. Bundled bytes never bypass catalog verification.
struct BundledArtifactDownloader: ArtifactDownloading {
  let directory: URL?
  let fallback: any ArtifactDownloading

  init(
    directory: URL? = Bundle.main.resourceURL?.appendingPathComponent("Release/Assets"),
    fallback: any ArtifactDownloading = ProgressReportingArtifactDownloader()
  ) {
    self.directory = directory
    self.fallback = fallback
  }

  func download(from sourceURL: URL) async throws -> URL {
    try await download(from: sourceURL, expectedSizeBytes: UInt64.max, onBytes: nil)
  }

  func download(
    from sourceURL: URL,
    expectedSizeBytes: UInt64,
    onBytes: ArtifactByteProgressHandler?
  ) async throws -> URL {
    guard let directory else {
      return try await fallback.download(
        from: sourceURL, expectedSizeBytes: expectedSizeBytes, onBytes: onBytes)
    }
    let name = sourceURL.lastPathComponent
    guard PinnedInstallerArtifact.isSafeFileName(name) else {
      throw ArtifactStageError.invalidFileName
    }
    let source = directory.appendingPathComponent(name)
    var status = stat()
    if lstat(source.path, &status) != 0 {
      guard errno == ENOENT else { throw ArtifactStageError.unsafeStagedFile(name) }
      return try await fallback.download(
        from: sourceURL, expectedSizeBytes: expectedSizeBytes, onBytes: onBytes)
    }
    guard status.st_mode & S_IFMT == S_IFREG, status.st_size >= 0,
      UInt64(status.st_size) <= expectedSizeBytes
    else { throw ArtifactStageError.unsafeStagedFile(name) }
    let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(
      ".omarchy-bundled-\(UUID().uuidString)")
    do {
      try FileManager.default.copyItem(at: source, to: temporary)
      onBytes?(UInt64(status.st_size))
      return temporary
    } catch {
      try? FileManager.default.removeItem(at: temporary)
      throw error
    }
  }
}
