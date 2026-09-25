import AppKit
import OmarchyAppleInstallerTrustCore
import OmarchyInstallerUXCore
import SwiftUI

@MainActor
private final class InstallerApplicationDelegate: NSObject, NSApplicationDelegate {
  static var removalInProgress = false

  func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
    if Self.removalInProgress {
      NSSound.beep()
      return .terminateCancel
    }
    return .terminateNow
  }

  private var instanceLease: InstallerAppInstanceLease?

  func applicationWillFinishLaunching(_ notification: Notification) {
    #if !DEBUG
      if ProcessInfo.processInfo.arguments.contains("--simulate") {
        fputs("Simulation requires a debug build. Refusing to launch the live installer.\n", stderr)
        NSApplication.shared.terminate(nil)
        return
      }
    #endif
    do {
      let lockFile = try InstallerAppInstanceLease.defaultLockFileURL()
      instanceLease = try InstallerAppInstanceLease.acquire(at: lockFile)
    } catch {
      fputs("Omarchy MX Mac Installer refused a duplicate or unsafe launch: \(error)\n", stderr)
      NSApplication.shared.terminate(nil)
    }
  }

  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    true
  }

  func applicationDidFinishLaunching(_ notification: Notification) {
    // The install ends with a shutdown the app itself requests. macOS would
    // otherwise treat it as an app open at shutdown and bring it back at the
    // next login, so someone rebooting from Omarchy into macOS met the
    // installer again. Opt out for good.
    NSApp.disableRelaunchOnLogin()

    #if DEBUG
      // A bare SwiftPM executable has no Info.plist, so Launch Services
      // registers it background-only and the window never appears. Promote
      // unbundled debug runs to a regular, frontmost app; the packaged app
      // is already regular and never enters this branch.
      if Bundle.main.bundleURL.pathExtension != "app" {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
      }
    #endif
  }
}

@main
struct OmarchyAppleInstallerApp: App {
  @NSApplicationDelegateAdaptor(InstallerApplicationDelegate.self)
  private var applicationDelegate

  /// Testers switch to a pre-release channel here. The choice only picks among
  /// the URLs already signed into this build; changing it restarts the check so
  /// nothing planned against one channel is installed from the other.
  @State private var liveSession: InstallerSession?
  @State private var showsRemoval = false
  @State private var advanced = false
  @State private var removalNeedsReview = false
  @State private var simulationDark = true
  @State private var generation = UUID()
  @State private var developmentOverrideEnabled = false
  @State private var selectedDevelopmentProfile = "apple,j413"
  @State private var activeDevelopmentProfile: String?

  private var isSimulation: Bool {
    #if DEBUG
      return ProcessInfo.processInfo.arguments.contains("--simulate")
    #else
      return false
    #endif
  }

  @ViewBuilder
  private var installerContent: some View {
    #if DEBUG
      if isSimulation {
        SimulationDashboard(
          onSessionAvailable: { liveSession = $0 }, onColorSchemeChange: { simulationDark = $0 })
      } else {
        liveContent
      }
    #else
      if ProcessInfo.processInfo.arguments.contains("--simulate") {
        ContentUnavailableView(
          "Simulation is available in debug builds only.", systemImage: "lock.shield")
      } else {
        liveContent
      }
    #endif
  }

  private var liveContent: some View {
    OnePageInstallerView(
      environment: InstallerEnvironmentFactory.make(developmentProfileID: activeDevelopmentProfile),
      channel: channel,
      onSessionAvailable: { liveSession = $0 })
  }

  @State private var channel = ReleaseChannelPreference().resolveFromMainBundle()

