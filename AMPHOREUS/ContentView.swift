import SwiftUI

enum PanelState {
    case collapsed
    case expanded
    case fullscreen
}

struct ContentView: View {
    @State private var appState = AppState()
    @State private var panelState: PanelState = .collapsed

    private var sessionManager: SessionManager { appState.sessionManager }
    private var processManager: ProcessManager { appState.processManager }

    var body: some View {
        NavigationSplitView {
            SidebarView()
                .navigationSplitViewColumnWidth(240)
        } detail: {
            ZStack {
                VStack(spacing: 0) {
                    if !processManager.isRunning && processManager.logLines.isEmpty {
                        emptyState
                    } else {
                        TerminalView()
                            .padding(12)
                            .frame(maxHeight: panelState == .fullscreen ? 150 : nil)
                            .opacity(panelState == .fullscreen ? 0.3 : 1.0)
                    }

                    if processManager.isPaused {
                        CommandReferenceView()
                            .padding(.horizontal, 12)
                            .padding(.bottom, 8)
                    }

                    if panelState == .expanded || panelState == .fullscreen {
                        if panelState == .expanded { Spacer() }
                        PerformanceView(
                            mode: panelState == .fullscreen ? .fullscreen : .expanded,
                            onCollapse: {
                                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                    panelState = .collapsed
                                }
                            },
                            onToggleFullscreen: {
                                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                    panelState = panelState == .fullscreen ? .expanded : .fullscreen
                                }
                            }
                        )
                        .padding(.horizontal, 20)
                        .padding(.bottom, panelState == .fullscreen ? 12 : 16)
                        .padding(.top, panelState == .fullscreen ? 12 : 0)
                        .frame(
                            maxWidth: .infinity,
                            maxHeight: panelState == .fullscreen ? .infinity : nil
                        )
                        .layoutPriority(panelState == .fullscreen ? 1 : 0)
                    }
                }

                // 收起态悬浮按钮
                if panelState == .collapsed {
                    VStack {
                        Spacer()
                        HStack {
                            Spacer()
                            Button {
                                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                    panelState = .expanded
                                }
                            } label: {
                                Image(systemName: "cpu")
                                    .font(.title3)
                            }
                            .buttonStyle(.plain)
                            .padding(10)
                            .background(
                            Circle()
                                .fill(.regularMaterial)
                                .overlay(Circle().fill(.white.opacity(0.4)))
                                .overlay(Circle().fill(.blue.opacity(0.03)))
                                .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
                        )
                            .overlay(
                                Circle().stroke(.white.opacity(0.15), lineWidth: 1)
                            )
                        }
                        .padding(12)
                    }
                }
            }
            .background(GlassEffectView(material: .hudWindow).ignoresSafeArea())
        }
        .navigationTitle("")
        .toolbar(content: mainToolbar)
        .environment(sessionManager)
        .environment(processManager)
    }

    @ToolbarContentBuilder
    private func mainToolbar() -> some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            glassCircleButton("play.fill") {
                startNewRun()
            }
            .disabled(processManager.isRunning)

            glassCircleButton(
                processManager.isPaused ? "play.fill" : "pause.fill"
            ) {
                if processManager.isPaused {
                    processManager.resume()
                } else {
                    processManager.pause()
                }
            }
            .disabled(!processManager.isRunning)

            glassCircleButton("stop.fill") {
                processManager.stop()
            }
            .disabled(!processManager.isRunning)
        }
        ToolbarItem(id: "metrics", placement: .primaryAction) {
            if let metrics = processManager.latestMetrics {
                HStack(spacing: 16) {
                    Text("Gen \(metrics.gen)").font(.caption)
                    Text("Pop \(metrics.pop)").font(.caption)
                    Text("\(metrics.threads) 线程").font(.caption)
                }
                .foregroundStyle(.secondary)
            }
        }
        ToolbarItem(id: "new", placement: .primaryAction) {
            glassCircleButton("plus") {
                prepareNewRun()
            }
            .disabled(processManager.isRunning || processManager.isPrepared)
        }
    }

    private func glassCircleButton(
        _ systemName: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(.regularMaterial)
                    .overlay(Circle().fill(.white.opacity(0.4)))
                    .overlay(Circle().fill(.blue.opacity(0.03)))
                    .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
                    .frame(width: 26, height: 26)

                Circle()
                    .stroke(.white.opacity(0.15), lineWidth: 1)
                    .frame(width: 26, height: 26)

                Image(systemName: systemName)
                    .font(.title3)
            }.frame(minWidth: 35)
        }
        .buttonStyle(.plain)
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "sparkles")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)
            Text("翁法罗斯")
                .font(.title).fontWeight(.medium)
            Text("点击右上角 + 新建推演")
                .font(.callout).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func prepareNewRun() {
        processManager.logLines = []
        processManager.isPrepared = true
    }

    private func startNewRun() {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd-HH-mm-ss"
        let runID = fmt.string(from: Date())

        // 优先使用 App Bundle 内的 δ-me13，开发时回退到 iCloud 目录
        let projectDir: String
        if let bundled = ProcessManager.bundledProjectDir {
            projectDir = bundled
        } else {
            let docPath = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
            projectDir = docPath.deletingLastPathComponent()
                .appendingPathComponent("AMPHOREUS/δ-me13").path
        }

        sessionManager.refreshSessions()
        sessionManager.markRunning(runID)
        processManager.start(projectDir: projectDir, runDir: "")
    }
}
