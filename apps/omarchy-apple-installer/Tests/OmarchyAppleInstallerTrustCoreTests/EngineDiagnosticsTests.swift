#if os(macOS)
  import Foundation
  import XCTest
  @testable import OmarchyAppleInstallerTrustCore

  final class EngineDiagnosticsTests: XCTestCase {
    func testRedactsPasswordsSplitAcrossWrites() {
      var redactor = DiagnosticRedactor(secret: Data("secret-password".utf8))
      var output = redactor.append(Data("before secret-".utf8))
      output.append(redactor.append(Data("password after secret-password".utf8)))
      output.append(redactor.append(Data(), final: true))
      XCTAssertEqual(String(decoding: output, as: UTF8.self), "before [REDACTED] after [REDACTED]")
    }

    func testOutputSurvivesFailureIsBoundedAndPrivate() throws {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
      defer { try? FileManager.default.removeItem(at: root) }
      let diagnostics = try EngineDiagnostics(
        parent: root, operation: "plan", standardInput: Data("password\n".utf8))
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/bin/bash")
      process.arguments = [
        "-c", "head -c 2097152 /dev/zero; printf 'Traceback: password failed\\n' >&2; exit 1",
      ]
      try diagnostics.attach(to: process)
      try process.run()
      process.waitUntilExit()
      let detail = diagnostics.finish(
        process: process, bundle: root, transcript: root.appendingPathComponent("absent"))
      XCTAssertTrue(detail.contains("Traceback: [REDACTED] failed"))
      for name in ["stdout.log", "stderr.log", "run.log", "result.log"] {
        let file = diagnostics.directory.appendingPathComponent(name)
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertLessThanOrEqual(try Data(contentsOf: file).count, EngineDiagnosticStream.limit)
      }
    }

    func testInheritedPipeCannotHideEngineExitIndefinitely() throws {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
      defer { try? FileManager.default.removeItem(at: root) }
      let diagnostics = try EngineDiagnostics(
        parent: root, operation: "inspect", standardInput: nil)
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/bin/bash")
      process.arguments = ["-c", "/bin/sleep 4 & echo failure >&2; exit 1"]
      try diagnostics.attach(to: process)
      try process.run()
      process.waitUntilExit()
      let start = Date()
      _ = diagnostics.finish(
        process: process, bundle: root, transcript: root.appendingPathComponent("absent"))
      XCTAssertLessThan(Date().timeIntervalSince(start), 3.5)
    }

    func testHelperBridgePreservesDiagnosticDetails() {
      let failure = EngineDiagnosticFailure(
        operation: "install", logDirectory: "/var/db/test/diagnostics/run",
        detail: "Exit 1. Actual traceback")
      let bridged = EngineXPCErrorBridge.serviceError(for: failure)
      XCTAssertEqual(EngineXPCErrorBridge.submissionError(bridged), .engineFailed(failure))
    }
  }
#endif