  var body: some Scene {
    WindowGroup(PlainLanguage.windowTitle) {
      Group {
        if removalNeedsReview {
          VStack {
            ContentUnavailableView(
              "Removal needs review", systemImage: "externaldrive.badge.exclamationmark",
              description: Text(
                "Check the removal journal and disk layout before making further disk changes."))
            #if DEBUG
              if isSimulation {
                Button("Reset simulation") {
                  removalNeedsReview = false
                  generation = UUID()
                }
                .omarchySecondaryButton().padding(.bottom, 24)
              }
            #endif
          }
        } else {
          VStack(spacing: 0) {
            if let activeDevelopmentProfile {
              Text(
                "Development override: \(profileName(activeDevelopmentProfile)) firmware and configuration"
              )
              .font(.callout.bold())
              .foregroundStyle(OmarchyTheme.accent)
              .multilineTextAlignment(.center)
              .padding(.horizontal, 24)
              .padding(.top, 12)
            }
            Picker("Installer tab", selection: $advanced) {
              Text("Install").tag(false)
              Text("Advanced").tag(true)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 240)
            .padding(.top, 20)
            .padding(.bottom, 12)
            .disabled(showsRemoval || liveSession?.canChangeChannel != true)
            ZStack {
              installerContent
                .opacity(advanced ? 0 : 1)
                .allowsHitTesting(!advanced)
                .disabled(advanced)
                .accessibilityHidden(advanced)
              VStack(alignment: .leading, spacing: 18) {
                Text("Advanced").font(.title2.bold())
                if DevelopmentMachineOverride.isAvailable && !isSimulation {
                  Toggle("Install anyway on an unsupported Mac", isOn: $developmentOverrideEnabled)
                    .font(.headline)
                    .disabled(liveSession?.canChangeChannel != true)
                    .accessibilityIdentifier("development-machine-override")
                    .onChange(of: developmentOverrideEnabled) { _, enabled in
                      if !enabled { applyDevelopmentProfile(nil) }
                    }
                  if developmentOverrideEnabled {
                    Text(
                      "Development only. Uses the selected Mac’s firmware and installer configuration. The resulting installation may not boot."
                    )
                    .fixedSize(horizontal: false, vertical: true)
                    Picker("Mac configuration", selection: $selectedDevelopmentProfile) {
                      ForEach(DevelopmentMachineProfile.available) { profile in
                        Text("\(profileName(profile.id)) · \(profile.id)").tag(profile.id)
                      }
                    }
                    .disabled(liveSession?.canChangeChannel != true)
                    .accessibilityIdentifier("development-machine-profile")
                    Button("Use selected profile") {
                      applyDevelopmentProfile(selectedDevelopmentProfile)
                    }
                    .omarchySecondaryButton()
                    .disabled(
                      liveSession?.canChangeChannel != true
                        || activeDevelopmentProfile == selectedDevelopmentProfile
                    )
                    .accessibilityIdentifier("development-machine-apply")
                  }
                  Divider()
                }
                Text("Uninstall Omarchy").font(.headline)
                Text(
                  "Remove Omarchy and all files stored in it, and return its disk space to macOS. Your macOS files and Apple Recovery will be kept."
                )
                .fixedSize(horizontal: false, vertical: true)
                Button("Uninstall Omarchy…") { showsRemoval = true }
                  .omarchySecondaryButton()
                  .disabled(liveSession?.canChangeChannel != true)
                  .accessibilityIdentifier("advanced-uninstall")
                Spacer()
              }
              .padding(32)
              .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
              .foregroundStyle(OmarchyTheme.text)
              .opacity(advanced ? 1 : 0)
              .allowsHitTesting(advanced)
              .disabled(!advanced)
              .accessibilityHidden(!advanced)
            }
          }
        }
      }
      .id(generation)
      .onReceive(
        NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)
      ) { _ in
        liveSession?.cancelPrefetchOnQuit()
      }
      .disabled(showsRemoval)
      .sheet(isPresented: $showsRemoval, onDismiss: { generation = UUID() }) {
        OmarchyRemovalSheet(
          isSimulation: isSimulation,
          onBusyChanged: { busy in
            InstallerApplicationDelegate.removalInProgress = busy
            for window in NSApp.windows where window.sheetParent == nil {
              window.standardWindowButton(.closeButton)?.isEnabled = !busy
            }
          }, onClose: { showsRemoval = false }, onRequiresReview: { removalNeedsReview = true })
      }
      .preferredColorScheme(isSimulation ? (simulationDark ? .dark : .light) : nil)
      .frame(minWidth: 640)
      .tint(OmarchyTheme.accent)
      // The window itself takes the theme colour, title bar included, so the
      // translucent system title bar never tints from the wallpaper behind.
      .containerBackground(OmarchyTheme.window, for: .window)
    }
    .defaultSize(width: 780, height: 600)
    .windowResizability(.contentSize)
    .windowStyle(.hiddenTitleBar)
    .commands {
      CommandMenu("Installation") {
        Button("Uninstall Omarchy…") { showsRemoval = true }
          .disabled(removalNeedsReview || showsRemoval || liveSession?.canChangeChannel != true)
      }
      CommandMenu(PlainLanguage.channelMenuTitle) {
        Picker(PlainLanguage.channelMenuTitle, selection: channelBinding) {
          Text(PlainLanguage.channelStable).tag(ReleaseChannel?.some(.stable))
          Text(PlainLanguage.channelRC).tag(ReleaseChannel?.some(.rc))
        }
        .pickerStyle(.inline)
        .disabled(
          removalNeedsReview || showsRemoval || liveSession?.canChangeChannel != true
            || isSimulation || channel == nil)
      }
    }
  }

  private var channelBinding: Binding<ReleaseChannel?> {
    Binding(
      get: { channel },
      set: { selected in
        guard let selected, liveSession?.canChangeChannel == true && !isSimulation else {
          return
        }
        ReleaseChannelPreference().select(selected)
        channel = selected
      }
    )
  }

  private func profileName(_ identifier: String) -> String {
    identifier == "apple,j700"
      ? "MacBook Neo (26.6.2; Aurora J700 kernel)"
      : MacModelNames.name(for: identifier) ?? identifier
  }

  private func applyDevelopmentProfile(_ identifier: String?) {
    guard liveSession?.canChangeChannel == true else { return }
    liveSession?.cancelPrefetchOnQuit()
    liveSession = nil
    activeDevelopmentProfile = identifier
    generation = UUID()
  }
}
