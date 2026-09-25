#if os(macOS)
  import Foundation

  /// Deliberate machine impersonation, available only in explicitly built development apps.
  public struct DevelopmentMachineProfile: Equatable, Sendable, Identifiable {
    public let id: String
    public let model: String
    public let chip: String
    public let chipID: Int

    /// Neo reuses the existing OS image; the separate Aurora bundle supplies its kernel.
    public var artifactProfile: Self {
      id == "apple,j700" ? Self.available.first { $0.id == "apple,j413" }! : self
    }

    public static let available: [Self] = [
      .init(id: "apple,j700", model: "Mac17,5", chip: "Apple A18 Pro", chipID: 0x8140),
      .init(id: "apple,j274", model: "Macmini9,1", chip: "Apple M1", chipID: 0x8103),
      .init(id: "apple,j293", model: "MacBookPro17,1", chip: "Apple M1", chipID: 0x8103),
      .init(id: "apple,j313", model: "MacBookAir10,1", chip: "Apple M1", chipID: 0x8103),
      .init(id: "apple,j456", model: "iMac21,1", chip: "Apple M1", chipID: 0x8103),
      .init(id: "apple,j457", model: "iMac21,2", chip: "Apple M1", chipID: 0x8103),
      .init(id: "apple,j314s", model: "MacBookPro18,3", chip: "Apple M1 Pro", chipID: 0x6000),
      .init(id: "apple,j314c", model: "MacBookPro18,4", chip: "Apple M1 Max", chipID: 0x6001),
      .init(id: "apple,j316s", model: "MacBookPro18,1", chip: "Apple M1 Pro", chipID: 0x6000),
      .init(id: "apple,j316c", model: "MacBookPro18,2", chip: "Apple M1 Max", chipID: 0x6001),
      .init(id: "apple,j375c", model: "Mac13,1", chip: "Apple M1 Max", chipID: 0x6001),
      .init(id: "apple,j375d", model: "Mac13,2", chip: "Apple M1 Ultra", chipID: 0x6002),
      .init(id: "apple,j413", model: "Mac14,2", chip: "Apple M2", chipID: 0x8112),
      .init(id: "apple,j493", model: "Mac14,7", chip: "Apple M2", chipID: 0x8112),
      .init(id: "apple,j473", model: "Mac14,3", chip: "Apple M2", chipID: 0x8112),
      .init(id: "apple,j474s", model: "Mac14,12", chip: "Apple M2 Pro", chipID: 0x6020),
      .init(id: "apple,j414s", model: "Mac14,9", chip: "Apple M2 Pro", chipID: 0x6020),
      .init(id: "apple,j414c", model: "Mac14,5", chip: "Apple M2 Max", chipID: 0x6021),
      .init(id: "apple,j416s", model: "Mac14,10", chip: "Apple M2 Pro", chipID: 0x6020),
      .init(id: "apple,j416c", model: "Mac14,6", chip: "Apple M2 Max", chipID: 0x6021),
      .init(id: "apple,j415", model: "Mac14,15", chip: "Apple M2", chipID: 0x8112),
      .init(id: "apple,j475c", model: "Mac14,13", chip: "Apple M2 Max", chipID: 0x6021),
      .init(id: "apple,j475d", model: "Mac14,14", chip: "Apple M2 Ultra", chipID: 0x6022),
      .init(id: "apple,j180d", model: "Mac14,8", chip: "Apple M2 Ultra", chipID: 0x6022),
    ]
  }

  public enum DevelopmentMachineOverrideError: Error {
    case unavailable
    case invalidProfile
    case physicalHostChanged
  }

  public struct DevelopmentMachineOverride: Equatable, Sendable {
    public let profile: DevelopmentMachineProfile
    public let physicalDeviceIdentifier: String
    static let profileKey = "OMARCHY_DEVELOPMENT_PROFILE"
    static let physicalDeviceKey = "OMARCHY_DEVELOPMENT_PHYSICAL_DEVICE"

    public static func resolvedProfileID(requested: String?, physicalDeviceIdentifier: String)
      -> String?
    {
      requested ?? (isAvailable && physicalDeviceIdentifier == "apple,j700" ? "apple,j700" : nil)
    }

    public static var isAvailable: Bool {
      #if OMARCHY_DEVELOPMENT
        true
      #else
        false
      #endif
    }

    public init(profileID: String, physicalDeviceIdentifier: String) throws {
      guard Self.isAvailable else { throw DevelopmentMachineOverrideError.unavailable }
      guard let profile = DevelopmentMachineProfile.available.first(where: { $0.id == profileID })
      else { throw DevelopmentMachineOverrideError.invalidProfile }
      guard physicalDeviceIdentifier.hasPrefix("apple,"),
        !AppleSiliconHostInspector.isExplicitlyUnsupported(physicalDeviceIdentifier)
      else { throw DevelopmentMachineOverrideError.physicalHostChanged }
      self.profile = profile
      self.physicalDeviceIdentifier = physicalDeviceIdentifier
    }

    public static func fromHelperEnvironment() throws -> Self? {
      let environment = ProcessInfo.processInfo.environment
      guard environment[profileKey] != nil || environment[physicalDeviceKey] != nil else {
        return nil
      }
      guard let profile = environment[profileKey], let physical = environment[physicalDeviceKey]
      else { throw DevelopmentMachineOverrideError.invalidProfile }
      return try Self(profileID: profile, physicalDeviceIdentifier: physical)
    }

    public var helperRelativePath: String {
      profile.id == "apple,j700"
        ? "/Installer.app/Contents/Resources/omarchy-apple-installer-helper" : "/helper"
    }

    var helperEnvironment: [String: String] {
      [Self.profileKey: profile.id, Self.physicalDeviceKey: physicalDeviceIdentifier]
    }

    public func applying(to host: AppleSiliconHostInspection) throws -> AppleSiliconHostInspection {
      guard host.identity.deviceIdentifier == physicalDeviceIdentifier,
        !AppleSiliconHostInspector.isExplicitlyUnsupported(host.identity.deviceIdentifier)
      else { throw DevelopmentMachineOverrideError.physicalHostChanged }
      // Only machine selection changes. Storage, OS state and eligibility remain real.
      return AppleSiliconHostInspection(
        identity: AppleMacIdentity(
          model: profile.artifactProfile.model, chip: profile.artifactProfile.chip,
          deviceIdentifier: profile.artifactProfile.id),
        eligibility: host.eligibility, macOSVersion: host.macOSVersion,
        powerSource: host.powerSource, fileVaultEnabled: host.fileVaultEnabled,
        storage: host.storage)
    }

    func engineArguments(in bundle: URL) throws -> [String] {
      #if OMARCHY_DEVELOPMENT
        _ = try applying(to: AppleSiliconHostInspector().inspect())
        let payload = try JSONSerialization.data(withJSONObject: [
          "device_class": String(profile.artifactProfile.id.dropFirst(6)) + "ap",
          "chip_id": profile.artifactProfile.chipID,
          "product_type": profile.artifactProfile.model,
          "product_name": profile.artifactProfile.model,
          "soc_name": profile.artifactProfile.chip,
          "physical_device": physicalDeviceIdentifier,
          "firmware_profile": profile.id,
        ])
        var arguments = [
          "-c", Self.engineLauncher, bundle.path,
          String(decoding: payload, as: UTF8.self),
        ]
        if profile.id == "apple,j700" {
          guard let executable = Bundle.main.executableURL else {
            throw DevelopmentMachineOverrideError.unavailable
          }
          let directory = executable.deletingLastPathComponent()
          let resources =
            directory.lastPathComponent == "MacOS"
            ? directory.deletingLastPathComponent().appendingPathComponent("Resources") : directory
          let neo = resources.appendingPathComponent("Development/neo")
          guard
            FileManager.default.isExecutableFile(
              atPath: neo.appendingPathComponent("restore-image-tool").path)
          else { throw DevelopmentMachineOverrideError.unavailable }
          arguments.append(neo.path)
        }
        return arguments
      #else
        throw DevelopmentMachineOverrideError.unavailable
      #endif
    }

    #if OMARCHY_DEVELOPMENT
      // The archive is still verified. This explicit development launcher changes
      // its execution semantics; it is never used by a normal release build.
      static let engineLauncher = #"""
        import json, logging, os, plistlib, runpy, sys
        root, profile = sys.argv[1], json.loads(sys.argv[2])
        sys.path.insert(0, root)
        import system, stub, bugs

        original_fetch = system.SystemInfo.fetch
        def fetch(self):
            original_fetch(self)
            real = self.device_class.lower()
            if real.endswith("ap"):
                real = real[:-2]
            if not real.startswith("apple,"):
                real = "apple," + real
            if real != profile["physical_device"] or real == "apple,j614s":
                raise RuntimeError("Development override physical host changed")
            if profile.get("firmware_profile") == "apple,j700":
                development_neo.require_system_firmware(self)
            self.omarchy_physical_device = real
            self.omarchy_physical_identity = {key: getattr(self, key) for key in
                ("device_class", "chip_id", "product_type", "product_name", "soc_name")}
            for key in ("device_class", "chip_id", "product_type", "product_name", "soc_name"):
                setattr(self, key, profile[key])
            logging.warning("DEVELOPMENT machine override: %s -> %s", real, self.device_class)
        system.SystemInfo.fetch = fetch

        original_checks = bugs.run_checks
        def run_checks(installer):
            info = installer.sysinfo
            for key, value in info.omarchy_physical_identity.items():
                setattr(info, key, value)
            try:
                return original_checks(installer)
            finally:
                for key in info.omarchy_physical_identity:
                    setattr(info, key, profile[key])
        bugs.run_checks = run_checks

        original_load_ipsw = stub.StubInstaller.load_ipsw
        def load_ipsw(self, ipsw_info):
            result = original_load_ipsw(self, ipsw_info)
            manifest = plistlib.load(self.open("BuildManifest.plist"))
            behavior = "Update" if self.is_ota else "Erase"
            variant = "macOS Customer Software Update" if self.is_ota else "macOS Customer"
            identities = [entry for entry in manifest["BuildIdentities"]
                          if entry["Info"]["DeviceClass"] == profile["device_class"]
                          and int(entry["ApChipID"], 16) == profile["chip_id"]
                          and entry["Info"]["RestoreBehavior"] == behavior
                          and entry["Info"]["Variant"] == variant]
            if len(identities) != 1:
                raise RuntimeError("No unique firmware board identity for selected development profile")
            self.sysinfo.board_id = int(identities[0]["ApBoardID"], 16)
            return result
        if profile.get("firmware_profile") == "apple,j700":
            sys.dont_write_bytecode = True
            sys.path.insert(0, sys.argv[3])
            import development_neo
            development_neo.configure(profile)
        else:
            stub.StubInstaller.load_ipsw = load_ipsw
        sys.argv = [os.path.join(root, "main.py")]
        runpy.run_path(sys.argv[0], run_name="__main__")
        """#
    #endif
  }
#endif
