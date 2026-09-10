import Foundation
import OmarchyAppleInstallerTrustCore
import XCTest

final class DeveloperModelOverrideTests: XCTestCase {
  func testBothModelsRemainSelectableWithM4First() throws {
    XCTAssertEqual(DeveloperModelOverride.allCases, [.m4MacBookAir, .macBookNeo])
    XCTAssertEqual(DeveloperModelOverride.m4MacBookAir.rawValue, "apple,j713")
    XCTAssertEqual(DeveloperModelOverride.macBookNeo.rawValue, "apple,j700")
    for model in DeveloperModelOverride.allCases {
      let encoded = try JSONEncoder().encode(model)
      XCTAssertEqual(try JSONDecoder().decode(DeveloperModelOverride.self, from: encoded), model)
    }
    XCTAssertNil(DeveloperModelOverride(rawValue: "apple,j614s"))
  }
}
