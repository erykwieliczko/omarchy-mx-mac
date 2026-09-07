import Foundation
import XCTest

@testable import OmarchyAppleInstallerTrustCore

final class InstallerAllocationRecommendationTests: XCTestCase {
  private let gib: UInt64 = 1_073_741_824

  func testImageFitsOnSmallDiskBelowRecommendedSize() throws {
    let resize = candidate(
      kind: "resize", source: "disk0s2", length: 245_054_767_104,
      minimumInstall: 39_531_315_200, minimumContainer: 161_512_161_280)
    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([resize]), workingSpaceBytes: 29_022_805_100,
      targetBytes: 45_000_000_000)
    XCTAssertEqual(recommendation.minimumLengthBytes, 39_531_315_200)
    XCTAssertGreaterThan(recommendation.maximumLengthBytes, 53_000_000_000)
    XCTAssertLessThan(recommendation.maximumLengthBytes, 54_000_000_000)
    XCTAssertLessThanOrEqual(recommendation.requestedLengthBytes, 45_000_000_000)
    XCTAssertGreaterThan(recommendation.requestedLengthBytes, 44_999_000_000)
    XCTAssertNoThrow(
      try PinnedAsahiPlanRequest(
        inventory: inventory([resize]), candidate: resize,
        requestedLengthBytes: recommendation.requestedLengthBytes))
  }

  func testPrefersFreeExtentAndBalancedTarget() throws {
    let resize = candidate(
      kind: "resize",
      source: "disk0s2",
      length: 600 * gib,
      minimumInstall: 64 * gib,
      minimumContainer: 200 * gib
    )
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 300 * gib,
      minimumInstall: 64 * gib
    )

    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([resize, free]), workingSpaceBytes: 0
    )

    XCTAssertEqual(recommendation.candidate, free)
    XCTAssertEqual(recommendation.requestedLengthBytes, 128 * gib)
  }

  func testClampsToAlignedMaximumWithoutViolatingMinimum() throws {
    let unit = PinnedAsahiPlanRequest.allocationUnitBytes
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 90 * gib + 333,
      minimumInstall: 64 * gib + 1
    )

    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([free]), workingSpaceBytes: 0
    )

    XCTAssertEqual(recommendation.requestedLengthBytes % unit, 0)
    XCTAssertLessThanOrEqual(
      recommendation.requestedLengthBytes,
      free.lengthBytes
    )
    XCTAssertGreaterThanOrEqual(
      recommendation.requestedLengthBytes,
      free.minimumInstallBytes
    )
  }

  func testResizeRecommendationToleratesSmallIncreaseInAPFSMinimum() throws {
    let resize = candidate(
      kind: "resize", source: "disk0s2", length: 240 * gib,
      minimumInstall: 64 * gib, minimumContainer: 120 * gib)
    let proposed = try InstallerAllocationRecommendation(
      inventory: inventory([resize]), workingSpaceBytes: 0)
    XCTAssertEqual(proposed.requestedLengthBytes, 119 * gib)

    let refreshed = candidate(
      kind: "resize", source: "disk0s2", length: resize.lengthBytes,
      minimumInstall: resize.minimumInstallBytes,
      minimumContainer: resize.minimumContainerBytes + 1_048_576)
    XCTAssertNoThrow(
      try PinnedAsahiPlanRequest(
        inventory: inventory([refreshed]), candidate: refreshed,
        requestedLengthBytes: proposed.requestedLengthBytes))
  }

  func testExplicitOversizedResizeTargetAlsoKeepsHeadroom() throws {
    let resize = candidate(
      kind: "resize", source: "disk0s2", length: 240 * gib,
      minimumInstall: 64 * gib, minimumContainer: 120 * gib)
    let proposed = try InstallerAllocationRecommendation(
      inventory: inventory([resize]), workingSpaceBytes: 0, targetBytes: 200 * gib)
    XCTAssertEqual(proposed.requestedLengthBytes, 119 * gib)
    let smaller = try InstallerAllocationRecommendation(
      inventory: inventory([resize]), workingSpaceBytes: 0, targetBytes: 80 * gib)
    XCTAssertEqual(smaller.requestedLengthBytes, 80 * gib)
  }

  func testResizeMustFitMinimumAndHeadroom() throws {
    let tight = candidate(
      kind: "resize", source: "disk0s2", length: 128 * gib,
      minimumInstall: 64 * gib, minimumContainer: 64 * gib)
    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(inventory: inventory([tight]), workingSpaceBytes: 0))
    let exact = candidate(
      kind: "resize", source: "disk0s2", length: 129 * gib,
      minimumInstall: 64 * gib, minimumContainer: 64 * gib)
    XCTAssertEqual(
      try InstallerAllocationRecommendation(inventory: inventory([exact]), workingSpaceBytes: 0)
        .requestedLengthBytes,
      64 * gib)
  }

  func testFailsClosedWhenNoCandidateCanMeetMinimum() {
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 32 * gib,
      minimumInstall: 64 * gib
    )

    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(inventory: inventory([free]), workingSpaceBytes: 0)
    ) {
      XCTAssertEqual(
        $0 as? InstallerAllocationRecommendationError,
        .insufficientSpace(availableBytes: 32 * gib, requiredBytes: 64 * gib, workingSpaceBytes: 0)
      )
    }
  }

  func testReplaceOnlyInventoryFailsClosed() {
    let replace = candidate(
      kind: "replace",
      source: "disk0s3",
      length: 300 * gib,
      minimumInstall: 64 * gib,
      identityDigest: "sha256:" + String(repeating: "9", count: 64)
    )

    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(inventory: inventory([replace]), workingSpaceBytes: 0)
    ) {
      XCTAssertEqual(
        $0 as? InstallerAllocationRecommendationError,
        .noEligibleCandidate
      )
    }
  }

  func testReplaceCandidateIsNeverAutoSelected() throws {
    let replace = candidate(
      kind: "replace",
      source: "disk0s2",
      length: 600 * gib,
      minimumInstall: 64 * gib,
      identityDigest: "sha256:" + String(repeating: "9", count: 64)
    )
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 300 * gib,
      minimumInstall: 64 * gib
    )

    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([replace, free]), workingSpaceBytes: 0
    )

    XCTAssertEqual(recommendation.candidate, free)
  }

  func testResizeSurvivesBothHandoffCopiesAndEngineWorkspace() throws {
    let installer = try releaseRecord()
    let workspace = try InstallerAllocationRecommendation.workingSpaceBytes(for: installer)
    XCTAssertEqual(workspace, 2 * (23_337_845 + 2_664 + 5_927_464_429) + 8 * gib)
    let resize = candidate(
      kind: "resize", source: "disk0s2", length: 245_107_195_904,
      minimumInstall: 76_562_825_216, minimumContainer: 120_740_380_672)
    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([resize]), workingSpaceBytes: workspace)
    let afterPreparation = candidate(
      kind: "resize", source: resize.sourceIdentifier, length: resize.lengthBytes,
      minimumInstall: resize.minimumInstallBytes,
      minimumContainer: resize.minimumContainerBytes + workspace + 1_048_576)
    XCTAssertNoThrow(
      try PinnedAsahiPlanRequest(
        inventory: inventory([afterPreparation]), candidate: afterPreparation,
        requestedLengthBytes: recommendation.requestedLengthBytes))
    XCTAssertGreaterThanOrEqual(recommendation.requestedLengthBytes, resize.minimumInstallBytes)
    let tooMuchGrowth = candidate(
      kind: "resize", source: resize.sourceIdentifier, length: resize.lengthBytes,
      minimumInstall: resize.minimumInstallBytes,
      minimumContainer: resize.lengthBytes - recommendation.requestedLengthBytes + 1)
    XCTAssertThrowsError(
      try PinnedAsahiPlanRequest(
        inventory: inventory([tooMuchGrowth]), candidate: tooMuchGrowth,
        requestedLengthBytes: recommendation.requestedLengthBytes))
  }

  func testApplePreparationPeakDoesNotRemainReservedAfterResize() throws {
    let resize = candidate(
      kind: "resize", source: "disk0s2", length: 245_107_195_904,
      minimumInstall: 76_562_825_216, minimumContainer: 97_372_445_081)
    let installer = try releaseRecord(payloadBytes: 3_750_452_703, scratchBytes: 8 * gib)
    let workspace = try InstallerAllocationRecommendation.workingSpaceBytes(for: installer)
    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([resize]), workingSpaceBytes: workspace)
    XCTAssertGreaterThan(recommendation.requestedLengthBytes, 120 * gib)
    XCTAssertLessThanOrEqual(
      recommendation.requestedLengthBytes + workspace
        + InstallerAllocationRecommendation.resizeHeadroomBytes,
      resize.lengthBytes - resize.minimumContainerBytes)
    let oldWorkspace = try InstallerAllocationRecommendation.workingSpaceBytes(
      for: releaseRecord(payloadBytes: 3_750_452_703, scratchBytes: 64 * gib))
    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(
        inventory: inventory([resize]), workingSpaceBytes: oldWorkspace))
  }

  func testWorkingSpaceCannotConsumeMinimumInstallOrFreeExtent() throws {
    let resize = candidate(
      kind: "resize", source: "disk0s2", length: 160 * gib,
      minimumInstall: 64 * gib, minimumContainer: 80 * gib)
    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(
        inventory: inventory([resize]), workingSpaceBytes: 16 * gib))
    let free = candidate(
      kind: "free", source: "disk0s3", length: 64 * gib, minimumInstall: 64 * gib)
    XCTAssertEqual(
      try InstallerAllocationRecommendation(
        inventory: inventory([free]), workingSpaceBytes: 16 * gib
      ).requestedLengthBytes,
      64 * gib)
  }

  func testWorkingSpaceArithmeticFailsClosed() throws {
    XCTAssertThrowsError(
      try InstallerAllocationRecommendation.workingSpaceBytes(
        for: releaseRecord(payloadBytes: .max)))
    XCTAssertThrowsError(
      try InstallerAllocationRecommendation.workingSpaceBytes(
        for: releaseRecord(scratchBytes: .max)))
    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(inventory: inventory([]), workingSpaceBytes: .max))
  }

  private func releaseRecord(
    payloadBytes: UInt64 = 5_927_464_429, scratchBytes: UInt64 = 8_589_934_592
  ) throws -> PinnedInstallerRecord {
    let digest = "sha256:" + String(repeating: "a", count: 64)
    func artifact(_ role: String, _ size: UInt64) throws -> PinnedInstallerArtifact {
      try PinnedInstallerArtifact(
        role: role, sourceURL: URL(string: "https://example.com/" + role)!,
        fileName: role, expectedDigest: digest, expectedSizeBytes: size)
    }
    return try PinnedInstallerRecord(
      deviceIdentifier: "apple,j713", downstreamRevision: String(repeating: "b", count: 40),
      engineVersion: "v0.1.0-cleanroom.1", engineDigest: digest,
      metadataDigest: digest, payloadDigest: digest, evidenceRevision: "working-space-test",
      delivery: PinnedInstallerDelivery(
        engine: artifact("engine", 23_337_845), metadata: artifact("metadata", 2_664),
        payload: artifact("payload", payloadBytes)), executionScratchBytes: scratchBytes)
  }

  private func inventory(
    _ candidates: [ValidatedEngineCandidate]
  ) -> ValidatedEngineInventory {
    ValidatedEngineInventory(
      layoutDigest: "sha256:" + String(repeating: "a", count: 64),
      systemStoreIdentifier: "disk0",
      candidates: candidates
    )
  }

  private func candidate(
    kind: String,
    source: String,
    length: UInt64,
    minimumInstall: UInt64,
    minimumContainer: UInt64 = 0,
    identityDigest: String? = nil
  ) -> ValidatedEngineCandidate {
    ValidatedEngineCandidate(
      kind: kind,
      sourceIdentifier: source,
      offsetBytes: 0,
      lengthBytes: length,
      minimumInstallBytes: minimumInstall,
      minimumContainerBytes: minimumContainer,
      identityDigest: identityDigest
    )
  }
}
