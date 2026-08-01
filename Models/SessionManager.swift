import SwiftUI
import Foundation

struct SessionRecord: Identifiable, Hashable {
    let id: String
    let url: URL
    let timestamp: Date
    var status: Status

    enum Status: String, CaseIterable {
        case running   = "运行中"
        case completed = "已完成"
        case interrupted = "已中断"

        var color: Color {
            switch self {
            case .running:    return .green
            case .completed:  return .secondary
            case .interrupted: return .orange
            }
        }
    }
}

@Observable
final class SessionManager {
    var sessions: [SessionRecord] = []
    var selectedSession: SessionRecord?

    private let runsURL: URL

    init() {
        let docPath = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        // 从 Documents 回退到项目根，再进入 δ-me13/runs/
        let projectRoot = docPath.deletingLastPathComponent()
        let _runsURL = projectRoot
            .appendingPathComponent("δ-me13")
            .appendingPathComponent("runs")
        self.runsURL = _runsURL
        refreshSessions()
    }

    func refreshSessions() {
        guard FileManager.default.fileExists(atPath: runsURL.path) else {
            sessions = []
            return
        }
        do {
            let dirs = try FileManager.default.contentsOfDirectory(at: runsURL, includingPropertiesForKeys: [.creationDateKey], options: [.skipsHiddenFiles])
            sessions = dirs
                .filter { $0.hasDirectoryPath }
                .compactMap { url in
                    let name = url.lastPathComponent
                    let fmt = DateFormatter()
                    fmt.dateFormat = "yyyy-MM-dd-HH-mm-ss"
                    guard let date = fmt.date(from: name) else { return nil }
                    return SessionRecord(id: name, url: url, timestamp: date, status: .completed)
                }
                .sorted { $0.timestamp > $1.timestamp }
        } catch {
            sessions = []
        }
    }

    func markRunning(_ id: String) {
        if let idx = sessions.firstIndex(where: { $0.id == id }) {
            sessions[idx].status = .running
        }
    }

    func markCompleted(_ id: String) {
        if let idx = sessions.firstIndex(where: { $0.id == id }) {
            sessions[idx].status = .completed
        }
    }
}
