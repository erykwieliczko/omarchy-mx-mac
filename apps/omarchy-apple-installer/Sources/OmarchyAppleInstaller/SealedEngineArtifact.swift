#if os(macOS)
  import Foundation

  /// The inspection engine is selected from the app's authenticated catalog,
  /// allowing independently versioned engine families in packaged releases.
  public struct SealedEngineArtifactLocator: Sendable {
    public init() {}

    public func locate(for deviceIdentifier: String) throws -> PinnedAsahiEngineArchive? {
      guard let resources = Bundle.main.resourceURL else { return nil }
      return try locate(for: deviceIdentifier, resources: resources, now: Date())
    }

    func locate(for deviceIdentifier: String, resources: URL, now: Date) throws
      -> PinnedAsahiEngineArchive?
    {
      let release = resources.appendingPathComponent("Release")
      guard
        FileManager.default.fileExists(
          atPath: release.appendingPathComponent("catalog.json").path)
      else { return nil }
      let configuration = try InstallerReleaseConfigurationLocator().load(from: release)
      guard let documents = configuration.sealedCatalogDocuments else {
        throw InstallerReleaseConfigurationError.invalidCatalogSignature
      }
      let catalog = try AppleInstallerTrustCore().validateSupportCatalog(
        payload: documents.payload, signature: documents.signature,
        trustRoot: configuration.trustRoot, now: now)
      guard case .admitted(let record) = catalog.admission(for: deviceIdentifier),
        let engine = record.delivery?.engine
      else { return nil }
      let file = release.appendingPathComponent("Assets").appendingPathComponent(engine.fileName)
      guard FileManager.default.fileExists(atPath: file.path) else {
        throw ValidationEngineArtifactError.unavailable
      }
      return try PinnedAsahiEngineArchive(
        fileURL: file, expectedDigest: engine.expectedDigest,
        expectedSizeBytes: engine.expectedSizeBytes)
    }
  }
#endif
