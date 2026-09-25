import Darwin
import Foundation
import XCTest

@testable import OmarchyAppleInstallerTrustCore

final class HelperSubprocessLifetimeTests: XCTestCase {
  func testCancellationKillsTheChildGroupAndRejectsNewWork() throws {
    let lifetime = HelperSubprocessLifetime()
    lifetime.enable()
    let process = Process()
    let output = Pipe()
    process.executableURL = URL(fileURLWithPath: "/bin/sh")
    process.arguments = ["-c", "sleep 60 & echo $!; wait"]
    process.standardOutput = output
    try lifetime.run(process)
    defer { lifetime.stop() }
    var data = Data()
    while data.count < 32 {
      let byte = try XCTUnwrap(output.fileHandleForReading.read(upToCount: 1))
      guard !byte.isEmpty else { break }
      data.append(byte)
      if byte == Data([10]) { break }
    }
    let child = try XCTUnwrap(
      Int32(String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)))
    XCTAssertEqual(getpgid(process.processIdentifier), process.processIdentifier)
    XCTAssertEqual(getpgid(child), process.processIdentifier)
    lifetime.stop()
    process.waitUntilExit()
    XCTAssertEqual(process.terminationReason, .uncaughtSignal)
    XCTAssertEqual(process.terminationStatus, SIGKILL)
    for _ in 0..<100 {
      if kill(child, 0) != 0 { break }
      usleep(10_000)
    }
    XCTAssertEqual(kill(child, 0), -1, "A child of the engine survived cancellation")
    let next = Process()
    next.executableURL = URL(fileURLWithPath: "/usr/bin/true")
    XCTAssertThrowsError(try lifetime.run(next))
  }

  func testNormalExitDoesNotBlockLaterWork() throws {
    let lifetime = HelperSubprocessLifetime()
    lifetime.enable()
    defer { lifetime.stop() }
    for _ in 0..<2 {
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/usr/bin/true")
      try lifetime.run(process)
      process.waitUntilExit()
      XCTAssertEqual(process.terminationStatus, 0)
    }
  }
}
