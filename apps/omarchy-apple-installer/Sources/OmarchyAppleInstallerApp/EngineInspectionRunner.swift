import Foundation
import OmarchyAppleInstallerTrustCore

struct EngineInspectionRunner: Sendable {
  func inspect(deviceIdentifier: String? = nil, developerOverride: DeveloperModelOverride? = nil)
    async throws -> EngineInspectionResult
  {
    let scratch = try scratchDirectory()
    if let deviceIdentifier,
      let archive = try SealedEngineArtifactLocator().locate(for: deviceIdentifier)
    {
      return try await inspect(archive, developerOverride: developerOverride, in: scratch)
    }
    let archive = try ValidationEngineArtifactLocator().locate()
    return try await inspect(archive, developerOverride: developerOverride, in: scratch)
  }

  func inspect(
    _ archive: PinnedAsahiEngineArchive,
    developerOverride: DeveloperModelOverride? = nil
  ) async throws -> EngineInspectionResult {
    try await inspect(archive, developerOverride: developerOverride, in: scratchDirectory())
  }

  private func inspect(
    _ archive: PinnedAsahiEngineArchive,
    developerOverride: DeveloperModelOverride?,
    in scratch: URL
  ) async throws -> EngineInspectionResult {
    let transcript = try await PinnedAsahiEngineExecutor().inspect(
      archive,
      developerOverride: developerOverride,
      in: scratch
    )
    return EngineInspectionResult(
      transcript: transcript,
      validated: try AppleInstallerTrustCore()
        .validateEngineTranscript(transcript)
    )
  }

  private func scratchDirectory() throws -> URL {
    let directory = try FileManager.default.url(
      for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
    ).appendingPathComponent(
      "com.omarchy.mx.installer/scratch",
      isDirectory: true
    )
    if !FileManager.default.fileExists(atPath: directory.path) {
      try FileManager.default.createDirectory(
        at: directory,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
      )
    }
    return directory
  }
}

struct EngineInspectionResult: Sendable {
  let transcript: Data
  let validated: ValidatedEngineTranscript
}

enum InstallerAppError: Error {
  case hostChanged
  case workspaceUnavailable
  case inspectionRequired
  case approvalUnavailable
  case previewFixtureUnavailable
}
