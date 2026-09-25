#if os(macOS)
  import Foundation
  import XCTest

  @testable import OmarchyAppleInstallerTrustCore

  final class DiskutilInstallConfMountTests: XCTestCase {
    private let destination = URL(
      fileURLWithPath: "/private/var/run/omarchy-mount-test", isDirectory: true)

    func testAlreadyMountedESPIsMovedBeforeWriting() throws {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC")
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      try operatorUnderTest.mount("disk5s1", at: destination)
      XCTAssertEqual(disk.mountPoint, destination.path)
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info", "unmount", "mount", "info"])
    }

    func testSuccessfulCommandAtWrongMountPointIsRejected() {
      let disk = MountFixture(forcedMountPoint: "/Volumes/EFI - OMARC")
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination)) {
        XCTAssertEqual($0 as? InstallConfESPError, .mountFailed)
      }
      XCTAssertNil(disk.mountPoint)
      XCTAssertEqual(disk.calls.last, ["unmount", "disk5s1"])
    }

    func testMountThatAttachesThenFailsIsUnmounted() {
      let disk = MountFixture(failsAfterMount: true)
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination))
      XCTAssertNil(disk.mountPoint)
      XCTAssertEqual(disk.calls.last, ["unmount", "disk5s1"])
    }

    func testReadOnlyMountIsRejectedAndUnmounted() {
      let disk = MountFixture(writable: false)
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination))
      XCTAssertNil(disk.mountPoint)
      XCTAssertEqual(disk.calls.last, ["unmount", "disk5s1"])
    }

    func testBusyExistingMountIsNeverForcedOrWrittenThrough() {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC", refusesUnmount: true)
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination))
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info", "unmount"])
      XCTAssertEqual(disk.mountPoint, "/Volumes/EFI - OMARC")
    }
  }

  /// Models diskutil's successful no-op when mounting an already mounted volume.
  private final class MountFixture: @unchecked Sendable {
    var mountPoint: String?
    let forcedMountPoint: String?
    let writable: Bool
    let refusesUnmount: Bool
    let failsAfterMount: Bool
    var calls: [[String]] = []

    init(
      mountPoint: String? = nil, forcedMountPoint: String? = nil,
      writable: Bool = true, refusesUnmount: Bool = false, failsAfterMount: Bool = false
    ) {
      self.mountPoint = mountPoint
      self.forcedMountPoint = forcedMountPoint
      self.writable = writable
      self.refusesUnmount = refusesUnmount
      self.failsAfterMount = failsAfterMount
    }

    func run(_ arguments: [String]) throws -> Data {
      calls.append(arguments)
      switch arguments[0] {
      case "info":
        var info: [String: Any] = ["DeviceIdentifier": "disk5s1", "WritableVolume": writable]
        if let mountPoint { info["MountPoint"] = mountPoint }
        return try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
      case "mount":
        if mountPoint == nil { mountPoint = forcedMountPoint ?? arguments[2] }
        if failsAfterMount { throw InstallConfESPError.mountFailed }
      case "unmount":
        if refusesUnmount { throw InstallConfESPError.mountFailed }
        mountPoint = nil
      default:
        XCTFail("Unexpected disk command: \(arguments)")
      }
      return Data()
    }
  }
#endif
