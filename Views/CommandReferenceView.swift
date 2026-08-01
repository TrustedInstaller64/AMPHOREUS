import SwiftUI

struct CommandReferenceView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("暂停后可用命令", systemImage: "terminal")
                .font(.subheadline)
                .fontWeight(.semibold)
                .padding(.bottom, 4)

            ForEach(commands, id: \.0) { cmd, desc in
                HStack(alignment: .top, spacing: 8) {
                    Text(cmd)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.yellow)
                        .frame(width: 120, alignment: .leading)
                    Text(desc)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.ultraThinMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(.white.opacity(0.1), lineWidth: 1)
        )
    }

    private let commands: [(String, String)] = [
        ("c, continue", "继续模拟"),
        ("n, next", "执行下一世代并暂停"),
        ("p <name|baie>", "打印指定实体或卡厄斯兰那详情"),
        ("top [k]", "显示评分最高的 k 个实体"),
        ("status", "显示当前翁法罗斯宏观状态"),
        ("zeitgeist", "查看当前思潮权重"),
        ("blueprint", "查看当前演化蓝图"),
        ("set <k> <v>", "动态设置模拟参数"),
        ("save <file>", "将当前状态保存到文件"),
        ("load <file>", "从文件加载模拟状态"),
        ("help", "显示所有可用命令"),
    ]
}
