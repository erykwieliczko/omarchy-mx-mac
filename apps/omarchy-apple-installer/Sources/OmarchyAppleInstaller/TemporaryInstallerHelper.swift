#if os(macOS)
  import Darwin
  import Foundation
  import Security

  /// These errors can only be raised before an operation is submitted.
  public enum InstallerHelperBootstrapError: Error, Equatable, Sendable {
    case authorizationCancelled
    case unavailable
    case alreadyRegistered
    case startupFailed

    public var message: String {
      switch self {
      case .authorizationCancelled:
        "Administrator approval was cancelled. No operation was submitted."
      case .unavailable:
        "The app’s bundled helper could not be verified. Replace the app with a fresh copy. No operation was submitted."
      case .alreadyRegistered:
        "Another installation service is registered. Finish any active operation and retire the older installed helper before trying again. No operation was submitted."
      case .startupFailed:
        "The temporary installation service could not start. No operation was submitted. Close the app and try again."
      }
    }
  }

  public struct InstallerHelperConnection: Equatable, Sendable {
    public let serviceName: String
    public let requirement: String
    let sessionID: UUID
    let developmentOverride: DevelopmentMachineOverride?

    public func submitter(
      journalProgress: (@Sendable (Data) -> Void)? = nil
    ) throws -> AuthenticatedEngineXPCSubmitter {
      try AuthenticatedEngineXPCSubmitter(
        machServiceName: serviceName, helperCodeSigningRequirement: requirement,
        journalProgress: journalProgress)
    }
  }

  /// One temporary privileged session shared by install, install-conf and removal.
  public actor TemporaryInstallerHelper {
    public static let shared = TemporaryInstallerHelper()
    private var connection: InstallerHelperConnection?
    private var retiring = false
    private var finishing: Task<Bool, Never>?
    private var starting: Task<InstallerHelperConnection, any Error>?

    public static func bundledHelperAvailable(in bundle: URL = Bundle.main.bundleURL) -> Bool {
      FileManager.default.isExecutableFile(atPath: helperURL(in: bundle).path)
    }

    public func start(developmentOverride: DevelopmentMachineOverride? = nil) async throws
      -> InstallerHelperConnection
    {
      // Operations are exclusive. A second caller must never receive a
      // session whose startup or shutdown is still in progress.
      guard !retiring, starting == nil else {
        throw InstallerHelperBootstrapError.startupFailed
      }
      if let connection {
        guard connection.developmentOverride == developmentOverride else {
          throw InstallerHelperBootstrapError.startupFailed
        }
        return connection
      }
      let bundle = Bundle.main.bundleURL
      let task = Task.detached { () async throws -> InstallerHelperConnection in
        let plan = try TemporaryHelperLaunchPlan(
          bundle: bundle, developmentOverride: developmentOverride)
        try plan.authorizeAndLaunch()
        do {
          try await plan.connection.submitter().ping()
        } catch {
          try? await plan.connection.submitter().finishSession()
          // The owner-exit watcher remains the final cleanup path if even
          // this authenticated shutdown request cannot be delivered.
          throw InstallerHelperBootstrapError.startupFailed
        }
        return plan.connection
      }
      starting = task
      do {
        let result = try await task.value
        connection = result
        starting = nil
        return result
      } catch {
        starting = nil
        throw error
      }
    }

    public func finish(_ session: InstallerHelperConnection) async {
      if let finishing {
        _ = await finishing.value
        return
      }
      guard connection == session else { return }
      retiring = true
      let task = Task.detached {
        try? await session.submitter().finishSession()
        for _ in 0..<50 {
          let probe = Process()
          probe.executableURL = URL(fileURLWithPath: "/bin/launchctl")
          probe.arguments = ["print", "system/" + session.serviceName]
          probe.standardOutput = FileHandle.nullDevice
          probe.standardError = FileHandle.nullDevice
          do { try probe.run() } catch { return false }
          probe.waitUntilExit()
          if probe.terminationStatus != 0 { return true }
          try? await Task.sleep(for: .milliseconds(100))
        }
        return false
      }
      finishing = task
      let finished = await task.value
      finishing = nil
      if finished {
        connection = nil
        retiring = false
      }
    }

    static func helperURL(in bundle: URL) -> URL {
      bundle.appendingPathComponent("Contents/Resources/omarchy-apple-installer-helper")
    }
  }

  struct TemporaryHelperLaunchPlan: Sendable {
    let bundle: URL
    let helper: URL
    let clientRequirement: String
    let connection: InstallerHelperConnection
    let ownerPID: Int32
    let ownerUID: UInt32
    let directory: String

    init(bundle: URL, developmentOverride: DevelopmentMachineOverride? = nil) throws {
      let helper = TemporaryInstallerHelper.helperURL(in: bundle)
      try self.init(
        bundle: bundle, helper: helper,
        clientHash: Self.codeHash(at: bundle), helperHash: Self.codeHash(at: helper),
        ownerPID: getpid(), ownerUID: getuid(), sessionID: UUID(),
        developmentOverride: developmentOverride)
    }

    init(
      bundle: URL, helper: URL, clientHash: String, helperHash: String,
      ownerPID: Int32, ownerUID: UInt32, sessionID: UUID,
      developmentOverride: DevelopmentMachineOverride? = nil
    ) throws {
      guard ownerPID > 1, ownerUID != 0,
        Self.isHash(clientHash), Self.isHash(helperHash),
        bundle.isFileURL, helper.isFileURL
      else { throw InstallerHelperBootstrapError.unavailable }
      self.bundle = bundle
      self.helper = helper
      self.ownerPID = ownerPID
      self.ownerUID = ownerUID
      directory = "/private/var/run/omarchy-installer-helper.\(sessionID.uuidString.lowercased())"
      clientRequirement =
        "identifier \"com.omarchy.mx.installer\" and cdhash H\"\(clientHash)\""
      connection = InstallerHelperConnection(
        serviceName: InstallerProductIdentity.helperMachServiceName,
        requirement:
          "identifier \"\(InstallerProductIdentity.helperIdentifier)\" and cdhash H\"\(helperHash)\"",
        sessionID: sessionID, developmentOverride: developmentOverride)
    }

    private static func isHash(_ value: String) -> Bool {
      value.count == 40
        && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    private static func codeHash(at url: URL) throws -> String {
      var code: SecStaticCode?
      guard SecStaticCodeCreateWithPath(url as CFURL, [], &code) == errSecSuccess,
        let code,
        SecStaticCodeCheckValidity(code, SecCSFlags(rawValue: kSecCSCheckAllArchitectures), nil)
          == errSecSuccess
      else { throw InstallerHelperBootstrapError.unavailable }
      var information: CFDictionary?
      guard
        SecCodeCopySigningInformation(
          code, SecCSFlags(rawValue: kSecCSSigningInformation), &information)
          == errSecSuccess,
        let information = information as? [String: Any],
        let digest = information[kSecCodeInfoUnique as String] as? Data
      else { throw InstallerHelperBootstrapError.unavailable }
      return digest.map { String(format: "%02x", $0) }.joined()
    }

    static func shellQuote(_ value: String) -> String {
      "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    func shellCommand() throws -> String {
      var environment = [
        InstallerProductIdentity.clientRequirementEnvironmentVariable: clientRequirement,
        "OMARCHY_HELPER_OWNER_PID": String(ownerPID),
        "OMARCHY_HELPER_OWNER_UID": String(ownerUID),
        "OMARCHY_HELPER_SESSION_DIRECTORY": directory,
      ]
      environment.merge(connection.developmentOverride?.helperEnvironment ?? [:]) { _, new in new }
      let needsNeoResources = connection.developmentOverride?.profile.id == "apple,j700"
      let stagedApp = directory + "/Installer.app"
      let stagedHelper =
        directory + (connection.developmentOverride?.helperRelativePath ?? "/helper")
      let plist: [String: Any] = [
        "Label": connection.serviceName,
        "ProgramArguments": [stagedHelper, "--temporary-session"],
        "MachServices": [connection.serviceName: true],
        "RunAtLoad": true,
        "UserName": "root",
        "EnvironmentVariables": environment,
      ]
      let encoded = try PropertyListSerialization.data(
        fromPropertyList: plist, format: .xml, options: 0
      ).base64EncodedString()
      let quote = Self.shellQuote
      let stageCommand =
        needsNeoResources
        ? """
        /usr/bin/ditto \(quote(bundle.path)) \(quote(stagedApp))
        /usr/sbin/chown -R root:wheel \(quote(stagedApp))
        /bin/chmod -R go-w \(quote(stagedApp))
        /usr/bin/codesign --verify --deep --strict -R=\(quote(clientRequirement)) \(quote(stagedApp))
        """
        : "/usr/bin/install -o root -g wheel -m 700 \(quote(helper.path)) \(quote(stagedHelper))"
      // Only immutable command text is elevated. All paths are quoted; the
      // staged executable is verified before launch, inside a root-only dir.
      return """
        set -eu
        umask 077
        if /bin/launchctl print system/\(connection.serviceName) >/dev/null 2>&1; then
          echo OMARCHY_HELPER_ALREADY_REGISTERED >&2
          exit 73
        fi
        /usr/bin/codesign --verify --strict -R=\(quote(clientRequirement)) \(quote(bundle.path))
        /bin/mkdir -m 700 \(quote(directory))
        trap '/bin/rm -rf \(quote(directory))' EXIT
        \(stageCommand)
        /usr/bin/codesign --verify --strict -R=\(quote(connection.requirement)) \(quote(stagedHelper))
        /usr/bin/printf '%s' \(quote(encoded)) | /usr/bin/base64 -D > \(quote(directory + "/daemon.plist"))
        /bin/launchctl bootstrap system \(quote(directory + "/daemon.plist"))
        trap - EXIT
        """
    }

    func authorizeAndLaunch() throws {
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
      process.arguments = [
        "-e",
        "on run argv\ndo shell script (item 1 of argv) with administrator privileges\nend run",
        try shellCommand(),
      ]
      let errors = Pipe()
      process.standardError = errors
      process.standardOutput = FileHandle.nullDevice
      do { try process.run() } catch { throw InstallerHelperBootstrapError.startupFailed }
      let output = errors.fileHandleForReading.readDataToEndOfFile()
      process.waitUntilExit()
      guard process.terminationStatus == 0 else {
        let detail = String(decoding: output, as: UTF8.self)
        if detail.contains("(-128)") { throw InstallerHelperBootstrapError.authorizationCancelled }
        if detail.contains("OMARCHY_HELPER_ALREADY_REGISTERED") {
          throw InstallerHelperBootstrapError.alreadyRegistered
        }
        throw InstallerHelperBootstrapError.startupFailed
      }
    }
  }
#endif
