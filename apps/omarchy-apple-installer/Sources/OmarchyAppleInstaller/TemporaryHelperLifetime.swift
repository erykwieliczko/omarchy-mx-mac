#if os(macOS)
  import Foundation

  /// Explicit session finish drains RPCs; owner death cancels them immediately.
  public final class TemporaryHelperLifetime: @unchecked Sendable {
    private let lock = NSLock()
    private var active = 0
    private var draining = false
    private var cleaned = false
    private var handshaken = false
    private let cleanup: @Sendable () -> Void

    public init(cleanup: @escaping @Sendable () -> Void) {
      self.cleanup = cleanup
    }

    public func handshake() -> Bool {
      lock.withLock {
        guard !draining else { return false }
        handshaken = true
        return true
      }
    }

    /// Call synchronously before scheduling the task that performs the RPC.
    public func begin() -> Bool {
      lock.withLock {
        guard !draining else { return false }
        active += 1
        return true
      }
    }

    public func end() {
      let shouldClean = lock.withLock {
        precondition(active > 0)
        active -= 1
        return claimCleanup()
      }
      if shouldClean { cleanup() }
    }

    public func finish() {
      let shouldClean = lock.withLock {
        draining = true
        return claimCleanup()
      }
      if shouldClean { cleanup() }
    }

    public func ownerExited() {
      let shouldClean = lock.withLock {
        draining = true
        guard !cleaned else { return false }
        cleaned = true
        return true
      }
      if shouldClean { cleanup() }
    }

    public func handshakeExpired() {
      let shouldClean = lock.withLock {
        if !handshaken { draining = true }
        return claimCleanup()
      }
      if shouldClean { cleanup() }
    }

    private func claimCleanup() -> Bool {
      guard draining, active == 0, !cleaned else { return false }
      cleaned = true
      return true
    }
  }
#endif
