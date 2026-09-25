#if os(macOS)
  import Darwin
  import Foundation

  /// Enabled only inside a temporary helper. Foundation gives each child its
  /// own process group; launchd's cleanup of the helper alone cannot stop it.
  public final class HelperSubprocessLifetime: @unchecked Sendable {
    public static let shared = HelperSubprocessLifetime()
    private let lock = NSLock()
    private var enabled = false
    private var stopped = false
    private var children: [ObjectIdentifier: Process] = [:]

    public init() {}

    public func enable() {
      lock.withLock { enabled = true }
    }

    public func run(_ process: Process) throws {
      try lock.withLock {
        guard !stopped else { throw CancellationError() }
        if enabled {
          let previous = process.terminationHandler
          process.terminationHandler = { [weak self] child in
            self?.didExit(child)
            previous?(child)
          }
        }
        try process.run()
        if enabled { children[ObjectIdentifier(process)] = process }
      }
    }

    public func stop() {
      lock.withLock {
        stopped = true
        for process in children.values { stopGroup(process) }
        children.removeAll()
      }
    }

    private func didExit(_ process: Process) {
      lock.withLock {
        if children.removeValue(forKey: ObjectIdentifier(process)) != nil {
          // A failed engine must not leave its own disk tools running either.
          stopGroup(process)
        }
      }
    }

    private func stopGroup(_ process: Process) {
      let pid = process.processIdentifier
      guard pid > 1, pid != getpgrp() else { return }
      // The owner's death is cancellation, including active operations. Stop
      // the entire group immediately; preserve journals for recovery checks.
      _ = kill(-pid, SIGKILL)
    }
  }
#endif
