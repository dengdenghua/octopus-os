import type { Locale } from "@/core/i18n";

type SafetyProfile = "strict" | "normal" | "lax";
type IdentitySource = "runtime" | "env" | "default";

interface SettingsUxCopy {
  observability: {
    title: string;
    description: string;
    activityTitle: string;
    activityDescription: string;
    tracesTitle: string;
    tracesDescription: string;
    healthTitle: string;
    healthDescription: string;
    openDashboard: string;
  };
  privacy: {
    identityTitle: string;
    identityDescription: string;
    identityOn: string;
    identityOff: string;
    enableIdentity: string;
    disableIdentity: string;
    sourceLabel: string;
    sources: Record<IdentitySource, string>;
    profileTitle: string;
    profileDescription: string;
    profileDocLabel: string;
    profiles: Record<SafetyProfile, { label: string; description: string }>;
    active: string;
    judgeTitle: string;
    judgeDescription: string;
    judgeUnavailable: string;
    judgeSwitchLabel: string;
    advancedTitle: string;
    advancedSummary: string;
    advancedDescription: string;
    advancedEnvironment: string;
    advancedTurn: string;
    advancedApi: string;
    loading: string;
    loadFailed: string;
    retry: string;
    restoreFailed: string;
    removePathTitle: string;
    removePathDescription: (path: string) => string;
    removePathConfirm: string;
  };
  mcp: {
    title: string;
    description: string;
    loading: string;
    loadFailed: string;
    retry: string;
    trustLoadFailed: string;
    trustUnknown: string;
    noServers: string;
    nameLabel: string;
    urlLabel: string;
    tokenLabel: string;
    tokenHint: string;
    add: string;
    adding: string;
    invalidUrl: string;
    duplicateName: (name: string) => string;
    toggleLabel: (name: string) => string;
    trustLabel: (name: string) => string;
    revokeLabel: (name: string) => string;
    activationFailed: (name: string, detail?: string) => string;
    runtimeError: (detail: string) => string;
    removeLabel: (name: string) => string;
    removeTitle: string;
    removeDescription: (name: string) => string;
    removeConfirm: string;
    removeSuccess: (name: string) => string;
    removeFailed: string;
  };
}

