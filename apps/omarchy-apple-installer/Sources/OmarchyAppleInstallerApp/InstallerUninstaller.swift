import AppKit
import Foundation

/// Launch through Terminal so the worker is independent of the installer
/// process tree it stops. The existing worker owns authentication and logs.
@MainActor
enum InstallerUninstaller {
  static func open() async throws {
    guard let script = Bundle.main.url(forResource: "uninstall-omarchy", withExtension: "command"),
      FileManager.default.isExecutableFile(atPath: script.path),
      let terminal = NSWorkspace.shared.urlForApplication(
        withBundleIdentifier: "com.apple.Terminal")
    else {
      throw LaunchError.missingResource
    }
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    _ = try await NSWorkspace.shared.open(
      [script], withApplicationAt: terminal, configuration: configuration)
  }

  private enum LaunchError: LocalizedError {
    case missingResource

    var errorDescription: String? {
      "The bundled uninstaller or Terminal is unavailable. Reinstall the installer package and try again."
    }
  }
}
