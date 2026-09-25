#if os(macOS)
  import Foundation

  /// Whether this app contains the helper needed for administrator approval.
  public enum InstallerHelperServiceStatus: Equatable, Sendable {
    /// The bundled helper is available; launching still requires approval.
    case enabled
    /// This app bundle is incomplete.
    case notInstalled
  }

  /// The injection seam. The shipping controller checks the filesystem; tests
  /// supply a fake that returns a fixed status.
  public protocol InstallerHelperServiceControlling: Sendable {
    var status: InstallerHelperServiceStatus { get }
  }

  public struct InstallerHelperServiceManager: Sendable {
    private let controller: any InstallerHelperServiceControlling

    public init(controller: any InstallerHelperServiceControlling) {
      self.controller = controller
    }

    public static func bundledHelper() -> Self {
      Self(controller: BundledHelperController())
    }

    public var status: InstallerHelperServiceStatus {
      controller.status
    }
  }

  private struct BundledHelperController:
    InstallerHelperServiceControlling
  {
    var status: InstallerHelperServiceStatus {
      TemporaryInstallerHelper.bundledHelperAvailable() ? .enabled : .notInstalled
    }
  }
#endif
