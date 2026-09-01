import type { AgentWorldAgent } from "@/core/agents/types";

export interface SmartTeamPlugin {
  id: string;
  label: string;
  reason: string;
}

export interface SmartTeamPlan {
  members: AgentWorldAgent[];
  plugins: SmartTeamPlugin[];
  mode: "cluster" | "swarm";
}

const DOMAINS = [
  {
    words: ["股票", "投资", "财报", "估值", "证券", "基金", "量化", "finance"],
    agent: ["investment", "financial", "market", "equity", "投研", "金融", "估值"],
    plugins: [
      ["finance", "行情与财务数据", "查询行情、财报和估值数据"],
      ["web-search", "联网搜索", "核验公告、新闻和公开资料"],
      ["spreadsheet", "电子表格", "建立估值、情景和风险模型"],
    ],
  },
  {
    words: ["电商", "亚马逊", "tiktok shop", "shopify", "跨境", "选品", "amazon"],
    agent: ["ecommerce", "commerce", "amazon", "shopify", "供应链", "选品", "跨境"],
    plugins: [
      ["browser", "浏览器自动化", "采集商品、竞品与渠道数据"],
      ["spreadsheet", "电子表格", "测算毛利、库存与广告回报"],
      ["web-search", "联网搜索", "研究市场、法规和竞争格局"],
    ],
  },
  {
    words: ["漫剧", "短剧", "视频", "分镜", "剧本", "角色", "动画", "内容"],
    agent: ["creative", "story", "video", "narrative", "漫剧", "分镜", "内容", "创意"],
    plugins: [
      ["image", "图像生成", "制作角色、场景与关键帧"],
      ["video", "视频制作", "生成镜头并完成剪辑合成"],
      ["documents", "文档", "管理剧本、分镜表和制作清单"],
    ],
  },
  {
    words: ["论文", "学术", "科研", "实验", "文献", "引用", "研究"],
    agent: ["academic", "research", "science", "文献", "科研", "实验", "研究"],
    plugins: [
      ["web-search", "学术搜索", "检索论文、数据集和原始来源"],
      ["documents", "文档", "撰写综述、方法和论文草稿"],
      ["spreadsheet", "数据分析", "整理实验数据与统计结果"],
    ],
  },
  {
    words: ["代码", "开发", "应用", "系统", "bug", "接口", "部署", "agent"],
    agent: ["coder", "engineer", "developer", "software", "代码", "工程", "开发"],
    plugins: [
      ["filesystem", "项目文件", "读取和修改项目代码"],
      ["terminal", "终端", "运行测试、构建和部署命令"],
      ["browser", "浏览器测试", "验证前端与线上行为"],
    ],
  },
  {
    words: ["办公", "报表", "会议", "邮件", "流程", "erp", "crm", "自动化"],
    agent: ["assistant", "automation", "office", "rpa", "办公", "流程", "自动化"],
    plugins: [
      ["documents", "文档", "处理方案、报告与会议材料"],
      ["spreadsheet", "电子表格", "处理台账、数据和报表"],
      ["desktop", "桌面自动化", "执行跨应用重复流程"],
    ],
  },
] as const;

function searchable(agent: AgentWorldAgent): string {
  return [
    agent.id,
    agent.name,
    agent.display_name,
    agent.description,
    agent.category,
    ...(agent.tags ?? []),
    ...(agent.tool_groups ?? []),
    ...(agent.extra_affinity ?? []),
    ...(agent.private_skills ?? []),
  ]
    .join(" ")
    .toLowerCase();
}

export function buildSmartTeamPlan(
  task: string,
  agents: AgentWorldAgent[],
  limit = 4,
): SmartTeamPlan {
  const query = task.trim().toLowerCase();
  const matchedDomains = DOMAINS.filter((domain) =>
    domain.words.some((word) => query.includes(word)),
  );
  const scored = agents
    .map((agent) => {
      const text = searchable(agent);
      let score = 0;
      for (const domain of matchedDomains) {
        score += domain.agent.filter((word) => text.includes(word)).length * 12;
      }
      for (const token of query.split(/[\s,，。；;、/]+/).filter((v) => v.length > 1)) {
        if (text.includes(token)) score += 5;
      }
      if (agent.is_installed) score += 2;
      if (agent.is_official) score += 1;
      score += Math.min(2, Math.max(0, agent.rating ?? 0) / 2.5);
      return { agent, score };
    })
    .filter((entry) => entry.score > 2)
    .sort((a, b) => b.score - a.score || b.agent.rating - a.agent.rating);

  const members = scored.slice(0, Math.max(1, limit)).map((entry) => entry.agent);
  if (members.length === 0) {
    members.push(
      ...agents
        .filter((agent) => agent.is_installed)
        .sort((a, b) => b.rating - a.rating)
        .slice(0, Math.max(1, limit)),
    );
  }

  const plugins = new Map<string, SmartTeamPlugin>();
  for (const domain of matchedDomains) {
    for (const [id, label, reason] of domain.plugins) {
      plugins.set(id, { id, label, reason });
    }
  }

  return {
    members,
    plugins: Array.from(plugins.values()).slice(0, 6),
    mode: members.length >= 4 ? "swarm" : "cluster",
  };
}

