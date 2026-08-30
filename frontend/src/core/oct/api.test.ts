import { describe, expect, it } from "vitest";

import { octErrorMessage } from "./api";

describe("octErrorMessage", () => {
  it("extracts the useful detail from the gateway wrapper", () => {
    expect(
      octErrorMessage(
        new Error(
          'oct 登录失败: gateway rejected: {"detail":"验证码错误或已过期"}',
        ),
        "登录失败",
      ),
    ).toBe("验证码错误或已过期");
  });

  it("preserves already human-readable errors", () => {
    expect(octErrorMessage(new Error("邮件服务暂时不可用"), "发送失败")).toBe(
      "邮件服务暂时不可用",
    );
  });

  it("falls back for empty or HTML proxy failures", () => {
    expect(octErrorMessage(null, "登录失败")).toBe("登录失败");
    expect(
      octErrorMessage(
        new Error("<html><body>Bad Gateway</body></html>"),
        "登录失败",
      ),
    ).toBe("登录失败");
  });
});
