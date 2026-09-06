import Foundation

/// Profiles with an installable payload in this release. Explicit opt-in only.
public enum DeveloperModelOverride: String, CaseIterable, Codable, Sendable {
  case m4MacBookAir = "apple,j713"

  public var displayName: String {
    switch self {
    case .m4MacBookAir: "M4 MacBook Air"
    }
  }
}
