import Foundation

public enum InstallerAllocationRecommendationError:
  Error, Equatable, Sendable
{
  case noEligibleCandidate
  case invalidWorkingSpace
}

public struct InstallerAllocationRecommendation:
  Equatable, Sendable
{
  public static let balancedTargetBytes: UInt64 = 137_438_953_472
  // APFS resize limits move as macOS writes between inspection and planning.
  // Keep breathing room instead of proposing the instantaneous shrink limit.
  public static let resizeHeadroomBytes: UInt64 = 1_073_741_824

  public let candidate: ValidatedEngineCandidate
  public let requestedLengthBytes: UInt64

  public init(
    inventory: ValidatedEngineInventory,
    workingSpaceBytes: UInt64,
    targetBytes: UInt64 = Self.balancedTargetBytes
  ) throws {
    let (reserve, overflow) = workingSpaceBytes.addingReportingOverflow(Self.resizeHeadroomBytes)
    guard !overflow else { throw InstallerAllocationRecommendationError.invalidWorkingSpace }
    let unit = PinnedAsahiPlanRequest.allocationUnitBytes
    let ranked = inventory.candidates.compactMap { candidate -> Ranked? in
      let maximum: UInt64
      if candidate.kind == "free" {
        maximum = candidate.lengthBytes
      } else if candidate.kind == "resize",
        candidate.lengthBytes > candidate.minimumContainerBytes
      {
        let available = candidate.lengthBytes - candidate.minimumContainerBytes
        guard available > reserve else { return nil }
        maximum = available - reserve
      } else {
        return nil
      }

      let minimum = Self.alignUp(
        candidate.minimumInstallBytes,
        unit: unit
      )
      let alignedMaximum = maximum - (maximum % unit)
      guard minimum <= alignedMaximum else {
        return nil
      }
      return Ranked(
        candidate: candidate,
        minimum: minimum,
        maximum: alignedMaximum
      )
    }.sorted { left, right in
      if left.candidate.kind != right.candidate.kind {
        return left.candidate.kind == "free"
      }
      if left.maximum != right.maximum {
        return left.maximum > right.maximum
      }
      return left.candidate.sourceIdentifier
        < right.candidate.sourceIdentifier
    }

    guard let selected = ranked.first else {
      throw InstallerAllocationRecommendationError.noEligibleCandidate
    }
    let alignedTarget = targetBytes - (targetBytes % unit)
    candidate = selected.candidate
    requestedLengthBytes = min(
      selected.maximum,
      max(selected.minimum, alignedTarget)
    )
  }

  /// Originals are already staged when inspection runs. Both the app handoff
  /// and the helper import create independent verified copies after approval.
  /// The signed release also budgets the engine's extraction and Recovery work.
  public static func workingSpaceBytes(for installer: PinnedInstallerRecord) throws -> UInt64 {
    guard let delivery = installer.delivery else {
      throw InstallerAllocationRecommendationError.invalidWorkingSpace
    }
    let artifacts =
      [delivery.engine, delivery.metadata, delivery.payload]
      + (delivery.repairManifest.map { [$0] } ?? [])
    var total = installer.executionScratchBytes
    for artifact in artifacts {
      let (copies, multiplicationOverflow) = artifact.expectedSizeBytes.multipliedReportingOverflow(
        by: 2)
      let (updated, additionOverflow) = total.addingReportingOverflow(copies)
      guard !multiplicationOverflow, !additionOverflow else {
        throw InstallerAllocationRecommendationError.invalidWorkingSpace
      }
      total = updated
    }
    return total
  }

  private static func alignUp(
    _ value: UInt64,
    unit: UInt64
  ) -> UInt64 {
    let remainder = value % unit
    guard remainder != 0 else {
      return value
    }
    let adjustment = unit - remainder
    let (result, overflow) = value.addingReportingOverflow(adjustment)
    return overflow ? UInt64.max : result
  }

  private struct Ranked {
    let candidate: ValidatedEngineCandidate
    let minimum: UInt64
    let maximum: UInt64
  }
}