const COPY: Record<Locale, SettingsUxCopy> = {
  "zh-CN": {
    observability: {
      title: "运行可观测性",
      description:
        "集中查看任务执行、操作记录和运行状态。设置窗口只提供入口，完整数据会在独立工作台中展示。",
      activityTitle: "实时活动",
      activityDescription: "跟踪正在执行的任务、步骤与事件。",
      tracesTitle: "操作与文件轨迹",
      tracesDescription: "核对操作记录、文件改动和执行结果。",
      healthTitle: "运行健康",
      healthDescription: "定位失败、延迟与异常状态。",
      openDashboard: "打开可观测性工作台",
    },
    privacy: {
      identityTitle: "产品身份保护",
      identityDescription:
        "开启后，回复会统一使用 Echo 的产品身份，不展示底层模型或服务商名称。仅在排查模型路由时临时关闭。",
      identityOn: "已开启",
      identityOff: "已关闭",
      enableIdentity: "开启产品身份保护",
      disableIdentity: "关闭产品身份保护",
      sourceLabel: "当前策略",
      sources: {
        runtime: "本次运行已修改",
        env: "由启动配置决定",
        default: "使用系统默认",
      },
      profileTitle: "外发安全级别",
      profileDescription:
        "控制内容离开本机前的检查强度。账号凭证和密钥在所有级别下都会被拦截。",
      profileDocLabel: "查看规则说明",
      profiles: {
        strict: {
          label: "严格",
          description:
            "适合对外发布、共享设备或多人使用，会主动改写敏感个人信息。",
        },
        normal: {
          label: "均衡",
          description:
            "适合个人日常使用，拦截明确风险，同时保留较流畅的工作体验。",
        },
        lax: {
          label: "宽松",
          description: "适合受信任的内部环境，仅强制拦截账号凭证和密钥。",
        },
      },
      active: "当前",
      judgeTitle: "智能语义审查",
      judgeDescription:
        "发送前再进行一次内容风险检查，可识别诱导泄密和越权抓取；会增加少量等待时间和模型用量。",
      judgeUnavailable: "当前没有可用模型，暂时无法开启。",
      judgeSwitchLabel: "启用智能语义审查",
      advancedTitle: "高级调试方式",
      advancedSummary: "临时查看底层模型身份",
      advancedDescription:
        "这些方式面向开发和排障。日常使用无需修改，错误配置可能让底层服务商名称出现在回复中。",
      advancedEnvironment: "启动时关闭：设置 ECHO_IDENTITY_LOCK=0。",
      advancedTurn: "仅当前消息：在消息开头输入 /raw。",
      advancedApi: "接口调用：在请求上下文中设置 raw_identity: true。",
      loading: "正在读取当前设置…",
      loadFailed: "暂时无法读取这项设置。原有配置没有被更改。",
      retry: "重新加载",
      restoreFailed: "操作失败，请稍后重试。",
      removePathTitle: "删除保护路径",
      removePathDescription: (path) =>
        `移除“${path}”后，Agent 将不再自动拒绝访问该路径。`,
      removePathConfirm: "确认删除",
    },
    mcp: {
      title: "MCP 服务",
      description: "管理 MCP 等外部工具集成，让 Echo 在获得你的信任后使用其能力。",
      loading: "正在读取 MCP 服务…",
      loadFailed: "暂时无法读取 MCP 服务。现有配置没有被更改。",
      retry: "重新加载",
      trustLoadFailed: "授权状态读取失败，相关操作已暂时停用。",
      trustUnknown: "授权状态未知",
      noServers: "还没有连接 MCP 服务。可在下方添加远程服务。",
      nameLabel: "服务名称",
      urlLabel: "服务地址",
      tokenLabel: "访问令牌（可选）",
      tokenHint: "令牌只用于连接该服务，不会在列表中显示。",
      add: "添加服务",
      adding: "正在添加…",
      invalidUrl: "请输入以 http:// 或 https:// 开头的有效地址。",
      duplicateName: (name) => `已存在名为“${name}”的服务，请换一个名称。`,
      toggleLabel: (name) => `启用或停用 ${name}`,
      trustLabel: (name) => `信任 ${name}`,
      revokeLabel: (name) => `撤销对 ${name} 的信任`,
      activationFailed: (name, detail) =>
        `${name} 启动失败${detail ? `：${detail}` : "，请检查服务配置。"}`,
      runtimeError: (detail) => `运行错误：${detail}`,
      removeLabel: (name) => `移除 ${name}`,
      removeTitle: "移除 MCP 服务",
      removeDescription: (name) =>
        `将移除“${name}”的连接配置，并撤销信任与已保存的 OAuth 授权。此操作不会删除远程服务本身。`,
      removeConfirm: "移除服务",
      removeSuccess: (name) => `已移除 MCP 服务 ${name}`,
      removeFailed: "移除 MCP 服务失败，当前状态已重新加载。",
    },
  },
  "en-US": {
    observability: {
      title: "Runtime observability",
      description:
        "Review task execution, tool calls, and runtime health in one place. Full data opens in a dedicated workspace.",
      activityTitle: "Live activity",
      activityDescription: "Follow active tasks, steps, and events.",
      tracesTitle: "Tool and file traces",
      tracesDescription: "Review tool calls, file changes, and outcomes.",
      healthTitle: "Runtime health",
      healthDescription: "Find failures, latency, and abnormal states.",
      openDashboard: "Open observability workspace",
    },
    privacy: {
      identityTitle: "Product identity protection",
      identityDescription:
        "Keeps replies under the Echo product identity instead of exposing the underlying model or provider. Turn this off only while diagnosing model routing.",
      identityOn: "On",
      identityOff: "Off",
      enableIdentity: "Turn on product identity protection",
      disableIdentity: "Turn off product identity protection",
      sourceLabel: "Current policy",
      sources: {
        runtime: "Changed for this run",
        env: "Set by startup configuration",
        default: "Using the system default",
      },
      profileTitle: "Outbound safety level",
      profileDescription:
        "Controls how carefully content is checked before it leaves this device. Credentials and secrets are blocked at every level.",
      profileDocLabel: "View rule details",
      profiles: {
        strict: {
          label: "Strict",
          description:
            "Best for publishing, shared devices, or multi-user environments; sensitive personal details may be rewritten.",
        },
        normal: {
          label: "Balanced",
          description:
            "Best for everyday personal use; blocks clear risks while keeping work fluid.",
        },
        lax: {
          label: "Relaxed",
          description:
            "Best for trusted internal environments; only credentials and secrets are forcibly blocked.",
        },
      },
      active: "Current",
      judgeTitle: "Semantic risk review",
      judgeDescription:
        "Runs one extra check before sending to detect requests for secrets or unauthorized collection. This adds a little latency and model usage.",
      judgeUnavailable: "No compatible model is available right now.",
      judgeSwitchLabel: "Enable semantic risk review",
      advancedTitle: "Advanced diagnostics",
      advancedSummary: "Temporarily reveal the underlying model identity",
      advancedDescription:
        "These options are intended for development and troubleshooting. Normal use does not require them.",
      advancedEnvironment: "At startup: set ECHO_IDENTITY_LOCK=0.",
      advancedTurn: "For one message: begin the message with /raw.",
      advancedApi:
        "For API calls: set raw_identity: true in the request context.",
      loading: "Loading the current setting…",
      loadFailed:
        "This setting could not be loaded. Your existing configuration was not changed.",
      retry: "Reload",
      restoreFailed: "The operation failed. Please try again.",
      removePathTitle: "Remove protected path",
      removePathDescription: (path) =>
        `After removing “${path}”, the agent will no longer automatically deny access to it.`,
      removePathConfirm: "Remove path",
    },
    mcp: {
      title: "MCP services",
      description:
        "Connect external tool services that Echo can use after you trust them.",
      loading: "Loading MCP services…",
      loadFailed:
        "MCP services could not be loaded. Your existing configuration was not changed.",
      retry: "Reload",
      trustLoadFailed:
        "Trust status could not be loaded, so trust actions are temporarily disabled.",
      trustUnknown: "Trust status unknown",
      noServers:
        "No MCP services are connected yet. Add a remote service below.",
      nameLabel: "Service name",
      urlLabel: "Service URL",
      tokenLabel: "Access token (optional)",
      tokenHint:
        "The token is used only to connect and is not shown in the list.",
      add: "Add service",
      adding: "Adding…",
      invalidUrl: "Enter a valid address beginning with http:// or https://.",
      duplicateName: (name) => `A service named “${name}” already exists.`,
      toggleLabel: (name) => `Enable or disable ${name}`,
      trustLabel: (name) => `Trust ${name}`,
      revokeLabel: (name) => `Revoke trust for ${name}`,
      activationFailed: (name, detail) =>
        `${name} could not start${detail ? `: ${detail}` : ". Check the service configuration."}`,
      runtimeError: (detail) => `Runtime error: ${detail}`,
      removeLabel: (name) => `Remove ${name}`,
      removeTitle: "Remove MCP service",
      removeDescription: (name) =>
        `This removes the connection for “${name}”, revokes trust, and clears saved OAuth authorization. It does not delete the remote service itself.`,
      removeConfirm: "Remove service",
      removeSuccess: (name) => `Removed MCP service ${name}`,
      removeFailed:
        "The MCP service could not be removed. Its current state was reloaded.",
    },
  },
  "ja-JP": {
    observability: {
      title: "実行オブザーバビリティ",
      description:
        "タスク実行、ツール呼び出し、実行状態をまとめて確認します。詳細データは専用ワークスペースで開きます。",
      activityTitle: "リアルタイム活動",
      activityDescription: "実行中のタスク、手順、イベントを追跡します。",
      tracesTitle: "ツールとファイルの履歴",
      tracesDescription: "ツール呼び出し、ファイル変更、結果を確認します。",
      healthTitle: "実行状態",
      healthDescription: "失敗、遅延、異常な状態を特定します。",
      openDashboard: "オブザーバビリティを開く",
    },
    privacy: {
      identityTitle: "製品アイデンティティ保護",
      identityDescription:
        "基盤モデルや提供元を表示せず、Echo の製品名で応答します。モデル経路の調査時のみ一時的にオフにしてください。",
      identityOn: "オン",
      identityOff: "オフ",
      enableIdentity: "製品アイデンティティ保護をオンにする",
      disableIdentity: "製品アイデンティティ保護をオフにする",
      sourceLabel: "現在のポリシー",
      sources: {
        runtime: "今回の実行で変更済み",
        env: "起動設定で指定",
        default: "システム既定値を使用",
      },
      profileTitle: "外部送信の安全レベル",
      profileDescription:
        "端末外へ送る前の確認強度を設定します。認証情報と秘密情報はすべてのレベルで遮断されます。",
      profileDocLabel: "ルールの詳細を見る",
      profiles: {
        strict: {
          label: "厳格",
          description: "公開、共有端末、複数人での利用に適しています。",
        },
        normal: {
          label: "標準",
          description: "日常の個人利用向け。明確な危険を防ぎます。",
        },
        lax: {
          label: "緩和",
          description:
            "信頼できる内部環境向け。認証情報と秘密情報を強制遮断します。",
        },
      },
      active: "現在",
      judgeTitle: "意味リスク審査",
      judgeDescription:
        "送信前に追加確認を行い、秘密情報の誘導や不正な収集を検出します。少し待ち時間とモデル利用量が増えます。",
      judgeUnavailable: "現在、利用できるモデルがありません。",
      judgeSwitchLabel: "意味リスク審査を有効にする",
      advancedTitle: "高度な診断",
      advancedSummary: "基盤モデルの識別情報を一時表示",
      advancedDescription: "開発・調査向けです。通常の利用では変更不要です。",
      advancedEnvironment: "起動時：ECHO_IDENTITY_LOCK=0 を設定。",
      advancedTurn: "1 メッセージのみ：先頭に /raw を入力。",
      advancedApi: "API：リクエストコンテキストに raw_identity: true を設定。",
      loading: "現在の設定を読み込み中…",
      loadFailed:
        "設定を読み込めませんでした。既存の設定は変更されていません。",
      retry: "再読み込み",
      restoreFailed: "操作に失敗しました。もう一度お試しください。",
      removePathTitle: "保護パスを削除",
      removePathDescription: (path) =>
        `「${path}」を削除すると、Agent はこのパスへのアクセスを自動拒否しなくなります。`,
      removePathConfirm: "削除する",
    },
    mcp: {
      title: "MCP サービス",
      description:
        "信頼後に Echo が利用できる外部ツールサービスを接続します。",
      loading: "MCP サービスを読み込み中…",
      loadFailed:
        "MCP サービスを読み込めませんでした。既存設定は変更されていません。",
      retry: "再読み込み",
      trustLoadFailed:
        "信頼状態を取得できないため、関連操作を一時停止しています。",
      trustUnknown: "信頼状態不明",
      noServers:
        "MCP サービスは未接続です。下からリモートサービスを追加できます。",
      nameLabel: "サービス名",
      urlLabel: "サービス URL",
      tokenLabel: "アクセストークン（任意）",
      tokenHint: "トークンは接続のみに使用され、一覧には表示されません。",
      add: "サービスを追加",
      adding: "追加中…",
      invalidUrl:
        "http:// または https:// で始まる有効な URL を入力してください。",
      duplicateName: (name) => `「${name}」というサービスは既に存在します。`,
      toggleLabel: (name) => `${name} を有効または無効にする`,
      trustLabel: (name) => `${name} を信頼`,
      revokeLabel: (name) => `${name} の信頼を解除`,
      activationFailed: (name, detail) =>
        `${name} を起動できませんでした${detail ? `：${detail}` : "。設定を確認してください。"}`,
      runtimeError: (detail) => `実行エラー：${detail}`,
      removeLabel: (name) => `${name} を削除`,
      removeTitle: "MCP サービスを削除",
      removeDescription: (name) =>
        `「${name}」の接続設定、信頼、保存済み OAuth 認証を削除します。リモートサービス自体は削除されません。`,
      removeConfirm: "サービスを削除",
      removeSuccess: (name) => `MCP サービス ${name} を削除しました`,
      removeFailed:
        "MCP サービスを削除できませんでした。現在の状態を再読み込みしました。",
    },
  },
  "ko-KR": {
    observability: {
      title: "실행 관측성",
      description:
        "작업 실행, 도구 호출과 실행 상태를 한곳에서 확인합니다. 전체 데이터는 전용 작업 공간에서 엽니다.",
      activityTitle: "실시간 활동",
      activityDescription: "진행 중인 작업, 단계와 이벤트를 추적합니다.",
      tracesTitle: "도구 및 파일 기록",
      tracesDescription: "도구 호출, 파일 변경과 결과를 확인합니다.",
      healthTitle: "실행 상태",
      healthDescription: "실패, 지연과 비정상 상태를 찾습니다.",
      openDashboard: "관측성 작업 공간 열기",
    },
    privacy: {
      identityTitle: "제품 정체성 보호",
      identityDescription:
        "기반 모델이나 공급자 이름 대신 Echo 제품 정체성으로 응답합니다. 모델 경로를 점검할 때만 잠시 끄세요.",
      identityOn: "켜짐",
      identityOff: "꺼짐",
      enableIdentity: "제품 정체성 보호 켜기",
      disableIdentity: "제품 정체성 보호 끄기",
      sourceLabel: "현재 정책",
      sources: {
        runtime: "이번 실행에서 변경됨",
        env: "시작 설정에서 지정",
        default: "시스템 기본값 사용",
      },
      profileTitle: "외부 전송 안전 수준",
      profileDescription:
        "기기 밖으로 보내기 전의 검사 강도를 정합니다. 자격 증명과 비밀 정보는 모든 수준에서 차단됩니다.",
      profileDocLabel: "규칙 자세히 보기",
      profiles: {
        strict: {
          label: "엄격",
          description: "외부 공개, 공유 기기, 다중 사용자 환경에 적합합니다.",
        },
        normal: {
          label: "균형",
          description:
            "일상적인 개인 사용에 적합하며 명확한 위험을 차단합니다.",
        },
        lax: {
          label: "완화",
          description:
            "신뢰하는 내부 환경에 적합하며 자격 증명과 비밀만 강제 차단합니다.",
        },
      },
      active: "현재",
      judgeTitle: "의미 위험 검사",
      judgeDescription:
        "전송 전에 한 번 더 검사해 비밀 유도나 무단 수집을 찾습니다. 약간의 대기 시간과 모델 사용량이 늘어납니다.",
      judgeUnavailable: "현재 사용할 수 있는 모델이 없습니다.",
      judgeSwitchLabel: "의미 위험 검사 사용",
      advancedTitle: "고급 진단",
      advancedSummary: "기반 모델 정체성을 일시적으로 표시",
      advancedDescription:
        "개발 및 문제 해결용입니다. 일반 사용에서는 변경할 필요가 없습니다.",
      advancedEnvironment: "시작 시: ECHO_IDENTITY_LOCK=0 설정.",
      advancedTurn: "메시지 한 번만: 메시지를 /raw 로 시작.",
      advancedApi: "API: 요청 컨텍스트에 raw_identity: true 설정.",
      loading: "현재 설정을 불러오는 중…",
      loadFailed:
        "설정을 불러오지 못했습니다. 기존 설정은 변경되지 않았습니다.",
      retry: "다시 불러오기",
      restoreFailed: "작업에 실패했습니다. 다시 시도하세요.",
      removePathTitle: "보호 경로 삭제",
      removePathDescription: (path) =>
        `“${path}”을(를) 삭제하면 Agent가 이 경로의 접근을 자동으로 차단하지 않습니다.`,
      removePathConfirm: "경로 삭제",
    },
    mcp: {
      title: "MCP 서비스",
      description: "신뢰한 뒤 Echo가 사용할 외부 도구 서비스를 연결합니다.",
      loading: "MCP 서비스를 불러오는 중…",
      loadFailed:
        "MCP 서비스를 불러오지 못했습니다. 기존 설정은 변경되지 않았습니다.",
      retry: "다시 불러오기",
      trustLoadFailed: "신뢰 상태를 읽지 못해 관련 작업을 일시 중지했습니다.",
      trustUnknown: "신뢰 상태 알 수 없음",
      noServers:
        "연결된 MCP 서비스가 없습니다. 아래에서 원격 서비스를 추가할 수 있습니다.",
      nameLabel: "서비스 이름",
      urlLabel: "서비스 URL",
      tokenLabel: "액세스 토큰(선택)",
      tokenHint: "토큰은 연결에만 사용되며 목록에 표시되지 않습니다.",
      add: "서비스 추가",
      adding: "추가 중…",
      invalidUrl: "http:// 또는 https:// 로 시작하는 유효한 주소를 입력하세요.",
      duplicateName: (name) => `“${name}” 서비스가 이미 있습니다.`,
      toggleLabel: (name) => `${name} 사용 또는 중지`,
      trustLabel: (name) => `${name} 신뢰`,
      revokeLabel: (name) => `${name} 신뢰 취소`,
      activationFailed: (name, detail) =>
        `${name} 시작 실패${detail ? `: ${detail}` : ". 서비스 설정을 확인하세요."}`,
      runtimeError: (detail) => `실행 오류: ${detail}`,
      removeLabel: (name) => `${name} 제거`,
      removeTitle: "MCP 서비스 제거",
      removeDescription: (name) =>
        `“${name}” 연결 설정과 신뢰, 저장된 OAuth 인증을 제거합니다. 원격 서비스 자체는 삭제하지 않습니다.`,
      removeConfirm: "서비스 제거",
      removeSuccess: (name) => `MCP 서비스 ${name} 제거됨`,
      removeFailed:
        "MCP 서비스를 제거하지 못했습니다. 현재 상태를 다시 불러왔습니다.",
    },
  },
};

export function getSettingsUxCopy(locale: Locale): SettingsUxCopy {
  return COPY[locale] ?? COPY["en-US"];
}
