#if os(macOS)
  import Darwin
  import Foundation

  public struct EngineDiagnosticFailure: Error, Equatable, Sendable, CustomStringConvertible,
    LocalizedError
  {
    public let operation: String
    public let logDirectory: String
    public let detail: String

    public init(operation: String, logDirectory: String, detail: String) {
      self.operation = operation
      self.logDirectory = logDirectory
      self.detail = detail
    }

    public var description: String {
      "Engine \(operation) failed: \(detail)\nLogs: \(logDirectory)"
    }
    public var errorDescription: String? { description }
  }

  /// Bounded, private diagnostic output. No environment or input is recorded.
  final class EngineDiagnostics {
    let directory: URL
    private let secret: Data
    private let operation: String
    private var streams: [EngineDiagnosticStream] = []

    init(parent: URL, operation: String, standardInput: Data?) throws {
      self.operation = operation
      var secret = standardInput ?? Data()
      if secret.last == 10 { secret.removeLast() }
      self.secret = secret
      let base = parent.appendingPathComponent("diagnostics", isDirectory: true)
      if !FileManager.default.fileExists(atPath: base.path) {
        try FileManager.default.createDirectory(
          at: base, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
      }
      var status = stat()
      guard lstat(base.path, &status) == 0, status.st_mode & S_IFMT == S_IFDIR,
        status.st_uid == geteuid(), status.st_mode & 0o077 == 0
      else { throw PinnedAsahiEngineExecutionError.unsafeTranscript }
      directory = base.appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
      try FileManager.default.createDirectory(
        at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
      try write("Operation: \(operation)\nStarted: \(Date().ISO8601Format())\n", name: "run.log")
    }

    func attach(to process: Process) throws {
      let stdout = try EngineDiagnosticStream(
        url: directory.appendingPathComponent("stdout.log"), secret: secret)
      let stderr = try EngineDiagnosticStream(
        url: directory.appendingPathComponent("stderr.log"), secret: secret)
      streams = [stdout, stderr]
      process.standardOutput = stdout.pipe
      process.standardError = stderr.pipe
      for stream in streams { stream.start() }
    }

    func cancel() {
      for stream in streams { stream.requestFinish() }
      for stream in streams { stream.finish() }
    }

    func finish(process: Process, bundle: URL, transcript: URL) -> String {
      for stream in streams { stream.requestFinish() }
      for stream in streams { stream.finish() }
      for (source, name) in [
        (bundle.appendingPathComponent("installer.log"), "installer.log"),
        (transcript, "transcript.jsonl"),
      ] {
        preserve(source, as: name)
      }
      preserve(bundle.appendingPathComponent("version.tag"), as: "engine-version.txt")
      let failure = streams.compactMap(\.failure).joined(separator: "; ")
      let status =
        "Operation: \(operation)\nFinished: \(Date().ISO8601Format())\nExit: \(process.terminationStatus)\nReason: \(process.terminationReason.rawValue)\n\(failure)\n"
      try? write(status, name: "result.log")
      let stderr = streams.last?.text ?? ""
      let stdout = streams.first?.text ?? ""
      let output = stderr.isEmpty ? stdout : stderr
      return String(output.suffix(2048))
    }

    private func preserve(_ source: URL, as name: String) {
      let descriptor = Darwin.open(source.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
      guard descriptor >= 0 else { return }
      defer { Darwin.close(descriptor) }
      var status = stat()
      guard fstat(descriptor, &status) == 0, status.st_mode & S_IFMT == S_IFREG else { return }
      let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
      let limit = EngineDiagnosticStream.limit + secret.count
      if status.st_size > limit { _ = lseek(descriptor, status.st_size - off_t(limit), SEEK_SET) }
      guard let data = try? handle.read(upToCount: limit) else { return }
      var redactor = DiagnosticRedactor(secret: secret)
      let safe = redactor.append(data, final: true)
      try? write(
        String(decoding: safe.suffix(EngineDiagnosticStream.limit), as: UTF8.self), name: name)
    }

    private func write(_ text: String, name: String) throws {
      let url = directory.appendingPathComponent(name)
      let descriptor = Darwin.open(
        url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
      guard descriptor >= 0 else { throw PinnedAsahiEngineExecutionError.unsafeTranscript }
      let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
      try handle.write(contentsOf: Data(text.utf8))
      try handle.close()
    }
  }

  /// Keeps possible password prefixes until the next chunk, so split writes
  /// cannot leak credentials into a log. Output is already redacted before retention.
  struct DiagnosticRedactor {
    let secret: Data
    private var pending = Data()

    init(secret: Data) { self.secret = secret }

    mutating func append(_ data: Data, final: Bool = false) -> Data {
      pending.append(data)
      guard !secret.isEmpty else {
        defer { pending.removeAll(keepingCapacity: true) }
        return pending
      }
      var safe = Data()
      while let range = pending.range(of: secret) {
        safe.append(pending[..<range.lowerBound])
        safe.append(Data("[REDACTED]".utf8))
        pending.removeSubrange(..<range.upperBound)
      }
      let count = final ? pending.count : max(0, pending.count - secret.count + 1)
      safe.append(pending.prefix(count))
      pending.removeFirst(count)
      return safe
    }
  }

  /// One worker owns each stream. finish() joins it before reading its state.
  final class EngineDiagnosticStream: @unchecked Sendable {
    static let limit = 1_048_576
    let pipe = Pipe()
    private let handle: FileHandle
    private let group = DispatchGroup()
    private let lock = NSLock()
    private var drainDeadline: UInt64?
    private var redactor: DiagnosticRedactor
    private var tail = Data()
    private(set) var failure: String?
    var text: String { String(decoding: tail, as: UTF8.self) }

    init(url: URL, secret: Data) throws {
      redactor = DiagnosticRedactor(secret: secret)
      let descriptor = Darwin.open(
        url.path, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
      guard descriptor >= 0 else { throw PinnedAsahiEngineExecutionError.unsafeTranscript }
      handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }

    func start() {
      group.enter()
      DispatchQueue.global(qos: .utility).async { [self] in
        defer {
          try? pipe.fileHandleForReading.close()
          try? handle.close()
          group.leave()
        }
        let descriptor = pipe.fileHandleForReading.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 else {
          failure = "Could not configure engine output capture"
          return
        }
        var buffer = [UInt8](repeating: 0, count: 65_536)
        while true {
          if let deadline = lock.withLock({ drainDeadline }),
            DispatchTime.now().uptimeNanoseconds >= deadline
          {
            failure = "Output drain stopped: a descendant kept the pipe open after engine exit"
            break
          }
          let count = Darwin.read(descriptor, &buffer, buffer.count)
          if count > 0 {
            retain(redactor.append(Data(buffer.prefix(count))))
          } else if count == 0 {
            break
          } else if errno == EAGAIN || errno == EINTR {
            var descriptorEvent = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
            _ = poll(&descriptorEvent, 1, 100)
          } else {
            failure = "Could not read engine output (errno \(errno))"
            break
          }
        }
        retain(redactor.append(Data(), final: true))
      }
    }

    func requestFinish() {
      lock.withLock { drainDeadline = DispatchTime.now().uptimeNanoseconds + 2_000_000_000 }
      try? pipe.fileHandleForWriting.close()
    }

    func finish() { group.wait() }

    private func retain(_ data: Data) {
      tail.append(data)
      if tail.count > Self.limit { tail.removeFirst(tail.count - Self.limit) }
      do {
        try handle.seek(toOffset: 0)
        try handle.write(contentsOf: tail)
        try handle.truncate(atOffset: UInt64(tail.count))
      } catch { failure = "Could not persist engine output: \(error)" }
    }
  }
#endif
