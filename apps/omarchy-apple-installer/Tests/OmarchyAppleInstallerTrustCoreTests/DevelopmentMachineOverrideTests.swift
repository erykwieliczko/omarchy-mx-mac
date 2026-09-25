#if os(macOS)
  import Foundation
  import XCTest

  @testable import OmarchyAppleInstallerTrustCore

  final class DevelopmentMachineOverrideTests: XCTestCase {
    func testAutomaticNeoSelectionPreservesExplicitChoicesAndOtherMacs() {
      XCTAssertEqual(
        DevelopmentMachineOverride.resolvedProfileID(
          requested: nil, physicalDeviceIdentifier: "apple,j700"),
        DevelopmentMachineOverride.isAvailable ? "apple,j700" : nil)
      for physical in ["apple,j313", "apple,j413", "apple,j614s", "apple,unknown"] {
        XCTAssertNil(
          DevelopmentMachineOverride.resolvedProfileID(
            requested: nil, physicalDeviceIdentifier: physical))
      }
      XCTAssertEqual(
        DevelopmentMachineOverride.resolvedProfileID(
          requested: "apple,j413", physicalDeviceIdentifier: "apple,j700"),
        "apple,j413")
    }

    func testNormalBuildRejectsDevelopmentOverride() throws {
      #if !OMARCHY_DEVELOPMENT
        XCTAssertFalse(DevelopmentMachineOverride.isAvailable)
        XCTAssertThrowsError(
          try DevelopmentMachineOverride(
            profileID: "apple,j413", physicalDeviceIdentifier: "apple,j700"))
      #endif
    }

    #if OMARCHY_DEVELOPMENT
      func testProfileChangesIdentityButPreservesPhysicalStorageAndEligibility() throws {
        let override = try DevelopmentMachineOverride(
          profileID: "apple,j413", physicalDeviceIdentifier: "apple,j700")
        let host = physicalHost()
        let selected = try override.applying(to: host)
        XCTAssertEqual(selected.identity.deviceIdentifier, "apple,j413")
        XCTAssertEqual(selected.identity.model, "Mac14,2")
        XCTAssertEqual(selected.storage, host.storage)
        XCTAssertEqual(selected.eligibility, host.eligibility)
        XCTAssertEqual(selected.macOSVersion, host.macOSVersion)
        XCTAssertEqual(selected.fileVaultEnabled, host.fileVaultEnabled)
        XCTAssertEqual(selected.powerSource, host.powerSource)
        XCTAssertEqual(
          override.helperEnvironment["OMARCHY_DEVELOPMENT_PHYSICAL_DEVICE"], "apple,j700")
      }

      func testNeoFirmwareSelectionReusesM2ArtifactsWithoutChangingOtherProfiles() throws {
        let neo = try DevelopmentMachineOverride(
          profileID: "apple,j700", physicalDeviceIdentifier: "apple,j700")
        XCTAssertEqual(neo.profile.model, "Mac17,5")
        XCTAssertEqual(neo.profile.chipID, 0x8140)
        XCTAssertEqual(try neo.applying(to: physicalHost()).identity.deviceIdentifier, "apple,j413")
        XCTAssertEqual(neo.helperEnvironment["OMARCHY_DEVELOPMENT_PROFILE"], "apple,j700")
        for profile in DevelopmentMachineProfile.available where profile.id != "apple,j700" {
          XCTAssertEqual(profile.artifactProfile, profile)
        }
      }

      func testDifferentPhysicalHostAndProtectedMacRemainRejected() throws {
        let override = try DevelopmentMachineOverride(
          profileID: "apple,j413", physicalDeviceIdentifier: "apple,j313")
        XCTAssertThrowsError(try override.applying(to: physicalHost()))
        XCTAssertThrowsError(
          try DevelopmentMachineOverride(
            profileID: "apple,j413", physicalDeviceIdentifier: "apple,j614s"))
        XCTAssertThrowsError(
          try DevelopmentMachineOverride(
            profileID: "apple,unknown", physicalDeviceIdentifier: "apple,j700"))
      }

      func testLauncherUsesSelectedFirmwareAndKeepsPhysicalBugChecks() throws {
        let result = try runLauncher(firmwareDevice: "j413ap")
        XCTAssertEqual(result.status, 0, result.output)
        XCTAssertTrue(
          result.output.contains("selected-board=42; physical-check=331; boot=REAL-BOOT"))
      }

      func testM1LauncherKeepsOriginalFirmwarePath() throws {
        let result = try runLauncher(
          firmwareDevice: "j313ap", selectedDevice: "j313ap", selectedChip: 0x8103)
        XCTAssertEqual(result.status, 0, result.output)
        XCTAssertTrue(
          result.output.contains("selected-board=42; physical-check=331; boot=REAL-BOOT"))
      }

      func testMissingSelectedFirmwareFailsBeforeDiskWork() throws {
        let result = try runLauncher(firmwareDevice: "j313ap")
        XCTAssertNotEqual(result.status, 0)
        XCTAssertTrue(result.output.contains("No unique firmware board identity"))
        XCTAssertFalse(result.output.contains("disk-work-would-start"))
      }

      func testLauncherRechecksActualDevice() throws {
        let result = try runLauncher(firmwareDevice: "j413ap", actualDevice: "j313ap")
        XCTAssertNotEqual(result.status, 0)
        XCTAssertTrue(result.output.contains("physical host changed"))
        XCTAssertFalse(result.output.contains("disk-work-would-start"))
      }

      private func physicalHost() -> AppleSiliconHostInspection {
        AppleSiliconHostInspection(
          identity: AppleMacIdentity(
            model: "Mac17,5", chip: "Apple A18 Pro", deviceIdentifier: "apple,j700"),
          eligibility: .requiresSignedCatalog, macOSVersion: "26.5.2", powerSource: .ac,
          fileVaultEnabled: true,
          storage: APFSStorageInspection(
            containerIdentifier: "disk3", physicalStoreIdentifier: "disk0s2", isInternal: true,
            containerSizeBytes: 200_000_000_000, containerFreeBytes: 130_000_000_000,
            minimumPreferredSizeBytes: 100_000_000_000))
      }

      private func runLauncher(
        firmwareDevice: String, actualDevice: String = "j700ap", selectedDevice: String = "j413ap",
        selectedChip: Int = 0x8112
      ) throws -> (
        status: Int32, output: String
      ) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(
          UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let files = [
          "system.py": """
          class SystemInfo:
              def __init__(self): self.fetch()
              def fetch(self):
                  self.device_class = '\(actualDevice)'
                  self.chip_id = 0x8140
                  self.board_id = 331
                  self.product_type = 'Mac17,5'
                  self.product_name = 'MacBook Neo'
                  self.soc_name = 'Apple A18 Pro'
                  self.boot_uuid = 'REAL-BOOT'
          """,
          "bugs.py": """
          def run_checks(installer):
              assert installer.sysinfo.device_class == 'j700ap'
              assert installer.sysinfo.chip_id == 0x8140
              installer.physical_check = installer.sysinfo.board_id
          """,
          "stub.py": """
          import io, plistlib
          class StubInstaller:
              def __init__(self, info): self.sysinfo = info
              def load_ipsw(self, ipsw_info): self.is_ota = False
              def open(self, name):
                  assert name == 'BuildManifest.plist'
                  return io.BytesIO(plistlib.dumps({'BuildIdentities': [{
                      'ApBoardID': '0x2A', 'ApChipID': '\(String(selectedChip, radix: 16))',
                      'Info': {'DeviceClass': '\(firmwareDevice)', 'RestoreBehavior': 'Erase',
                               'Variant': 'macOS Customer'}}]}))
          """,
          "main.py": """
          import system, stub, bugs
          from types import SimpleNamespace
          info = system.SystemInfo()
          assert info.device_class == '\(selectedDevice)' and info.chip_id == \(selectedChip)
          installer = SimpleNamespace(sysinfo=info)
          bugs.run_checks(installer)
          assert info.device_class == '\(selectedDevice)' and info.chip_id == \(selectedChip)
          stub.StubInstaller(info).load_ipsw(None)
          assert info.board_id == 42
          print(f'selected-board={info.board_id}; physical-check={installer.physical_check}; boot={info.boot_uuid}')
          print('disk-work-would-start')
          """,
        ]
        for (name, content) in files {
          try content.write(
            to: directory.appendingPathComponent(name), atomically: true, encoding: .utf8)
        }
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        let payload = try JSONSerialization.data(withJSONObject: [
          "device_class": selectedDevice, "chip_id": selectedChip,
          "product_type": "TestMac", "product_name": "Test Mac", "soc_name": "Apple Silicon",
          "physical_device": "apple,j700",
          "firmware_profile": "apple," + selectedDevice.dropLast(2),
        ])
        process.arguments = [
          "-c", DevelopmentMachineOverride.engineLauncher, directory.path,
          String(decoding: payload, as: UTF8.self),
        ]
        process.standardOutput = output
        process.standardError = output
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return (process.terminationStatus, String(decoding: data, as: UTF8.self))
      }
    #endif
  }
#endif
