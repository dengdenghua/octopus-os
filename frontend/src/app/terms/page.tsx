import { Link } from "react-router-dom";
import { ArrowLeftIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

const sections = [
  {
    title: "服务范围",
    body: "EchoAI 提供本地与云端协同的智能角色平台，用于对话、自动化、知识管理、团队协作和可复用技能编排。底层能力由 EchoOS 提供，具体可用范围会随账号权限、部署环境和已启用的插件而变化。",
  },
  {
    title: "账号与授权",
    body: "你需要确保登录信息真实、有效，并妥善保管验证码、令牌和本地凭据。你主动授权的工具、文件、浏览器会话或第三方服务，只会用于完成你发起的任务。",
  },
  {
    title: "使用边界",
    body: "请不要使用 EchoAI 进行违法、侵权、绕过访问控制、批量骚扰、恶意爬取或破坏系统稳定性的行为。涉及敏感数据、外部提交、删除、购买或权限变更时，应在执行前确认目标和影响。",
  },
  {
    title: "输出与责任",
    body: "智能体输出可能包含不完整或过时的信息。对于法律、医疗、金融、安全等高风险决策，你应结合专业意见和实际系统状态进行复核。",
  },
  {
    title: "变更与中止",
    body: "我们可能根据产品演进调整功能、接口或访问方式。若发现异常使用、风险行为或违反本协议的情况，可能限制相关功能或暂停服务。",
  },
];

export default function TermsPage() {
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
          <h1 className="mt-2 text-2xl font-semibold">用户协议</h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            欢迎使用
            EchoAI。以下条款用于说明你与服务之间的基本权利、责任和使用边界。
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
