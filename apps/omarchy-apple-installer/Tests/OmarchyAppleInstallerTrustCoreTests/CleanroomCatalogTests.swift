import CryptoKit
import Foundation
import OmarchyAppleInstallerTrustCore
import XCTest

final class CleanroomCatalogTests: XCTestCase {
  private let now = Date(timeIntervalSince1970: 1_788_000_000)

  private func model() -> [String: Any] {
    var value: [String: Any] = [
      "deviceIdentifier": "apple,j713",
      "status": "enabled",
      "operation": "install",
      "engineFamily": "cleanroom", "executionScratchBytes": 8_589_934_592,
      "componentRevisions": Dictionary(
        uniqueKeysWithValues: ["m1n1", "u_boot", "grub", "linux", "enablement"].map {
          ($0, String(repeating: "a", count: 40))
        }),
      "downstreamRevision": String(repeating: "b", count: 40),
      "engineVersion": "v0.1.0-cleanroom.1",
      "evidenceRevision": "cleanroom-test-1",
    ]
    for (role, suffix) in [("engine", "tar.gz"), ("metadata", "json"), ("payload", "zip")] {
      value[role + "Digest"] = "sha256:" + String(repeating: "c", count: 64)
      value[role + "Artifact"] =
        [
          "sourceURL": "https://downloads.example.com/\(role).\(suffix)",
          "fileName": "\(role).\(suffix)",
          "sizeBytes": 1,
        ] as [String: Any]
    }
    return value
  }

  private func validate(_ model: [String: Any], schema: Int = 4) throws -> ValidatedSupportCatalog {
    let formatter = ISO8601DateFormatter()
    let payload = try JSONSerialization.data(withJSONObject: [
      "schemaVersion": schema,
      "sequence": 1,
      "issuedAt": formatter.string(from: now.addingTimeInterval(-60)),
      "expiresAt": formatter.string(from: now.addingTimeInterval(3600)),
      "models": [model],
    ])
    let key = Curve25519.Signing.PrivateKey()
    let publicKey = key.publicKey.rawRepresentation
    let digest = SHA256.hash(data: publicKey).map { String(format: "%02x", $0) }.joined()
    return try AppleInstallerTrustCore().validateSupportCatalog(
      payload: payload,
      signature: key.signature(for: payload),
      trustRoot: AppOwnedTrustRoot(
        rawRepresentation: publicKey, expectedFingerprint: "sha256:" + digest),
      now: now
    )
  }

  func testCustomSourceGraphNeedsNoInventedAsahiRelease() throws {
    let catalog = try validate(model())
    guard case .admitted(let record) = catalog.admission(for: "apple,j713") else {
      return XCTFail("Expected test cleanroom catalog admission")
    }
    XCTAssertEqual(record.engineFamily, "cleanroom")
    XCTAssertEqual(record.executionScratchBytes, 8_589_934_592)
    XCTAssertEqual(record.componentRevisions?.count, 5)
    XCTAssertNil(record.asahiInstallerTag)
    XCTAssertNil(record.asahiInstallerRevision)
    XCTAssertNil(record.asahiInstallerDataRevision)
    XCTAssertEqual(record.delivery?.payload.role, "payload")
    XCTAssertEqual(
      catalog.admission(for: "apple,j614s"), .unsupported(deviceIdentifier: "apple,j614s"))
  }

  func testCustomCatalogRejectsMissingMutableAndExtraComponents() {
    for revisions in [
      ["linux": String(repeating: "a", count: 40)],
      ["m1n1": "main", "u_boot": "main", "grub": "main", "linux": "main", "enablement": "main"],
      Dictionary(
        uniqueKeysWithValues: ["m1n1", "u_boot", "grub", "linux", "enablement", "extra"].map {
          ($0, String(repeating: "a", count: 40))
        }),
    ] {
      var value = model()
      value["componentRevisions"] = revisions
      XCTAssertThrowsError(try validate(value))
    }
  }

  func testCustomCatalogRejectsMixedProvenanceAndRepair() {
    for (field, value) in [
      ("engineFamily", "asahi"),
      ("asahiInstallerTag", "v0.9.0"),
      ("asahiInstallerRevision", String(repeating: "a", count: 40)),
      ("operation", "repair-installed-system"),
      ("repairManifestDigest", "sha256:" + String(repeating: "a", count: 64)),
    ] {
      var candidate = model()
      candidate[field] = value
      XCTAssertThrowsError(try validate(candidate))
    }
  }

  func testCleanroomRequiresPositiveIntegerScratchBudget() {
    var missing = model()
    missing.removeValue(forKey: "executionScratchBytes")
    XCTAssertThrowsError(try validate(missing))
    for budget: Any in [0, -1, 1.5, "8589934592", true] {
      var value = model()
      value["executionScratchBytes"] = budget
      XCTAssertThrowsError(try validate(value))
    }
  }

  func testLegacySchemaCannotAdmitCleanroomProvenance() {
    XCTAssertThrowsError(try validate(model(), schema: 2))
  }

  func testDisabledCustomModelStaysUnsupported() throws {
    var value = model()
    value["status"] = "disabled"
    let catalog = try validate(value)
    XCTAssertEqual(
      catalog.admission(for: "apple,j713"), .unsupported(deviceIdentifier: "apple,j713"))
  }
}
