#if os(macOS)
  import Foundation
  import XCTest
  @testable import OmarchyAppleInstallerTrustCore

  final class InstallerDownloadURLPolicyTests: XCTestCase {
    func testHTTPSIsDefaultAndHTTPNeedsExactPrivateOrigin() throws {
      let origin = try XCTUnwrap(URL(string: "http://100.64.0.1:8765"))
      let target = origin.appendingPathComponent("payload.zip")
      XCTAssertFalse(InstallerDownloadURLPolicy.allows(target, privateOrigin: nil))
      XCTAssertTrue(InstallerDownloadURLPolicy.allows(target, privateOrigin: origin))
      for value in [
        "http://100.64.0.2:8765/payload.zip", "http://100.64.0.1:8766/payload.zip",
        "http://user@100.64.0.1:8765/payload.zip", "http://100.64.0.1:8765/payload.zip#x",
      ] {
        XCTAssertFalse(
          InstallerDownloadURLPolicy.allows(
            try XCTUnwrap(URL(string: value)), privateOrigin: origin))
      }
      XCTAssertTrue(
        InstallerDownloadURLPolicy.allows(
          try XCTUnwrap(URL(string: "https://example.com/payload.zip"))))
    }

    func testMissingBundledOriginStillRejectsHTTPInPrivateBuilds() throws {
      XCTAssertFalse(
        InstallerDownloadURLPolicy.allows(
          try XCTUnwrap(URL(string: "http://100.64.0.1:8765/payload.zip"))))
    }

    func testRedirectCannotDowngradeHTTPS() throws {
      XCTAssertFalse(
        InstallerDownloadURLPolicy.allowsRedirect(
          from: URL(string: "https://example.com/payload.zip"),
          to: try XCTUnwrap(URL(string: "http://example.com/payload.zip"))))
      XCTAssertTrue(
        InstallerDownloadURLPolicy.allowsRedirect(
          from: URL(string: "https://example.com/payload.zip"),
          to: try XCTUnwrap(URL(string: "https://cdn.example.com/payload.zip"))))
    }
  }
#endif
