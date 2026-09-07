import OmarchyAppleInstallerTrustCore
import XCTest

@testable import OmarchyInstallerUXCore

final class PlanDisplayTests: XCTestCase {
  func testFreeExtentLargerThanContainerCannotUnderflowTheBar() {
    let plan = PlanDisplay(
      diskTotalBytes: 100_000_000_000, omarchyBytes: 137_438_953_472,
      bindingDigest: "test", minimumOmarchyBytes: 40_000_000_000,
      maximumOmarchyBytes: 200_000_000_000)
    XCTAssertEqual(plan.diskTotalBytes, 200_000_000_000)
    XCTAssertEqual(plan.diskTotalBytes - plan.clampedOmarchyBytes(gigabytes: 200), 0)
  }

  func testSizeEntryUsesRealBoundsAndPlannerAlignment() {
    let unit = PinnedAsahiPlanRequest.allocationUnitBytes
    let plan = PlanDisplay(
      diskTotalBytes: 245_054_767_104, omarchyBytes: 45_000 * unit,
      bindingDigest: "test", minimumOmarchyBytes: 37_700 * unit,
      maximumOmarchyBytes: 51_000 * unit)
    XCTAssertEqual(plan.clampedOmarchyBytes(gigabytes: 1), plan.minimumOmarchyBytes)
    XCTAssertEqual(plan.clampedOmarchyBytes(gigabytes: 900), plan.maximumOmarchyBytes)
    XCTAssertEqual(plan.clampedOmarchyBytes(gigabytes: .nan), plan.omarchyBytes)
    XCTAssertEqual(plan.clampedOmarchyBytes(gigabytes: .infinity), plan.omarchyBytes)
    XCTAssertEqual(plan.clampedOmarchyBytes(gigabytes: 45), 45_000_000_000 / unit * unit)
    XCTAssertEqual(
      plan.clampedOmarchyBytes(gigabytes: Double(plan.minimumOmarchyBytes) / 1e9),
      plan.minimumOmarchyBytes)
    XCTAssertEqual(
      plan.clampedOmarchyBytes(gigabytes: Double(plan.maximumOmarchyBytes) / 1e9),
      plan.maximumOmarchyBytes)
  }
}
