import SwiftUI
import Foundation

@Observable
final class ProcessManager {
    var isRunning = false
    var isPaused = false
    var isPrepared = false
    var logLines: [String] = []
    var latestMetrics: MetricsSnapshot?
    var metricsHistory: [MetricsSnapshot] = []
    var pythonPath: String

    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private var fd3Pipe: Pipe?
    private var logLock = NSLock()

    static let bundledPythonPath: String = {
        let fm = FileManager.default
#if DEBUG
        // Debug: use system Python (has all site-packages)
        let sysPy = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
        if fm.fileExists(atPath: sysPy) { return sysPy }
        return "/usr/bin/python3"
#else
        // Release: use bundled Python.framework
        if let fw = Bundle.main.privateFrameworksURL {
            let py = fw.appendingPathComponent("Python.framework/Versions/3.12/bin/python3").path
            if fm.fileExists(atPath: py) { return py }
        }
        return "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
#endif
    }()

    static let bundledProjectDir: String? = {
        let fm = FileManager.default
        guard let res = Bundle.main.resourceURL else { return nil }
        // PBXFileSystemSynchronizedRootGroup flattens δ-me13 into Resources root
        if fm.fileExists(atPath: res.appendingPathComponent("main.py").path) {
            return res.path
        }
        // Fallback: δ-me13 as subdirectory
        let sub = res.appendingPathComponent("δ-me13")
        if fm.fileExists(atPath: sub.appendingPathComponent("main.py").path) {
            return sub.path
        }
        return nil
    }()

    init(pythonPath: String = bundledPythonPath) {
        self.pythonPath = pythonPath
    }

    struct MetricsSnapshot {
        var gen: Int = 0
        var pop: Int = 0
        var cpuPct: Double = 0
        var memPct: Double = 0
        var cpuPowerMW: Int = 0
        var gpuPowerMW: Int = 0
        var anePowerMW: Int = 0
        var threads: Int = 0
        var emaIdle: Double = 0
    }

    func start(projectDir: String, runDir: String) {
        guard !isRunning else { return }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: pythonPath)
        task.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        task.arguments = ["main.py", "--gui"]

        // PYTHONPATH: include user site-packages (sandbox blocks default discovery)
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = ("~/Library/Python/3.12/lib/python/site-packages" as NSString).expandingTildeInPath
        task.environment = env

        let stdinP = Pipe()
        task.standardInput = stdinP
        stdinPipe = stdinP

        let stdoutP = Pipe()
        task.standardOutput = stdoutP
        stdoutPipe = stdoutP

        let stderrP = Pipe()
        task.standardError = stderrP
        stderrPipe = stderrP

        let fd3P = Pipe()
        fd3Pipe = fd3P
        dup2(fd3P.fileHandleForWriting.fileDescriptor, 3)

        task.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                self?.isRunning = false
                self?.isPrepared = false
                self?.appendLog("[System] Python 进程已退出")
                self?.appendLog("\u{2016}P10K\u{2016}trustedinstaller@Mac\u{2016}amphoreus\u{2016}main\u{2016}")
                self?.appendLog("\u{1B}[32m>\u{1B}[0m")
            }
        }

        process = task
        isRunning = true
        isPaused = false
        logLines = []
        appendLog("\u{2016}P10K\u{2016}trustedinstaller@Mac\u{2016}amphoreus\u{2016}main\u{2016}")
        appendLog("\u{1B}[32m>\u{1B}[0m python main.py --gui")

        stdoutP.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let self = self else { return }
            if let str = String(data: data, encoding: .utf8) {
                self.appendLog(str)
            }
        }

        fd3P.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let self = self else { return }
            if let str = String(data: data, encoding: .utf8) {
                self.processMetricsLines(str)
            }
        }

        stderrP.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let self = self else { return }
            if let str = String(data: data, encoding: .utf8) {
                self.appendLog("[stderr] " + str)
            }
        }

        do {
            try task.run()
        } catch {
            appendLog("[Fail] 启动失败: \(error.localizedDescription)")
            appendLog("\u{2016}P10K\u{2016}trustedinstaller@Mac\u{2016}amphoreus\u{2016}main\u{2016}")
            appendLog("\u{1B}[32m>\u{1B}[0m")
            isRunning = false
            isPrepared = false
        }
    }

    func stop() {
        guard let proc = process, proc.isRunning else { return }
        proc.interrupt()
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            if self?.process?.isRunning == true {
                self?.process?.terminate()
            }
        }
        isRunning = false
    }

    func pause() {
        sendJSON(["type": "pause"])
        isPaused = true
    }

    func resume() {
        sendJSON(["type": "resume"])
        isPaused = false
    }

    func sendCmd(_ text: String) {
        sendJSON(["type": "cmd", "text": text])
    }

    private func sendJSON(_ dict: [String: Any]) {
        guard process?.isRunning == true,
              let data = try? JSONSerialization.data(withJSONObject: dict),
              let str = String(data: data, encoding: .utf8),
              let lineData = (str + "\n").data(using: .utf8),
              let fh = stdinPipe?.fileHandleForWriting else { return }
        // Ignore SIGPIPE to prevent crash on broken pipe
        signal(SIGPIPE, SIG_IGN)
        fh.write(lineData)
    }

    func appendLog(_ text: String) {
        logLock.lock()
        defer { logLock.unlock() }
        for line in text.components(separatedBy: "\n") where !line.isEmpty {
            logLines.append(line)
        }
        if logLines.count > 5000 {
            logLines.removeFirst(logLines.count - 5000)
        }
    }

    private func processMetricsLines(_ text: String) {
        for raw in text.components(separatedBy: "\n") {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty,
                  let data = trimmed.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  json["type"] as? String == "metrics" else { continue }

            var snap = MetricsSnapshot()
            snap.gen = json["gen"] as? Int ?? 0
            snap.pop = json["pop"] as? Int ?? 0
            snap.cpuPct = json["cpu_pct"] as? Double ?? 0
            snap.memPct = json["mem_pct"] as? Double ?? 0
            snap.cpuPowerMW = json["cpu_power_mw"] as? Int ?? 0
            snap.gpuPowerMW = json["gpu_power_mw"] as? Int ?? 0
            snap.anePowerMW = json["ane_power_mw"] as? Int ?? 0
            snap.threads = json["threads"] as? Int ?? 0
            snap.emaIdle = json["ema_idle"] as? Double ?? 0
            latestMetrics = snap
            metricsHistory.append(snap)
            if metricsHistory.count > 300 {
                metricsHistory.removeFirst(metricsHistory.count - 300)
            }
        }
    }
}
