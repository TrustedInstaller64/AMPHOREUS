import SwiftUI

// MARK: - Keyword-based log coloring

private struct ColoredLine {
    let segments: [Segment]

    struct Segment: Identifiable {
        let id = UUID()
        let text: String
        let color: Color
        let bg: Color?
        let bold: Bool

        var hasTriangle = false

        init(text: String, color: Color, bold: Bool = false, bg: Color? = nil, hasTriangle: Bool = false) {
            self.text = text; self.color = color; self.bold = bold; self.bg = bg; self.hasTriangle = hasTriangle
        }
    }
}

private func colorizeLine(_ raw: String) -> ColoredLine {
    if raw.hasPrefix(p10kMarker) {
        return parseP10K(String(raw.dropFirst(p10kMarker.count)))
    }
    guard raw.contains("\u{1B}[") else {
        return ColoredLine(segments: [ColoredLine.Segment(
            text: raw,
            color: keywordColor(for: raw),
            bold: false
        )])
    }
    return parseAnsi(raw)
}

private let p10kMarker = "\u{2016}P10K\u{2016}"

private func parseP10K(_ content: String) -> ColoredLine {
    let parts = content.components(separatedBy: "\u{2016}")
    guard parts.count >= 4 else {
        return ColoredLine(segments: [ColoredLine.Segment(text: content, color: .primary.opacity(0.85), bold: false)])
    }
    let p10kBlue   = Color(red: 0.15, green: 0.55, blue: 0.85)
    let p10kCyan   = Color(red: 0.10, green: 0.65, blue: 0.70)
    let p10kPurple = Color(red: 0.50, green: 0.45, blue: 0.85)
    return ColoredLine(segments: [
        ColoredLine.Segment(text: " \(parts[0]) ", color: .white, bg: p10kBlue),
        ColoredLine.Segment(text: " \(parts[1]) ", color: .white, bg: p10kCyan),
        ColoredLine.Segment(text: " \(parts[2]) ", color: .white, bg: p10kPurple, hasTriangle: true),
    ])
}

private func keywordColor(for line: String) -> Color {
    if line.contains("[Fail]")    || line.contains("FAIL")            { return .red }
    if line.contains("[错误]")     || line.contains("错误")              { return .red }
    if line.contains("[System]")                                       { return .gray }
    if line.hasPrefix("\u{1B}[94m")                                    { return .blue }
    if line.contains("[DEBUG]")                                        { return .blue }
    if line.contains("卡厄斯兰那") || line.contains("白厄")
        || line.contains("轮回")                                       { return .orange }
    return .primary.opacity(0.85)
}

// MARK: - ANSI Parser (16 color + 256 color + bold)

private struct AnsiState {
    var color: Color = .primary.opacity(0.85)
    var bold = false
}

private func parseAnsi(_ raw: String) -> ColoredLine {
    var segments: [ColoredLine.Segment] = []
    var current = ""
    var state = AnsiState()
    var i = raw.startIndex

    while i < raw.endIndex {
        if raw[i] == "\u{1B}",
           raw.index(after: i) < raw.endIndex,
           raw[raw.index(after: i)] == "[" {

            if !current.isEmpty {
                let c = state.color == .primary.opacity(0.85)
                    ? keywordColor(for: current)
                    : state.color
                segments.append(ColoredLine.Segment(text: current, color: c, bold: state.bold))
                current = ""
            }
            i = raw.index(i, offsetBy: 2)
            var code = ""
            while i < raw.endIndex, raw[i] != "m" {
                code.append(raw[i])
                i = raw.index(after: i)
            }
            if i < raw.endIndex { i = raw.index(after: i) }

            if code == "0" || code == "00" {
                state = AnsiState()
            } else {
                for sub in code.components(separatedBy: ";") {
                    switch sub {
                    case "1":  state.bold = true
                    case "22": state.bold = false
                    case "30": state.color = .black
                    case "31": state.color = .red
                    case "32": state.color = .green
                    case "33": state.color = .yellow
                    case "34": state.color = .blue
                    case "35": state.color = .purple
                    case "36": state.color = .cyan
                    case "37": state.color = .white
                    case "90": state.color = .gray
                    case "91": state.color = .red.opacity(0.8)
                    case "92": state.color = .green.opacity(0.8)
                    case "93": state.color = .yellow.opacity(0.8)
                    case "94": state.color = .blue.opacity(0.8)
                    case "95": state.color = .purple.opacity(0.8)
                    case "96": state.color = .cyan.opacity(0.8)
                    case "97": state.color = .white
                    default: break
                    }
                }
            }
        } else {
            current.append(raw[i])
            i = raw.index(after: i)
        }
    }
    if !current.isEmpty {
        let c = state.color == .primary.opacity(0.85)
            ? keywordColor(for: current)
            : state.color
        segments.append(ColoredLine.Segment(text: current, color: c, bold: state.bold))
    }
    return ColoredLine(segments: segments)
}

