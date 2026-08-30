# Skill Finder — Usage Examples

## Example 1: Local Catalog Match (Fastest Path)

**User:** "有没有能帮我做关税查询的skill？"

**Agent action:** Always search the local skill catalog first.

**Step 1.5: Search Local Skill Catalog**
```bash
cat ~/.echo/accounts/ACCOUNT_ID/skills/remote_skills_cache.json | \
  python3 -c "
import json, sys
query = 'tariff hs code 关税'.lower().split()
data = json.load(sys.stdin)
for s in data['skills']:
    text = (s['name'] + ' ' + s.get('description', '')).lower()
    if any(q in text for q in query):
        print(f\"  {s['name']}\")
        print(f\"    {s.get('description', 'No description')[:150]}\")
        print()
"
```

**Result:**
```
  tariff-search
    Tariff calculation and HS code classification tool via TurtleClassify API. **When to use**: user asks for tariff rates, HS codes, duty calculations...
```

**Check install status:**
```bash
ls ~/.echo/accounts/ACCOUNT_ID/skills/tariff-search/SKILL.md 2>/dev/null && echo "installed" || echo "not installed"
# Result: not installed
```

**Agent response:**

> Found **tariff-search** in the local skill catalog — it handles tariff calculation and HS code classification via the TurtleClassify API. It's not currently installed. Want me to install it for you?

**User:** "装上吧"

**Install from OSS:**
```bash
OSS_URL=$(cat ~/.echo/accounts/ACCOUNT_ID/skills/remote_skills_cache.json | \
  python3 -c "import json,sys; data=json.load(sys.stdin); [print(s['oss']) for s in data['skills'] if s['name']=='tariff-search']")
curl -sL "$OSS_URL" -o /tmp/skill.zip && \
  unzip -o /tmp/skill.zip -d ~/.echo/accounts/ACCOUNT_ID/skills/tariff-search/ && \
  rm /tmp/skill.zip
```

> Installed **tariff-search**. You can now ask me to look up tariff rates and HS codes.

---

## Example 2: Local Catalog — Skill Already Installed

**User:** "find a skill for Gmail"

**Agent action:** Search the local skill catalog first.

**Step 1.5:** Search cache for "gmail email"
```
  gmail-assistant
    Send, search, and manage Gmail messages with automatic delivery verification and smart draft workflow
```

**Check:** gmail-assistant is already listed in `<available_skills>` in the system prompt.

**Agent response:**

> **gmail-assistant** is already installed and enabled. It can send, search, and manage Gmail messages. Want me to help you with something specific?

No need to search external platforms.

---

## Example 3: Comprehensive Search for Niche Skill (No Local Match)

**User:** "帮我找找能每日追踪agent领域前沿论文的skill"

**Agent action:** Search the local skill catalog first → no match → escalate to Strategy B (multi-platform).

**Step 1.5: Search Local Skill Catalog**
```bash
# Search for: arxiv, research, papers, agent, 论文
# Result: 0 matches — no local skill for academic paper tracking
```

**Step 2: Search external platforms (Strategy B)**

**Search skills.sh:**
```bash
npx skills find "agent research papers"
# Result: 1 skill found
```

**Step 2: Search ClawHub**
```bash
clawhub search "research papers"
# Result: 0 skills found
```

**Step 3: Search SkillsMP**
```bash
SKILLSMP_API_KEY=$(grep SKILLSMP_API_KEY .env | cut -d '=' -f2 | tr -d '"')
curl -s "https://skillsmp.com/api/v1/skills/ai-search?q=arxiv+agent+research+papers+tracking&limit=10" \
  -H "Authorization: Bearer $SKILLSMP_API_KEY" -H "Accept: application/json" \
  | jq -r '.data.data[] | "\(.skill.name) by \(.skill.author) ⭐\(.skill.stars)\n  \(.skill.description[0:80])...\n  \(.skill.githubUrl)\n"'
# Result: 8 skills found
```

**Aggregated Results:**

```
Found 9 skills across 3 platforms:

From SkillsMP (8 results — best matches):
1. academic-search by HyunjunJeon ⭐44
   Multi-source: arXiv + Semantic Scholar
   https://github.com/HyunjunJeon/...

2. arxiv-search by DeevsDeevs ⭐29
   Search arXiv preprints for AI/ML papers
   https://github.com/DeevsDeevs/agent-system/...

3. arxiv-search by langchain-ai ⭐9582
   Official LangChain implementation
   https://github.com/langchain-ai/deepagents/...

From skills.sh (1 result):
4. research-papers by vercel-labs
   General paper discovery
   https://skills.sh/vercel-labs/agent-skills/research-papers

From ClawHub (0 results)
```

**Agent installs the best match directly:**

```bash
cd ~/.echo/accounts/{accountId}/agents/{agentId}/agent-core/skills/
git clone --depth 1 https://github.com/HyunjunJeon/... academic-search
```

> Installed **academic-search** — it searches multiple academic sources (arXiv + Semantic Scholar) and has active community support (44 stars).

---

## Example 4: Common Skill — External Search Fallback

**User:** "find a react testing skill"

**Agent action:** Search the local skill catalog first → no match → Strategy A (fast).

**Step 1.5:** Search cache for "react testing" → 0 matches (no local skill for react testing).

**Step 2:** External search:
```bash
npx skills find "react testing"
```

```
Install with npx skills add <owner/repo@skill>

vercel-labs/agent-skills@react-testing-library
└ https://skills.sh/vercel-labs/agent-skills/react-testing-library
```

Good match found. Install directly:

```bash
cd ~/.echo/accounts/{accountId}/agents/{agentId}/agent-core/skills/
npx skills add vercel-labs/agent-skills --skill react-testing-library -y
mv .agents/skills/react-testing-library ./
rm -rf .agents
```

> Installed **react-testing-library** — official React Testing Library patterns from vercel-labs.

No need to search other platforms for this common query.

---

## Example 5: No Results — Retry and Fallback

**User:** "find skill for quantum entanglement simulation"

**Agent action:** Strategy A → no results → Strategy C (retry with alternatives).

**Attempt 1:** `npx skills find "quantum entanglement simulation"`
- skills.sh: 0 results → escalate to Strategy B

**Attempt 1 (multi-platform):**
- skills.sh: 0 results
- ClawHub: 0 results
- SkillsMP: 0 results

**Attempt 2 (broader terms):** "quantum computing"
- skills.sh: 0 results
- ClawHub: 0 results
- SkillsMP: 2 results (general quantum, not entanglement-specific)

**Attempt 3 (related domain):** "physics simulation"
- skills.sh: 1 result (general scientific computing)
- SkillsMP: 3 results (computational physics)

**Agent response after 3 attempts:**

> I searched skills.sh, ClawHub, and SkillsMP with "quantum entanglement simulation", "quantum computing", and "physics simulation" but found no exact match.
>
> Here's what I can do instead:
> 1. Help with this task directly using my general capabilities
> 2. Create a custom skill for quantum simulation: `npx skills init quantum-simulation`
> 3. Build workflows around existing Python quantum libraries (Qiskit, Cirq)
