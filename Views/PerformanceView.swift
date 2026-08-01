import SwiftUI
import AppKit
import Charts

extension View {
    @ViewBuilder
    func `if`<Content: View>(_ condition: Bool, transform: (Self) -> Content) -> some View {
        if condition { transform(self) } else { self }
    }
}

enum PerformancePanelMode {
    case expanded
    case fullscreen
}

struct PerformanceView: View {
    @Environment(ProcessManager.self) private var processManager
    let mode: PerformancePanelMode
    var onCollapse: (() -> Void)? = nil
    var onToggleFullscreen: (() -> Void)? = nil

    private var chartHeight: CGFloat {
        mode == .fullscreen ? 200 : 90
    }

    var body: some View {
        VStack(spacing: 12) {
            headerRow
            chartContent
        }
        .padding(18)
        .background {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(.white.opacity(0.4))
                .overlay {
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .fill(.blue.opacity(0.03))
                }
                .overlay {
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .stroke(Color.primary.opacity(0.08), lineWidth: 1)
                }
                .shadow(color: .black.opacity(0.15), radius: 20, y: 8)
        }
    }

    // MARK: - Header

    private var headerRow: some View {
        HStack(spacing: 10) {
            glassCircleButton("chevron.down") { onCollapse?() }

            Text("性能监视器")
                .font(.headline)
                .foregroundStyle(.primary.opacity(0.7))

            if let m = processManager.latestMetrics {
                Text("Gen \(m.gen) · Pop \(m.pop) · \(m.threads)线程")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            glassCircleButton(
                mode == .fullscreen
                    ? "arrow.down.right.and.arrow.up.left"
                    : "arrow.up.left.and.arrow.down.right"
            ) { onToggleFullscreen?() }
        }
    }

    // MARK: - Glass Circle Button

    private func glassCircleButton(
        _ systemName: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
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
        .overlay {
            Circle().stroke(.white.opacity(0.15), lineWidth: 1)
        }
    }

    // MARK: - Charts

    @ViewBuilder
    private var chartContent: some View {
        let history = processManager.metricsHistory

        if history.isEmpty {
            VStack(spacing: 8) {
                Text("等待数据…")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxHeight: .infinity)
        } else {
            chartCard("CPU / Memory %") {
                Chart(Array(history.enumerated()), id: \.offset) { i, snap in
                    LineMark(x: .value("t", i), y: .value("cpu", snap.cpuPct))
                        .foregroundStyle(.blue)
                    AreaMark(x: .value("t", i), y: .value("cpu", snap.cpuPct))
                        .foregroundStyle(.blue.opacity(0.08))

                    LineMark(x: .value("t", i), y: .value("mem", snap.memPct))
                        .foregroundStyle(.green)
                    AreaMark(x: .value("t", i), y: .value("mem", snap.memPct))
                        .foregroundStyle(.green.opacity(0.08))
                }
            }

            chartCard("功耗 (mW)") {
                Chart(Array(history.enumerated()), id: \.offset) { i, snap in
                    LineMark(x: .value("t", i), y: .value("cpu", snap.cpuPowerMW))
                        .foregroundStyle(.blue)
                    LineMark(x: .value("t", i), y: .value("gpu", snap.gpuPowerMW))
                        .foregroundStyle(.orange)
                    LineMark(x: .value("t", i), y: .value("ane", snap.anePowerMW))
                        .foregroundStyle(.purple)
                }
            }
        }
    }

    @ViewBuilder
    private func chartCard<V: View>(
        _ title: String,
        @ViewBuilder chart: () -> V
    ) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            chart()
                .chartXAxis(.hidden)
                .chartYAxis {
                    AxisMarks { _ in
                        AxisValueLabel()
                            .font(.system(size: 8))
                    }
                }
                .frame(maxWidth: .infinity)
                .if(mode == .fullscreen) { $0.frame(maxHeight: .infinity) }
                .if(mode != .fullscreen) { $0.frame(height: chartHeight) }
        }
    }
}
