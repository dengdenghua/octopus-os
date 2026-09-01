import { Link } from "react-router-dom";
import { ArrowLeftIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

const sections = [
  {
    title: "我们处理的数据",
    body: "EchoAI 可能处理你的登录标识、对话内容、任务上下文、上传文件、操作记录、浏览器会话状态、工作区配置和错误日志。实际范围取决于你启用的功能和部署方式。",
  },
  {
    title: "处理目的",
    body: "这些数据用于完成你发起的任务、保持会话连续性、改进工作流体验、排查故障、展示运行记录，并在你授权时连接外部工具或第三方服务。",
  },
  {
    title: "本地与第三方服务",
    body: "部分能力会在本地运行，部分能力可能调用模型、插件、搜索、浏览器、文件系统或企业服务。涉及外部提交、上传、删除或权限变更时，产品应在关键动作前给出明确提示。",
  },
  {
    title: "保留与删除",
    body: "会话、任务、知识库和日志的保留周期由当前部署配置决定。你可以在设置、工作区管理或相应数据页面中删除可管理的数据；企业部署可由管理员配置保留策略。",
  },
  {
    title: "安全措施",
    body: "EchoAI 与 EchoOS 会通过权限模式、工具确认、访问边界和运行日志降低误操作风险。你仍应避免在未确认目标的情况下输入密钥、验证码、身份证件、支付信息或其他敏感内容。",
  },
];

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-muted px-4 py-8 text-foreground">
      <div className="mx-auto max-w-3xl">
        <Button asChild variant="ghost" className="mb-4 px-0">
          <Link to="/login">
            <ArrowLeftIcon className="size-4" />
            返回登录
          </Link>
        </Button>

        <article className="bg-card text-card-foreground rounded-2xl border border-border-default px-6 py-7 shadow-[var(--shadow-card)]">
          <p className="text-xs font-medium uppercase text-muted-foreground">
            EchoAI · Powered by EchoOS
          </p>
          <h1 className="mt-2 text-2xl font-semibold">隐私政策</h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            本政策说明 EchoAI
            在产品运行中可能处理哪些数据，以及这些数据如何用于完成你的任务。
          </p>

          <div className="mt-6 space-y-5">
            {sections.map((section) => (
              <section key={section.title}>
                <h2 className="text-base font-semibold">{section.title}</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {section.body}
                </p>
              </section>
            ))}
          </div>

          <p className="mt-6 border-t border-border pt-4 text-xs leading-5 text-muted-foreground">
            本页面为产品内公开说明，后续若接入正式法务文本，可在此路由替换为完整版本。
          </p>
        </article>
      </div>
    </main>
  );
}
