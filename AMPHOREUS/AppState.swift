import SwiftUI

/// 集中持有所有 Observable 状态对象
/// 由 ContentView 创建并通过 .environment() 注入子视图链
@Observable
final class AppState {
    var sessionManager = SessionManager()
    var processManager = ProcessManager()
}
