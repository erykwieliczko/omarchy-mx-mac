import Foundation

/// Public builds require HTTPS. Private test builds additionally admit one
/// exact HTTP origin sealed into the app, shared by its app and root helper.
enum InstallerDownloadURLPolicy {
  static func allows(_ url: URL) -> Bool {
    #if OMARCHY_PRIVATE_HTTP
      return allows(url, privateOrigin: bundledPrivateOrigin)
    #else
      return allows(url, privateOrigin: nil)
    #endif
  }

  static func allows(_ url: URL, privateOrigin: URL?) -> Bool {
    guard url.host?.isEmpty == false, url.user == nil, url.password == nil,
      url.fragment == nil
    else { return false }
    if url.scheme == "https" { return true }
    guard url.scheme == "http", let origin = privateOrigin,
      origin.scheme == "http", origin.host == url.host,
      (origin.port ?? 80) == (url.port ?? 80),
      origin.user == nil, origin.password == nil, origin.query == nil,
      origin.fragment == nil, origin.path.isEmpty || origin.path == "/"
    else { return false }
    return true
  }

  static func allowsRedirect(from original: URL?, to destination: URL) -> Bool {
    guard allows(destination), let original else { return false }
    if original.scheme == "https" { return destination.scheme == "https" }
    return original.scheme == destination.scheme && original.host == destination.host
      && (original.port ?? 80) == (destination.port ?? 80)
  }

  #if OMARCHY_PRIVATE_HTTP
    private static var bundledPrivateOrigin: URL? {
      guard var path = Bundle.main.executableURL else { return nil }
      // Both Contents/MacOS/app and Contents/Resources/helper resolve here.
      for _ in 0..<5 {
        path.deleteLastPathComponent()
        if path.pathExtension == "app" {
          guard let app = Bundle(url: path),
            let value = app.object(forInfoDictionaryKey: "OmarchyPrivateHTTPOrigin") as? String
          else { return nil }
          return URL(string: value)
        }
      }
      return nil
    }
  #endif
}

final class InstallerDownloadRedirectDelegate: NSObject, URLSessionTaskDelegate {
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping @Sendable (URLRequest?) -> Void
  ) {
    guard let target = request.url,
      InstallerDownloadURLPolicy.allowsRedirect(from: task.originalRequest?.url, to: target)
    else {
      completionHandler(nil)
      return
    }
    completionHandler(request)
  }
}
