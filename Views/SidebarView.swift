import SwiftUI

struct SidebarView: View {
    @Environment(SessionManager.self) private var sessionManager
    @Environment(ProcessManager.self) private var processManager

    var body: some View {
        List(selection: Binding(
            get: { sessionManager.selectedSession?.id },
            set: { id in
                sessionManager.selectedSession = sessionManager.sessions.first { $0.id == id }
            }
        )) {
            Section {
                Button {
                    processManager.logLines = []
                    processManager.isPrepared = true
                } label: {
                    Label("新推演", systemImage: "sparkles")
                }
                .buttonStyle(.plain)
                .disabled(processManager.isRunning || processManager.isPrepared)
            }

            Section("推演记录") {
                if sessionManager.sessions.isEmpty {
                    Text("暂无记录")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                } else {
                    ForEach(sessionManager.sessions) { session in
                        HStack {
                            Image(systemName: session.status == .running ? "circle.fill" : "circle")
                                .font(.caption2)
                                .foregroundStyle(session.status.color)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(formatTimestamp(session.timestamp))
                                    .font(.callout)
                                Text(session.status.rawValue)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .tag(session.id)
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            if processManager.isRunning {
                HStack {
                    Image(systemName: "circle.fill")
                        .font(.caption2)
                        .foregroundStyle(.green)
                    Text("推演运行中")
                        .font(.caption)
                }
                .padding(.vertical, 4)
                .frame(maxWidth: .infinity)
                .background(.ultraThinMaterial)
            }
        }
    }

    private func formatTimestamp(_ date: Date) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return fmt.string(from: date)
    }
}
