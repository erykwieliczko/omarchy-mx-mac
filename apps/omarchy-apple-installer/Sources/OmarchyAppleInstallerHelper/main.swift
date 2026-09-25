import Darwin
import Foundation
import OmarchyAppleInstallerTrustCore

private enum HelperBootstrapError: Error {
  case rootRequired
  case missingClientRequirement
  case unsafeWorkingDirectory
  case invalidSession
}

private func prepareWorkingDirectory() throws -> URL {
  let path = InstallerProductIdentity.helperWorkingDirectory
  var status = stat()
  if lstat(path, &status) == 0 {
    guard (status.st_mode & S_IFMT) == S_IFDIR,
      status.st_uid == 0,
      status.st_mode & 0o077 == 0
    else {
      throw HelperBootstrapError.unsafeWorkingDirectory
    }
    return URL(fileURLWithPath: path, isDirectory: true)
  }
  guard errno == ENOENT,
    mkdir(path, S_IRWXU) == 0,
    lstat(path, &status) == 0,
    (status.st_mode & S_IFMT) == S_IFDIR,
    status.st_uid == 0,
    status.st_mode & 0o077 == 0
  else {
    throw HelperBootstrapError.unsafeWorkingDirectory
  }
  return URL(fileURLWithPath: path, isDirectory: true)
}

private func terminate(_ error: any Error) -> Never {
  let message = "Omarchy installer helper failed: \(error)\n"
  try? FileHandle.standardError.write(contentsOf: Data(message.utf8))
  exit(EX_CONFIG)
}

nonisolated(unsafe) private var sessionDirectory: String?

private func cleanUpSession() {
  guard let directory = sessionDirectory else { return }
  try? FileManager.default.removeItem(atPath: directory)
  let bootout = Process()
  bootout.executableURL = URL(fileURLWithPath: "/bin/launchctl")
  bootout.arguments = ["bootout", "system/" + InstallerProductIdentity.helperMachServiceName]
  if (try? bootout.run()) != nil { bootout.waitUntilExit() }
}

umask(0o077)

do {
  guard geteuid() == 0 else {
    throw HelperBootstrapError.rootRequired
  }
  let developmentOverride = try DevelopmentMachineOverride.fromHelperEnvironment()
  let temporary = CommandLine.arguments == [CommandLine.arguments[0], "--temporary-session"]
  guard temporary || CommandLine.arguments.count == 1 else {
    throw HelperBootstrapError.invalidSession
  }
  var ownerPID: Int32?
  var ownerUID: UInt32?
  var lifetime: TemporaryHelperLifetime?
  var ownerExit: (any DispatchSourceProcess)?
  if temporary {
    HelperSubprocessLifetime.shared.enable()
    let environment = ProcessInfo.processInfo.environment
    guard let pid = environment["OMARCHY_HELPER_OWNER_PID"].flatMap(Int32.init), pid > 1,
      let uid = environment["OMARCHY_HELPER_OWNER_UID"].flatMap(UInt32.init), uid != 0,
      let directory = environment["OMARCHY_HELPER_SESSION_DIRECTORY"],
      directory.hasPrefix("/private/var/run/omarchy-installer-helper."),
      UUID(
        uuidString: String(directory.dropFirst("/private/var/run/omarchy-installer-helper.".count)))
        != nil
    else { throw HelperBootstrapError.invalidSession }
    var status = stat()
    guard lstat(directory, &status) == 0, status.st_uid == 0,
      status.st_mode & S_IFMT == S_IFDIR, status.st_mode & 0o777 == 0o700,
      CommandLine.arguments[0] == directory + (developmentOverride?.helperRelativePath ?? "/helper")
    else { throw HelperBootstrapError.invalidSession }
    sessionDirectory = directory
    ownerPID = pid
    ownerUID = uid
    let sessionLifetime = TemporaryHelperLifetime {
      HelperSubprocessLifetime.shared.stop()
      // Allow the last XPC reply to leave before removing the job. No new RPC
      // can enter after the lifecycle gate transitions to draining.
      DispatchQueue.global().asyncAfter(deadline: .now() + 0.25) {
        cleanUpSession()
        exit(0)
      }
    }
    lifetime = sessionLifetime
    let source = DispatchSource.makeProcessSource(
      identifier: pid, eventMask: .exit, queue: .global())
    // DispatchSource does not infer Sendable: inheriting the top-level main
    // actor would trap when launchd reports owner exit on this global queue.
    source.setEventHandler { @Sendable in sessionLifetime.ownerExited() }
    source.activate()
    ownerExit = source
    if kill(pid, 0) != 0 { sessionLifetime.ownerExited() }
    DispatchQueue.global().asyncAfter(deadline: .now() + 30) {
      sessionLifetime.handshakeExpired()
    }
  }
  guard
    let clientRequirement = ProcessInfo.processInfo.environment[
      InstallerProductIdentity.clientRequirementEnvironmentVariable
    ], !clientRequirement.isEmpty
  else {
    throw HelperBootstrapError.missingClientRequirement
  }
  let workingDirectory = try prepareWorkingDirectory()
  // Also excludes another copy invoked directly, outside launchd. Keep the
  // lock inode and installation journals after this process exits.
  let lease = try InstallerAppInstanceLease.acquire(
    at: workingDirectory.appendingPathComponent("helper.lock"))
  if let developmentOverride {
    guard temporary else { throw HelperBootstrapError.invalidSession }
    _ = try developmentOverride.applying(to: AppleSiliconHostInspector().inspect())
  }
  let server = ClosedEngineHelperServer(
    workingDirectory: workingDirectory,
    executor: PinnedAsahiEngineExecutor(developmentOverride: developmentOverride),
    developmentOverride: developmentOverride
  )
  let delegate = try AuthenticatedEngineXPCListenerDelegate(
    clientCodeSigningRequirement: clientRequirement,
    server: server, ownerPID: ownerPID, ownerUID: ownerUID, lifetime: lifetime
  )
  let listener = NSXPCListener(
    machServiceName: InstallerProductIdentity.helperMachServiceName
  )
  listener.delegate = delegate
  listener.resume()
  withExtendedLifetime((delegate, lease, ownerExit, lifetime)) {
    RunLoop.current.run()
  }
} catch {
  cleanUpSession()
  terminate(error)
}
