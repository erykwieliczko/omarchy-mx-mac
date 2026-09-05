#if os(macOS)
  import Darwin
  import Foundation

  /// The root helper independently admits artifacts using its installed,
  /// root-owned release. Caller process identity never supplies this policy.
  public struct HelperReleasePolicy: Sendable {
    let configuration: InstallerReleaseConfiguration

    public static func loadInstalled(executableURL: URL) throws -> HelperReleasePolicy {
      let resources = executableURL.deletingLastPathComponent()
      let contents = resources.deletingLastPathComponent()
      let app = contents.deletingLastPathComponent()
      guard resources.lastPathComponent == "Resources", contents.lastPathComponent == "Contents",
        app.pathExtension == "app"
      else { throw EngineHandoffImportError.unsafeSourceDirectory }
      let release = resources.appendingPathComponent("Release")
      for directory in [app, contents, resources, release] {
        try requireRootOwned(directory, type: S_IFDIR)
      }
      try requireRootOwned(contents.appendingPathComponent("Info.plist"), type: S_IFREG)
      for name in ["release.json", "trust-root.ed25519.pub", "catalog.json", "catalog.json.sig"] {
        try requireRootOwned(release.appendingPathComponent(name), type: S_IFREG)
      }
      let policy = HelperReleasePolicy(
        configuration: try InstallerReleaseConfigurationLocator().load(from: release))
      _ = try policy.validatedCatalog(now: Date())
      return policy
    }

    func validatedCatalog(now: Date) throws -> ValidatedSupportCatalog {
      guard let documents = configuration.sealedCatalogDocuments else {
        throw InstallerReleaseConfigurationError.invalidCatalogSignature
      }
      return try AppleInstallerTrustCore().validateSupportCatalog(
        payload: documents.payload, signature: documents.signature,
        trustRoot: configuration.trustRoot, now: now)
    }

    private static func requireRootOwned(_ url: URL, type: mode_t) throws {
      var status = stat()
      guard lstat(url.path, &status) == 0, status.st_uid == 0,
        status.st_mode & S_IFMT == type, status.st_mode & 0o022 == 0
      else { throw EngineHandoffImportError.unsafeSourceDirectory }
    }
  }
#endif
