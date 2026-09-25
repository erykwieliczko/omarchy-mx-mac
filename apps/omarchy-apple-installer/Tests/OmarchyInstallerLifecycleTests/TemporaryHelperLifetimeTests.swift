import Foundation
import XCTest

@testable import OmarchyAppleInstallerTrustCore

final class TemporaryHelperLifetimeTests: XCTestCase {
  private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    func increment() { lock.withLock { count += 1 } }
    var value: Int { lock.withLock { count } }
  }

  func testExplicitFinishDrainsEveryAdmittedOperationExactlyOnce() {
    let counter = Counter()
    let lifetime = TemporaryHelperLifetime { counter.increment() }
    XCTAssertTrue(lifetime.handshake())
    XCTAssertTrue(lifetime.begin())
    XCTAssertTrue(lifetime.begin())
    lifetime.finish()
    XCTAssertFalse(lifetime.begin())
    XCTAssertFalse(lifetime.handshake())
    lifetime.end()
    XCTAssertEqual(counter.value, 0)
    lifetime.end()
    lifetime.finish()
    lifetime.handshakeExpired()
    XCTAssertEqual(counter.value, 1)
  }

  func testOwnerExitCancelsWithoutWaitingForActiveOperations() {
    let counter = Counter()
    let lifetime = TemporaryHelperLifetime { counter.increment() }
    XCTAssertTrue(lifetime.handshake())
    XCTAssertTrue(lifetime.begin())
    lifetime.ownerExited()
    XCTAssertEqual(counter.value, 1)
    XCTAssertFalse(lifetime.begin())
    XCTAssertFalse(lifetime.handshake())
    lifetime.end()
    lifetime.ownerExited()
    lifetime.finish()
    XCTAssertEqual(counter.value, 1)
  }

  func testHandshakeTimeoutAndSuccessfulHandshake() {
    let abandoned = Counter()
    let first = TemporaryHelperLifetime { abandoned.increment() }
    first.handshakeExpired()
    XCTAssertEqual(abandoned.value, 1)
    XCTAssertFalse(first.begin())
    let connected = Counter()
    let second = TemporaryHelperLifetime { connected.increment() }
    XCTAssertTrue(second.handshake())
    second.handshakeExpired()
    XCTAssertEqual(connected.value, 0)
    second.finish()
    XCTAssertEqual(connected.value, 1)
  }

  func testConcurrentAdmissionAndShutdown() {
    let counter = Counter()
    let lifetime = TemporaryHelperLifetime { counter.increment() }
    DispatchQueue.concurrentPerform(iterations: 1000) { index in
      if index == 500 { lifetime.finish() }
      if lifetime.begin() { lifetime.end() }
    }
    lifetime.finish()
    XCTAssertEqual(counter.value, 1)
    XCTAssertFalse(lifetime.begin())
  }
}
