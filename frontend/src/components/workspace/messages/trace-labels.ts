const TRACE_LABEL_PREFIX =
  /^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:Thought|Action|Observation|Final Answer|思考|思考中|想法|执行|执行中|行动|观察|最终答案)\s*[:：]\s*/i;

// Backend tool-execution envelopes that must never reach the user verbatim.
// The success/failure wrappers are runner bookkeeping; the trailing retry
// instruction (请在下一轮 Thought…) is coaching aimed at the MODEL, not the human.
const TOOL_RETRY_INSTRUCTION = /\s*请在下一轮\s*Thought[\s\S]*$/;
const TOOL_SUCCESS_ENVELOPE =
  /^\s*\(real tool execution succeeded\)\s*([a-z_][a-z0-9_]*)?\s*/i;
const TOOL_FAILURE_ENVELOPE = /^\s*\((?:工具失败|tool failed)\)\s*/i;

/** Strip the runner's tool-execution envelope + leaked retry coaching so the
 *  work-log shows clean tool I/O instead of `(real tool execution succeeded)
 *  grep_text {…}` / `(工具失败) status=failed … 请在下一轮 Thought …`. */
export function stripToolEnvelope(value?: string | null): string {
  let text = (value ?? "").replace(TOOL_RETRY_INSTRUCTION, "").trim();
  const failed =
    TOOL_FAILURE_ENVELOPE.test(text) || /^\s*status=failed\b/i.test(text);
  text = text
    .replace(TOOL_SUCCESS_ENVELOPE, "")
    .replace(TOOL_FAILURE_ENVELOPE, "")
    .replace(/^\s*status=failed\b\s*/i, "")
    .trim();
  if (failed) {
    const errMatch = text.match(/error=\s*(.+)$/is);
    const detail = (errMatch?.[1] ?? text).trim();
    text = detail ? `失败：${detail}` : "失败";
  }
  return text.trim();
}

export function stripTraceLabelPrefixes(value?: string | null): string {
  const withoutLabels = (value ?? "")
    .split(/\r?\n/)
    .map((line) => {
      let next = line;
      while (TRACE_LABEL_PREFIX.test(next)) {
        next = next.replace(TRACE_LABEL_PREFIX, "");
      }
      return next.trimEnd();
    })
    .join("\n")
    .trim();
  return stripToolEnvelope(withoutLabels);
}