// MARK: - Right-pointing Triangle (powerline separator)

private struct RightTriangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: .zero)
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: 0, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}

// MARK: - Left Semicircle Shape

private struct LeftSemicircle: Shape {
    func path(in rect: CGRect) -> Path {
        let r = rect.height / 2
        var p = Path()
        p.move(to: CGPoint(x: rect.minX + r, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.minX + r, y: rect.minY))
        p.addArc(center: CGPoint(x: rect.minX + r, y: rect.midY),
                 radius: r,
                 startAngle: .degrees(90),
                 endAngle: .degrees(270),
                 clockwise: false)
        p.closeSubpath()
        return p
    }
}

// MARK: - TerminalView

struct TerminalView: View {
    @Environment(ProcessManager.self) private var processManager
    @State private var commandInput = ""

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(processManager.logLines.enumerated()), id: \.offset) { _, line in
                        coloredLineView(colorizeLine(line))
                    }

                    if processManager.isPaused {
                        p10kPromptLine
                    }

                    Color.clear.frame(height: 0).id("logEnd")
                }
                .padding(8)
            }
            .onChange(of: processManager.logLines.count) { _, _ in
                DispatchQueue.main.async {
                    proxy.scrollTo("logEnd", anchor: .bottom)
                }
            }
        }
    }

    // MARK: - Colored line (no deprecated Text + Text)

    @ViewBuilder
    private func coloredLineView(_ line: ColoredLine) -> some View {
        if line.segments.count == 1, let seg = line.segments.first, seg.bg == nil {
            Text(seg.text)
                .font(.system(size: 12, weight: seg.bold ? .bold : .regular, design: .monospaced))
                .foregroundStyle(seg.color)
                .textSelection(.enabled)
        } else {
            HStack(spacing: 0) {
                ForEach(Array(line.segments.enumerated()), id: \.element.id) { idx, seg in
                    Text(seg.text)
                        .font(.system(size: 12, weight: seg.bold ? .bold : .regular, design: .monospaced))
                        .foregroundStyle(seg.color)
                        .padding(.leading, idx == 0 && seg.bg != nil ? 2 : 0)
                        .background(seg.bg ?? .clear)
                        .clipShape(
                            idx == 0 && seg.bg != nil
                            ? AnyShape(LeftSemicircle())
                            : AnyShape(Rectangle())
                        )
                }

                if let bg = line.segments.last?.bg, line.segments.last?.hasTriangle == true {
                    RightTriangle()
                        .fill(bg)
                        .frame(width: 9, height: 15)

                    Rectangle()
                        .fill(Color.gray.opacity(0.35))
                        .frame(height: 1)
                        .frame(maxWidth: .infinity)
                }
            }
            .textSelection(.enabled)
        }
    }

    // MARK: - powerlevel10k Prompt

    private var p10kPromptLine: some View {
        let p10kBlue   = Color(red: 0.15, green: 0.55, blue: 0.85)
        let p10kCyan   = Color(red: 0.10, green: 0.65, blue: 0.70)
        let p10kPurple = Color(red: 0.50, green: 0.45, blue: 0.85)

        return VStack(alignment: .leading, spacing: 0) {
            // Line 1: colored segments + triangle + gray line to edge
            HStack(spacing: 0) {
                p10kSeg(" trustedinstaller@Mac ", bg: p10kBlue, isFirst: true)
                p10kSeg(" amphoreus ", bg: p10kCyan)

                Text(" main ")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.white)
                    .padding(.vertical, 3)
                    .padding(.horizontal, 6)
                    .background(p10kPurple)

                RightTriangle()
                    .fill(p10kPurple)
                    .frame(width: 9, height: 17)

                // Gray line to right edge
                Rectangle()
                    .fill(Color.gray.opacity(0.35))
                    .frame(height: 1)
            }

            // Line 2: > prompt + input
            HStack(spacing: 0) {
                Text(" > ")
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(.green)

                TextField("", text: $commandInput)
                    .textFieldStyle(.plain)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(.primary)
                    .onSubmit {
                        guard !commandInput.isEmpty, processManager.isPaused else { return }
                        processManager.sendCmd(commandInput)
                        commandInput = ""
                    }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
    }

    private func p10kSeg(_ label: String, bg: Color, isFirst: Bool = false) -> some View {
        Text(label)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.white)
            .padding(.vertical, 3)
            .padding(.horizontal, 6)
            .padding(.leading, isFirst ? 2 : 0)
            .background(bg)
            .clipShape(isFirst ? AnyShape(LeftSemicircle()) : AnyShape(Rectangle()))
    }

}
